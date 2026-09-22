#include <jni.h>
#include <android/log.h>
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstring>
#include <random>
#include <string>
#include <vector>
#include "llama.h"

#define LOG_TAG "llama_bridge"
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

namespace {
constexpr char FIELD_SEP='\x1F';
constexpr char RECORD_SEP='\x1E';

struct Turn { std::string role; std::string text; };

struct EngineHandle {
    llama_model * model = nullptr;
    llama_context * ctx = nullptr;
    const llama_vocab * vocab = nullptr;
    std::mt19937 rng{std::random_device{}()};
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

// IMPORTANT: use the template stored in tokenizer.chat_template inside the
// selected GGUF. Passing nullptr to llama_chat_apply_template is only correct
// when the library version/model path resolves the model metadata itself; this
// bridge explicitly obtains the model's template to avoid accidentally using
// ChatML for DeepSeek-Coder.
std::string templ(const EngineHandle * handle, const std::vector<Turn> & turns) {
    if (!handle || !handle->model || turns.empty()) return {};

    std::vector<llama_chat_message> messages;
    messages.reserve(turns.size());
    for (const auto & t : turns) {
        messages.push_back({t.role.c_str(), t.text.c_str()});
    }

    const char * model_tmpl = llama_model_chat_template(handle->model, nullptr);
    if (!model_tmpl || !*model_tmpl) {
        LOGE("GGUF has no tokenizer.chat_template; using DeepSeek template fallback");
        // This is the canonical llama.cpp DeepSeek-Coder template selector in
        // the v0.2.x implementation. It is only used when GGUF metadata lacks
        // a usable chat template.
        model_tmpl = "deepseek";
    }

    std::vector<char> buffer(8192);
    int n = llama_chat_apply_template(
        model_tmpl,
        messages.data(),
        messages.size(),
        true,
        buffer.data(),
        static_cast<int32_t>(buffer.size()));

    if (n < 0) {
        LOGE("chat template failed; falling back to explicit plain prompt");
        return {};
    }

    if (n >= static_cast<int>(buffer.size())) {
        buffer.resize(static_cast<size_t>(n) + 1);
        n = llama_chat_apply_template(
            model_tmpl,
            messages.data(),
            messages.size(),
            true,
            buffer.data(),
            static_cast<int32_t>(buffer.size()));
    }

    if (n <= 0) return {};

    std::string prompt(buffer.data(), static_cast<size_t>(n));
    LOGE("chat template selected; prompt bytes=%d", n);
    return prompt;
}

llama_token sample(
        const float * logits,
        int n,
        int k,
        float temp,
        std::mt19937 & rng) {
    k = std::max(1, std::min(k, n));
    std::vector<int> ix(n);
    for (int i = 0; i < n; ++i) ix[i] = i;

    std::partial_sort(ix.begin(), ix.begin() + k, ix.end(),
        [&](int a, int b) { return logits[a] > logits[b]; });

    if (temp <= 0.01f) return ix[0];

    std::vector<double> probs(k);
    double max_logit = logits[ix[0]] / temp;
    double sum = 0.0;
    for (int i = 0; i < k; ++i) {
        probs[i] = std::exp(logits[ix[i]] / temp - max_logit);
        sum += probs[i];
    }

    double x = std::uniform_real_distribution<double>(0.0, sum)(rng);
    for (int i = 0; i < k; ++i) {
        x -= probs[i];
        if (x <= 0.0) return ix[i];
    }
    return ix[0];
}

bool has_stop_suffix(const std::string & text) {
    static const char * stops[] = {
        "<|EOT|>",
        "<|eot_id|>",
        "<|im_end|>",
        "<|end_of_turn|>",
        "<|endoftext|>"
    };
    for (const char * stop : stops) {
        if (text.size() >= std::strlen(stop) &&
            text.compare(text.size() - std::strlen(stop), std::strlen(stop), stop) == 0) {
            return true;
        }
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
    cp.n_ctx = std::max(256, static_cast<int>(nc));
    cp.n_batch = cp.n_ctx;
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

    const char * tmpl = llama_model_chat_template(model, nullptr);
    LOGE("model chat template present=%s", (tmpl && *tmpl) ? "yes" : "no");
    return reinterpret_cast<jlong>(handle);
}

JNIEXPORT jstring JNICALL
Java_com_musab_aragpt2_LlamaEngine_nativeGenerate(
        JNIEnv * env, jobject, jlong hp, jstring jt,
        jint max_tokens, jfloat temp, jint topk) {
    auto * handle = reinterpret_cast<EngineHandle *>(hp);
    if (!handle || !handle->ctx) {
        fail(env, "المحرك غير محمل");
        return nullptr;
    }

    auto turns = parse(js(env, jt));
    std::string prompt = templ(handle, turns);
    if (prompt.empty()) prompt = fallback(turns);

    int n_tokens = -llama_tokenize(
        handle->vocab, prompt.c_str(), static_cast<int>(prompt.size()),
        nullptr, 0, true, true);
    if (n_tokens <= 0) {
        fail(env, "فشل ترميز النص");
        return nullptr;
    }

    LOGE("prompt tokens=%d", n_tokens);
    std::vector<llama_token> tokens(static_cast<size_t>(n_tokens));
    if (llama_tokenize(handle->vocab, prompt.c_str(), static_cast<int>(prompt.size()),
                       tokens.data(), n_tokens, true, true) < 0) {
        fail(env, "فشل ترميز النص");
        return nullptr;
    }

    llama_memory_clear(llama_get_memory(handle->ctx), true);
    handle->cancel = false;

    auto batch = llama_batch_get_one(tokens.data(), n_tokens);
    if (llama_decode(handle->ctx, batch) != 0) {
        fail(env, "فشل تشغيل النموذج");
        return nullptr;
    }

    std::string output;
    int n = 0;
    int vocab_size = llama_vocab_n_tokens(handle->vocab);

    while (n < max_tokens && !handle->cancel.load()) {
        llama_token token = sample(
            llama_get_logits_ith(handle->ctx, -1), vocab_size,
            topk, temp, handle->rng);

        if (llama_vocab_is_eog(handle->vocab, token)) break;

        char piece[256];
        int z = llama_token_to_piece(
            handle->vocab, token, piece, sizeof(piece), 0, false);
        if (z > 0) output.append(piece, z);

        if (has_stop_suffix(output)) {
            strip_special_suffix(output);
            break;
        }

        llama_token one[1] = {token};
        auto next_batch = llama_batch_get_one(one, 1);
        if (llama_decode(handle->ctx, next_batch) != 0) break;
        ++n;
    }

    strip_special_suffix(output);
    LOGE("generated tokens=%d output_bytes=%zu", n, output.size());
    return env->NewStringUTF(output.c_str());
}

JNIEXPORT void JNICALL
Java_com_musab_aragpt2_LlamaEngine_nativeCancel(JNIEnv *, jobject, jlong hp) {
    auto * handle = reinterpret_cast<EngineHandle *>(hp);
    if (handle) handle->cancel = true;
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
