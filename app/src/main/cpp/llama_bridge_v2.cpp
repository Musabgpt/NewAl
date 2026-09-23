#include <jni.h>
#include <android/log.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstring>
#include <string>
#include <vector>
#include <mutex>
#include "llama.h"
#include "ggml-backend.h"

#define LOG_TAG "llama_bridge"
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)

namespace {
constexpr char FIELD_SEP='\x1F';
constexpr char RECORD_SEP='\x1E';
constexpr char RESULT_SEP='\x1D';

struct Turn { std::string role; std::string text; };

struct EngineHandle {
    llama_model * model = nullptr;
    llama_context * ctx = nullptr;
    const llama_vocab * vocab = nullptr;
    int n_ctx = 0;
    std::atomic<bool> cancel{false};
};

std::once_flag backend_once;

void init_backends() {
    std::call_once(backend_once, [] {
        llama_backend_init();
        LOGI("llama backend initialized; registered backends=%zu", ggml_backend_reg_count());
        for (size_t i = 0; i < ggml_backend_reg_count(); ++i) {
            auto * reg = ggml_backend_reg_get(i);
            LOGI("backend[%zu]=%s", i, ggml_backend_reg_name(reg));
        }
    });
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
        if (t.role == "system") continue;
        o += (t.role == "user" ? "User: " : "Assistant: ");
        o += t.text;
        o += '\n';
    }
    o += "Assistant:";
    return o;
}

std::string templ(const EngineHandle * h, const std::vector<Turn> & turns, bool code_mode) {
    if (!h || !h->model || turns.empty()) return {};

    std::vector<llama_chat_message> messages;
    messages.reserve(turns.size() + 1);

    std::string system;
    if (code_mode) {
        system = "You are a precise offline programming assistant. "
                 "Prioritize correct, runnable Python and explain assumptions briefly. "
                 "Answer simple arithmetic exactly. Do not invent APIs. "
                 "When code is requested, return complete code.";
    }

    for (const auto & t : turns) {
        if (t.role == "system") {
            if (!system.empty()) system += "\n\n";
            system += t.text;
        }
    }
    if (!system.empty()) messages.push_back({"system", system.c_str()});
    for (const auto & t : turns) {
        if (t.role != "system") messages.push_back({t.role.c_str(), t.text.c_str()});
    }
    if (messages.empty()) return fallback(turns);

    const char * model_tmpl = llama_model_chat_template(h->model, nullptr);
    if (!model_tmpl || !*model_tmpl) return fallback(turns);

    std::vector<char> buffer(8192);
    int n = llama_chat_apply_template(model_tmpl, messages.data(), messages.size(), true,
                                      buffer.data(), static_cast<int32_t>(buffer.size()));
    if (n < 0) return fallback(turns);
    if (n >= static_cast<int>(buffer.size())) {
        buffer.resize(static_cast<size_t>(n) + 1);
        n = llama_chat_apply_template(model_tmpl, messages.data(), messages.size(), true,
                                      buffer.data(), static_cast<int32_t>(buffer.size()));
    }
    return n > 0 ? std::string(buffer.data(), static_cast<size_t>(n)) : fallback(turns);
}

bool tokenize(const EngineHandle * h, const std::string & prompt, std::vector<llama_token> & tokens) {
    int n = -llama_tokenize(h->vocab, prompt.c_str(), static_cast<int>(prompt.size()), nullptr, 0, true, true);
    if (n <= 0) return false;
    tokens.resize(static_cast<size_t>(n));
    int written = llama_tokenize(h->vocab, prompt.c_str(), static_cast<int>(prompt.size()), tokens.data(), n, true, true);
    return written >= 0;
}

// Drop only old non-system turns. The newest user request is never discarded wholesale.
bool fit_prompt(const EngineHandle * h, std::vector<Turn> turns, bool code_mode,
                int max_prompt_tokens, std::string & prompt, std::vector<llama_token> & tokens) {
    while (true) {
        prompt = templ(h, turns, code_mode);
        if (prompt.empty()) prompt = fallback(turns);
        if (!tokenize(h, prompt, tokens)) return false;
        if (static_cast<int>(tokens.size()) <= max_prompt_tokens) return true;

        int non_system_count = 0;
        for (const auto & t : turns) if (t.role != "system") ++non_system_count;

        // Preserve the newest/current non-system turn. Only remove history when
        // there is at least one older non-system turn to sacrifice.
        if (non_system_count > 1) {
            int remove_index = -1;
            for (int i = 0; i < static_cast<int>(turns.size()); ++i) {
                if (turns[static_cast<size_t>(i)].role != "system") {
                    remove_index = i;
                    break;
                }
            }
            if (remove_index >= 0) {
                turns.erase(turns.begin() + remove_index);
                continue;
            }
        }

        // A single enormous current message remains. Keep BOS plus the tail;
        // this is the last-resort guard against native context overflow.
        if (static_cast<int>(tokens.size()) > max_prompt_tokens) {
            std::vector<llama_token> clipped;
            clipped.reserve(static_cast<size_t>(max_prompt_tokens));
            if (!tokens.empty()) clipped.push_back(tokens.front());
            const int tail = std::max(0, max_prompt_tokens - static_cast<int>(clipped.size()));
            if (tail > 0) clipped.insert(clipped.end(), tokens.end() - std::min(tail, static_cast<int>(tokens.size())), tokens.end());
            if (static_cast<int>(clipped.size()) > max_prompt_tokens) clipped.resize(static_cast<size_t>(max_prompt_tokens));
            tokens.swap(clipped);
        }
        return true;
    }
}

bool has_stop_suffix(const std::string & text) {
    static const char * stops[] = {"<|EOT|>", "<|eot_id|>", "<|im_end|>", "<|end_of_turn|>", "<|endoftext|>"};
    for (const char * stop : stops) {
        size_t len = std::strlen(stop);
        if (text.size() >= len && text.compare(text.size() - len, len, stop) == 0) return true;
    }
    return false;
}

void strip_special_suffix(std::string & text) {
    static const char * stops[] = {"<|EOT|>", "<|eot_id|>", "<|im_end|>", "<|end_of_turn|>", "<|endoftext|>"};
    for (const char * stop : stops) {
        size_t len = std::strlen(stop);
        if (text.size() >= len && text.compare(text.size() - len, len, stop) == 0) {
            text.erase(text.size() - len);
            break;
        }
    }
}

std::string result_pack(const std::string & answer, int prompt_tokens, int generated_tokens,
                       long long first_ms, double tok_per_sec, long long total_ms) {
    return answer + RESULT_SEP + std::to_string(prompt_tokens) + RESULT_SEP
         + std::to_string(generated_tokens) + RESULT_SEP + std::to_string(first_ms)
         + RESULT_SEP + std::to_string(tok_per_sec) + RESULT_SEP + std::to_string(total_ms);
}
}

extern "C" {

JNIEXPORT jlong JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeLoadModel(
        JNIEnv * env, jobject, jstring jp, jstring, jint nc, jint nt) {
    init_backends();
    std::string path = js(env, jp);

    auto mp = llama_model_default_params();
    mp.n_gpu_layers = 0;
    llama_model * model = llama_model_load_from_file(path.c_str(), mp);
    if (!model) {
        fail(env, "تعذر فتح GGUF بعد النسخ — سجل llama backend يحدد سبب الفشل");
        return 0;
    }

    auto cp = llama_context_default_params();
    cp.n_ctx = std::max(512, static_cast<int>(nc));
    cp.n_batch = cp.n_ctx;
    cp.n_ubatch = std::min(cp.n_ctx, 512u);
    cp.n_threads = std::max(1, static_cast<int>(nt));
    cp.n_threads_batch = cp.n_threads;

    llama_context * ctx = llama_init_from_model(model, cp);
    if (!ctx) {
        llama_model_free(model);
        fail(env, "تم فتح GGUF لكن فشل إنشاء سياق llama.cpp");
        return 0;
    }

    auto * handle = new EngineHandle();
    handle->model = model;
    handle->ctx = ctx;
    handle->vocab = llama_model_get_vocab(model);
    handle->n_ctx = cp.n_ctx;

    const char * tmpl_ptr = llama_model_chat_template(model, nullptr);
    LOGI("model loaded; context=%d batch=%d ubatch=%d threads=%d chat_template=%s",
         static_cast<int>(cp.n_ctx), static_cast<int>(cp.n_batch), static_cast<int>(cp.n_ubatch),
         static_cast<int>(cp.n_threads), (tmpl_ptr && *tmpl_ptr) ? "yes" : "no");
    return reinterpret_cast<jlong>(handle);
}

JNIEXPORT jstring JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeGenerate(
        JNIEnv * env, jobject, jlong hp, jstring jt, jint max_tokens, jfloat temp,
        jint topk, jboolean code_mode) {
    auto * h = reinterpret_cast<EngineHandle *>(hp);
    if (!h || !h->ctx) { fail(env, "المحرك غير محمل"); return nullptr; }

    const auto total_start = std::chrono::steady_clock::now();
    auto turns = parse(js(env, jt));
    if (turns.empty()) { fail(env, "لا توجد رسائل للتوليد"); return nullptr; }

    const int requested_new = std::max(1, static_cast<int>(max_tokens));
    const int reserve = std::min(requested_new, std::max(1, h->n_ctx - 32));
    const int max_prompt_tokens = std::max(128, h->n_ctx - reserve - 8);

    std::string prompt;
    std::vector<llama_token> prompt_tokens;
    if (!fit_prompt(h, turns, code_mode == JNI_TRUE, max_prompt_tokens, prompt, prompt_tokens)) {
        fail(env, "فشل تجهيز prompt");
        return nullptr;
    }
    LOGI("bounded prompt tokens=%d/%d", static_cast<int>(prompt_tokens.size()), max_prompt_tokens);

    // Clean KV state on every turn. This removes stale-position risks from the old prompt-cache path.
    llama_memory_clear(llama_get_memory(h->ctx), true);
    h->cancel = false;

    auto batch = llama_batch_get_one(prompt_tokens.data(), prompt_tokens.size());
    if (llama_decode(h->ctx, batch) != 0) { fail(env, "فشل llama_decode للـprompt"); return nullptr; }

    auto sp = llama_sampler_chain_default_params();
    sp.no_perf = true;
    llama_sampler * sampler = llama_sampler_chain_init(sp);
    if (!sampler) { fail(env, "تعذر إنشاء sampler"); return nullptr; }

    const float effective_temp = std::max(0.01f, static_cast<float>(temp));
    if (effective_temp <= 0.01f) {
        llama_sampler_chain_add(sampler, llama_sampler_init_greedy());
    } else {
        llama_sampler_chain_add(sampler, llama_sampler_init_top_k(std::max(1, static_cast<int>(topk))));
        llama_sampler_chain_add(sampler, llama_sampler_init_min_p(0.05f, 1));
        llama_sampler_chain_add(sampler, llama_sampler_init_temp(effective_temp));
        llama_sampler_chain_add(sampler, llama_sampler_init_penalties(llama_vocab_n_tokens(h->vocab), 64, 1.10f, 0.0f, 0.0f));
        llama_sampler_chain_add(sampler, llama_sampler_init_dist(LLAMA_DEFAULT_SEED));
    }

    std::string output;
    int generated = 0;
    long long first_ms = -1;
    const auto generation_start = std::chrono::steady_clock::now();

    while (generated < requested_new && !h->cancel.load()) {
        llama_token token = llama_sampler_sample(sampler, h->ctx, -1);
        llama_sampler_accept(sampler, token);
        if (llama_vocab_is_eog(h->vocab, token)) break;

        char piece[512];
        int z = llama_token_to_piece(h->vocab, token, piece, sizeof(piece), 0, false);
        if (z > 0) output.append(piece, z);
        ++generated;

        if (first_ms < 0) {
            first_ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - total_start).count();
        }
        if (has_stop_suffix(output)) { strip_special_suffix(output); break; }

        llama_token one[1] = {token};
        auto next = llama_batch_get_one(one, 1);
        if (llama_decode(h->ctx, next) != 0) break;
    }

    llama_sampler_free(sampler);
    strip_special_suffix(output);

    const auto total_end = std::chrono::steady_clock::now();
    const double generation_seconds = std::chrono::duration<double>(total_end - generation_start).count();
    const double tok_per_sec = generated > 0 && generation_seconds > 0.0 ? generated / generation_seconds : 0.0;
    const long long total_ms = std::chrono::duration_cast<std::chrono::milliseconds>(total_end - total_start).count();

    LOGI("prompt=%d generated=%d first_ms=%lld tok_s=%.2f total_ms=%lld",
         static_cast<int>(prompt_tokens.size()), generated, first_ms, tok_per_sec, total_ms);

    return env->NewStringUTF(result_pack(output, static_cast<int>(prompt_tokens.size()), generated,
                                         first_ms, tok_per_sec, total_ms).c_str());
}

JNIEXPORT void JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeCancel(JNIEnv *, jobject, jlong hp) {
    auto * h = reinterpret_cast<EngineHandle *>(hp);
    if (h) h->cancel = true;
}

JNIEXPORT void JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeReset(JNIEnv *, jobject, jlong hp) {
    auto * h = reinterpret_cast<EngineHandle *>(hp);
    if (!h || !h->ctx) return;
    llama_memory_clear(llama_get_memory(h->ctx), true);
}

JNIEXPORT void JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeFree(JNIEnv *, jobject, jlong hp) {
    auto * h = reinterpret_cast<EngineHandle *>(hp);
    if (!h) return;
    if (h->ctx) llama_free(h->ctx);
    if (h->model) llama_model_free(h->model);
    delete h;
}
}
