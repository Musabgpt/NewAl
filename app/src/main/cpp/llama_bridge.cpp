#include <jni.h>
#include <android/log.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstring>
#include <string>
#include <vector>
#include "llama.h"

#define LOG_TAG "llama_bridge"
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

namespace {
constexpr char FIELD_SEP='\x1F';
constexpr char RECORD_SEP='\x1E';
constexpr char RESULT_SEP='\x1D';

struct Turn { std::string role; std::string text; };

struct EngineHandle {
    llama_model * model = nullptr;
    llama_context * ctx = nullptr;
    const llama_vocab * vocab = nullptr;
    std::vector<llama_token> cached_prompt_tokens;
    std::atomic<bool> cancel{false};
};

bool initialized = false;

void init() {
    if (!initialized) {
        llama_backend_init();
        initialized = true;
    }
}

void fail(JNIEnv * env, const std::string & message) {
    jclass c = env->FindClass("java/lang/RuntimeException");
    if (c) env->ThrowNew(c, message.c_str());
}

std::string js(JNIEnv * env, jstring s) {
    if (!s) return {};
    const char * p = env->GetStringUTFChars(s, nullptr);
    std::string r(p ? p : "");
    env->ReleaseStringUTFChars(s, p);
    return r;
}

std::vector<Turn> parse(const std::string & x) {
    std::vector<Turn> v;
    size_t p = 0;
    while (p <= x.size()) {
        size_t q = x.find(RECORD_SEP, p);
        std::string r = q == std::string::npos ? x.substr(p) : x.substr(p, q - p);
        if (!r.empty()) {
            size_t s = r.find(FIELD_SEP);
            if (s != std::string::npos) v.push_back({r.substr(0, s), r.substr(s + 1)});
        }
        if (q == std::string::npos) break;
        p = q + 1;
    }
    return v;
}

std::string fallback(const std::vector<Turn> & v) {
    std::string o;
    for (const auto & t : v) {
        o += (t.role == "user" ? "User: " : "Assistant: ");
        o += t.text;
        o += '\n';
    }
    o += "Assistant:";
    return o;
}

std::string templ(const EngineHandle * handle, const std::vector<Turn> & turns, bool code_mode) {
    if (!handle || !handle->model || turns.empty()) return {};

    std::vector<llama_chat_message> messages;
    messages.reserve(turns.size() + 1);
    if (code_mode) {
        static const char * system_prompt =
            "You are a precise offline programming assistant. "
            "Prioritize correct, runnable Python and explain assumptions briefly. "
            "Do not invent APIs. When code is requested, return complete code.";
        messages.push_back({"system", system_prompt});
    }
    for (const auto & t : turns) {
        messages.push_back({t.role.c_str(), t.text.c_str()});
    }

    const char * model_tmpl = llama_model_chat_template(handle->model, nullptr);
    if (!model_tmpl || !*model_tmpl) {
        LOGE("GGUF has no tokenizer.chat_template; using plain prompt fallback");
        return fallback(turns);
    }

    std::vector<char> buffer(8192);
    int n = llama_chat_apply_template(
        model_tmpl, messages.data(), messages.size(), true,
        buffer.data(), static_cast<int32_t>(buffer.size()));

    if (n < 0) {
        LOGE("chat template failed; using plain prompt fallback");
        return fallback(turns);
    }
    if (n >= static_cast<int>(buffer.size())) {
        buffer.resize(static_cast<size_t>(n) + 1);
        n = llama_chat_apply_template(
            model_tmpl, messages.data(), messages.size(), true,
            buffer.data(), static_cast<int32_t>(buffer.size()));
    }
    if (n <= 0) return fallback(turns);
    return std::string(buffer.data(), static_cast<size_t>(n));
}

int common_prefix(const std::vector<llama_token> & a, const std::vector<llama_token> & b) {
    const size_t n = std::min(a.size(), b.size());
    size_t i = 0;
    while (i < n && a[i] == b[i]) ++i;
    return static_cast<int>(i);
}

bool has_stop_suffix(const std::string & text) {
    static const char * stops[] = {
        "<|EOT|>", "<|eot_id|>", "<|im_end|>",
        "<|end_of_turn|>", "<|endoftext|>"
    };
    for (const char * stop : stops) {
        const size_t len = std::strlen(stop);
        if (text.size() >= len && text.compare(text.size() - len, len, stop) == 0) return true;
    }
    return false;
}

void strip_special_suffix(std::string & text) {
    static const char * stops[] = {
        "<|EOT|>", "<|eot_id|>", "<|im_end|>",
        "<|end_of_turn|>", "<|endoftext|>"
    };
    for (const char * stop : stops) {
        const size_t len = std::strlen(stop);
        if (text.size() >= len && text.compare(text.size() - len, len, stop) == 0) {
            text.erase(text.size() - len);
            break;
        }
    }
}

std::string result_pack(const std::string & answer, int prompt_tokens, int generated_tokens,
                       long long first_ms, double tok_per_sec) {
    return answer + RESULT_SEP + std::to_string(prompt_tokens) + RESULT_SEP
         + std::to_string(generated_tokens) + RESULT_SEP + std::to_string(first_ms)
         + RESULT_SEP + std::to_string(tok_per_sec);
}
} // namespace

extern "C" {

JNIEXPORT jlong JNICALL
Java_com_musab_aragpt2_LlamaEngine_nativeLoadModel(
        JNIEnv * env, jobject, jstring jp, jint nc, jint nt) {
    init();
    std::string path = js(env, jp);

    auto mp = llama_model_default_params();
    mp.n_gpu_layers = 0;

    llama_model * model = llama_model_load_from_file(path.c_str(), mp);
    if (!model) {
        fail(env, "تعذر تحميل ملف GGUF");
        return 0;
    }

    auto cp = llama_context_default_params();
    cp.n_ctx = std::max(512, static_cast<int>(nc));
    cp.n_batch = std::min(cp.n_ctx, 512u);
    cp.n_threads = std::max(1, static_cast<int>(nt));
    cp.n_threads_batch = cp.n_threads;

    llama_context * ctx = llama_init_from_model(model, cp);
    if (!ctx) {
        llama_model_free(model);
        fail(env, "تعذر إنشاء سياق النموذج");
        return 0;
    }

    auto * handle = new EngineHandle();
    handle->model = model;
    handle->ctx = ctx;
    handle->vocab = llama_model_get_vocab(model);

    LOGE("model loaded; context=%d threads=%d chat_template=%s",
         static_cast<int>(cp.n_ctx), static_cast<int>(cp.n_threads),
         llama_model_chat_template(model, nullptr) ? "yes" : "no");
    return reinterpret_cast<jlong>(handle);
}

JNIEXPORT jstring JNICALL
Java_com_musab_aragpt2_LlamaEngine_nativeGenerate(
        JNIEnv * env, jobject, jlong hp, jstring jt,
        jint max_tokens, jfloat temp, jint topk, jboolean code_mode) {
    auto * handle = reinterpret_cast<EngineHandle *>(hp);
    if (!handle || !handle->ctx) {
        fail(env, "المحرك غير محمل");
        return nullptr;
    }

    const auto total_start = std::chrono::steady_clock::now();
    auto turns = parse(js(env, jt));
    std::string prompt = templ(handle, turns, code_mode == JNI_TRUE);
    if (prompt.empty()) prompt = fallback(turns);

    const bool first_context = handle->cached_prompt_tokens.empty();
    const bool add_special = first_context;
    int n_tokens = -llama_tokenize(
        handle->vocab, prompt.c_str(), static_cast<int>(prompt.size()),
        nullptr, 0, add_special, true);
    if (n_tokens <= 0) {
        fail(env, "فشل ترميز النص");
        return nullptr;
    }

    std::vector<llama_token> prompt_tokens(static_cast<size_t>(n_tokens));
    if (llama_tokenize(handle->vocab, prompt.c_str(), static_cast<int>(prompt.size()),
                       prompt_tokens.data(), n_tokens, add_special, true) < 0) {
        fail(env, "فشل ترميز النص");
        return nullptr;
    }

    // Reuse the existing KV cache whenever the new prompt shares a prefix with
    // the previous prompt. The first token is the one-time BOS added on the
    // first request, so subsequent tokenization omits it.
    int prefix = 0;
    if (!handle->cached_prompt_tokens.empty()) {
        const size_t old_offset = handle->cached_prompt_tokens.size() > 0 ? 1 : 0;
        if (prompt_tokens.size() >= 0 && handle->cached_prompt_tokens.size() >= old_offset) {
            const size_t n = std::min(
                prompt_tokens.size(), handle->cached_prompt_tokens.size() - old_offset);
            while (prefix < static_cast<int>(n) &&
                   prompt_tokens[static_cast<size_t>(prefix)] ==
                       handle->cached_prompt_tokens[old_offset + static_cast<size_t>(prefix)]) {
                ++prefix;
            }
            prefix += static_cast<int>(old_offset);
        }
    }

    auto mem = llama_get_memory(handle->ctx);
    const llama_pos pos_max = llama_memory_seq_pos_max(mem, 0);
    const bool can_reuse = !handle->cached_prompt_tokens.empty() && prefix > 0 && pos_max >= prefix - 1;

    if (!can_reuse) {
        llama_memory_clear(mem, true);
        handle->cached_prompt_tokens.clear();
        prefix = 0;
        if (llama_tokenize(handle->vocab, prompt.c_str(), static_cast<int>(prompt.size()),
                           prompt_tokens.data(), n_tokens, true, true) < 0) {
            fail(env, "فشل إعادة ترميز النص");
            return nullptr;
        }
    } else {
        llama_memory_seq_rm(mem, 0, prefix, -1);
    }

    handle->cancel = false;

    std::vector<llama_token> suffix(
        prompt_tokens.begin() + std::min(prefix, static_cast<int>(prompt_tokens.size())),
        prompt_tokens.end());

    const auto prompt_start = std::chrono::steady_clock::now();
    if (!suffix.empty()) {
        auto batch = llama_batch_get_one(suffix.data(), suffix.size());
        if (llama_decode(handle->ctx, batch) != 0) {
            fail(env, "فشل تشغيل النموذج");
            return nullptr;
        }
    } else if (pos_max < 0) {
        auto batch = llama_batch_get_one(prompt_tokens.data(), prompt_tokens.size());
        if (llama_decode(handle->ctx, batch) != 0) {
            fail(env, "فشل تشغيل النموذج");
            return nullptr;
        }
    }
    const auto prompt_end = std::chrono::steady_clock::now();

    handle->cached_prompt_tokens.clear();
    handle->cached_prompt_tokens.reserve(prompt_tokens.size());
    if (prefix == 0) {
        handle->cached_prompt_tokens = prompt_tokens;
    } else {
        handle->cached_prompt_tokens = prompt_tokens;
        if (handle->cached_prompt_tokens.size() > 0) {
            // cached_prompt_tokens keeps the BOS marker when the first request
            // used one; this makes the next prefix comparison unambiguous.
            if (handle->cached_prompt_tokens[0] != prompt_tokens[0] &&
                !prompt_tokens.empty()) {
                handle->cached_prompt_tokens.insert(handle->cached_prompt_tokens.begin(), prompt_tokens[0]);
            }
        }
    }

    auto sparams = llama_sampler_chain_default_params();
    sparams.no_perf = true;
    llama_sampler * sampler = llama_sampler_chain_init(sparams);
    if (!sampler) {
        fail(env, "تعذر إنشاء sampler");
        return nullptr;
    }

    if (code_mode == JNI_TRUE || temp <= 0.01f) {
        llama_sampler_chain_add(sampler, llama_sampler_init_greedy());
    } else {
        llama_sampler_chain_add(sampler, llama_sampler_init_top_k(std::max(1, static_cast<int>(topk))));
        llama_sampler_chain_add(sampler, llama_sampler_init_min_p(0.05f, 1));
        llama_sampler_chain_add(sampler, llama_sampler_init_temp(temp));
        llama_sampler_chain_add(sampler, llama_sampler_init_dist(LLAMA_DEFAULT_SEED));
    }

    std::string output;
    int generated = 0;
    long long first_ms = -1;
    const auto gen_start = std::chrono::steady_clock::now();

    while (generated < max_tokens && !handle->cancel.load()) {
        llama_token token = llama_sampler_sample(sampler, handle->ctx, -1);
        llama_sampler_accept(sampler, token);

        if (llama_vocab_is_eog(handle->vocab, token)) break;

        char piece[512];
        int z = llama_token_to_piece(handle->vocab, token, piece, sizeof(piece), 0, false);
        if (z > 0) output.append(piece, z);
        ++generated;

        if (first_ms < 0) {
            first_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - prompt_start).count();
        }

        if (has_stop_suffix(output)) {
            strip_special_suffix(output);
            break;
        }

        llama_token one[1] = {token};
        auto next_batch = llama_batch_get_one(one, 1);
        if (llama_decode(handle->ctx, next_batch) != 0) break;
    }

    llama_sampler_free(sampler);
    strip_special_suffix(output);

    // Keep the generated continuation in the live context. The next request
    // will trim it back to the common prompt prefix before decoding its suffix.
    const auto total_end = std::chrono::steady_clock::now();
    const double gen_seconds = std::chrono::duration<double>(total_end - gen_start).count();
    const double tok_per_sec = generated > 0 && gen_seconds > 0.0
        ? static_cast<double>(generated) / gen_seconds : 0.0;

    LOGE("prompt=%d generated=%d first_ms=%lld tok_s=%.2f total_ms=%lld",
         n_tokens, generated, first_ms, tok_per_sec,
         static_cast<long long>(std::chrono::duration_cast<std::chrono::milliseconds>(
             total_end - total_start).count()));

    return env->NewStringUTF(result_pack(output, n_tokens, generated, first_ms, tok_per_sec).c_str());
}

JNIEXPORT void JNICALL
Java_com_musab_aragpt2_LlamaEngine_nativeCancel(JNIEnv *, jobject, jlong hp) {
    auto * handle = reinterpret_cast<EngineHandle *>(hp);
    if (handle) handle->cancel = true;
}

JNIEXPORT void JNICALL
Java_com_musab_aragpt2_LlamaEngine_nativeReset(JNIEnv *, jobject, jlong hp) {
    auto * handle = reinterpret_cast<EngineHandle *>(hp);
    if (!handle || !handle->ctx) return;
    llama_memory_clear(llama_get_memory(handle->ctx), true);
    handle->cached_prompt_tokens.clear();
}

JNIEXPORT void JNICALL
Java_com_musab_aragpt2_LlamaEngine_nativeFree(JNIEnv *, jobject, jlong hp) {
    auto * handle = reinterpret_cast<EngineHandle *>(hp);
    if (!handle) return;
    if (handle->ctx) llama_free(handle->ctx);
    if (handle->model) llama_model_free(handle->model);
    delete handle;
}

}
