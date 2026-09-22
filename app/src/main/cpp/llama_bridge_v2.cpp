#include <jni.h>
#include <android/log.h>
#include <algorithm>
#include <atomic>
#include <chrono>
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
    std::vector<llama_token> cached_prompt_tokens;
    std::atomic<bool> cancel{false};
};
std::once_flag backend_once;

// CPU backend is linked into llama.cpp by CMake. Do not attempt dynamic
// backend discovery: Android's nativeLibraryDir does not contain the separate
// ggml backend libraries unless they are explicitly packaged as JNI libs.
void init_backends() {
    std::call_once(backend_once, [] {
        llama_backend_init();
        LOGI("llama backend initialized; registered backends=%zu", ggml_backend_reg_count());
        for (size_t i = 0; i < ggml_backend_reg_count(); ++i) {
            auto *reg = ggml_backend_reg_get(i);
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
    std::vector<Turn> v; size_t p = 0;
    while (p <= x.size()) {
        size_t q = x.find(RECORD_SEP, p);
        std::string r = q == std::string::npos ? x.substr(p) : x.substr(p, q - p);
        if (!r.empty()) { size_t s = r.find(FIELD_SEP); if (s != std::string::npos) v.push_back({r.substr(0, s), r.substr(s + 1)}); }
        if (q == std::string::npos) break; p = q + 1;
    }
    return v;
}
std::string fallback(const std::vector<Turn> & v) {
    std::string o;
    for (const auto & t : v) { if (t.role == "system") continue; o += (t.role == "user" ? "User: " : "Assistant: "); o += t.text; o += '\n'; }
    o += "Assistant:"; return o;
}
std::string templ(const EngineHandle * h, const std::vector<Turn> & turns, bool code_mode) {
    if (!h || !h->model || turns.empty()) return {};
    std::vector<llama_chat_message> messages; messages.reserve(turns.size() + 1);
    std::string system;
    if (code_mode) system = "You are a precise offline programming assistant. Prioritize correct, runnable Python and explain assumptions briefly. Do not invent APIs. When code is requested, return complete code.";
    for (const auto & t : turns) if (t.role == "system") { if (!system.empty()) system += "\n\n"; system += t.text; }
    if (!system.empty()) messages.push_back({"system", system.c_str()});
    for (const auto & t : turns) if (t.role != "system") messages.push_back({t.role.c_str(), t.text.c_str()});
    if (messages.empty()) return fallback(turns);
    const char * model_tmpl = llama_model_chat_template(h->model, nullptr);
    if (!model_tmpl || !*model_tmpl) return fallback(turns);
    std::vector<char> buffer(8192);
    int n = llama_chat_apply_template(model_tmpl, messages.data(), messages.size(), true, buffer.data(), static_cast<int32_t>(buffer.size()));
    if (n < 0) return fallback(turns);
    if (n >= static_cast<int>(buffer.size())) { buffer.resize(static_cast<size_t>(n) + 1); n = llama_chat_apply_template(model_tmpl, messages.data(), messages.size(), true, buffer.data(), static_cast<int32_t>(buffer.size())); }
    return n > 0 ? std::string(buffer.data(), static_cast<size_t>(n)) : fallback(turns);
}
bool has_stop_suffix(const std::string & text) {
    static const char * stops[] = {"<|EOT|>", "<|eot_id|>", "<|im_end|>", "<|end_of_turn|>", "<|endoftext|>"};
    for (const char * stop : stops) { size_t len = std::strlen(stop); if (text.size() >= len && text.compare(text.size() - len, len, stop) == 0) return true; }
    return false;
}
void strip_special_suffix(std::string & text) {
    static const char * stops[] = {"<|EOT|>", "<|eot_id|>", "<|im_end|>", "<|end_of_turn|>", "<|endoftext|>"};
    for (const char * stop : stops) { size_t len = std::strlen(stop); if (text.size() >= len && text.compare(text.size() - len, len, stop) == 0) { text.erase(text.size() - len); break; } }
}
std::string result_pack(const std::string & answer, int prompt_tokens, int generated_tokens, long long first_ms, double tok_per_sec) {
    return answer + RESULT_SEP + std::to_string(prompt_tokens) + RESULT_SEP + std::to_string(generated_tokens) + RESULT_SEP + std::to_string(first_ms) + RESULT_SEP + std::to_string(tok_per_sec);
}
}

extern "C" {
JNIEXPORT jlong JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeLoadModel(JNIEnv * env, jobject, jstring jp, jstring, jint nc, jint nt) {
    init_backends();
    std::string path = js(env, jp);
    auto mp = llama_model_default_params();
    mp.n_gpu_layers = 0;
    llama_model * model = llama_model_load_from_file(path.c_str(), mp);
    if (!model) { fail(env, "تعذر فتح GGUF بعد النسخ — سجل llama backend يحدد سبب الفشل"); return 0; }
    auto cp = llama_context_default_params();
    cp.n_ctx = std::max(512, static_cast<int>(nc));
    cp.n_batch = std::min(cp.n_ctx, 512u);
    cp.n_ubatch = std::min(cp.n_batch, 512u);
    cp.n_threads = std::max(1, static_cast<int>(nt));
    cp.n_threads_batch = cp.n_threads;
    llama_context * ctx = llama_init_from_model(model, cp);
    if (!ctx) { llama_model_free(model); fail(env, "تم فتح GGUF لكن فشل إنشاء سياق llama.cpp"); return 0; }
    auto * handle = new EngineHandle(); handle->model = model; handle->ctx = ctx; handle->vocab = llama_model_get_vocab(model);
    LOGI("model loaded; context=%d threads=%d chat_template=%s", static_cast<int>(cp.n_ctx), static_cast<int>(cp.n_threads), llama_model_chat_template(model, nullptr) ? "yes" : "no");
    return reinterpret_cast<jlong>(handle);
}

JNIEXPORT jstring JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeGenerate(JNIEnv * env, jobject, jlong hp, jstring jt, jint max_tokens, jfloat temp, jint topk, jboolean code_mode) {
    auto * h = reinterpret_cast<EngineHandle *>(hp); if (!h || !h->ctx) { fail(env, "المحرك غير محمل"); return nullptr; }
    const auto total_start = std::chrono::steady_clock::now(); auto turns = parse(js(env, jt)); std::string prompt = templ(h, turns, code_mode == JNI_TRUE); if (prompt.empty()) prompt = fallback(turns);
    const bool first_context = h->cached_prompt_tokens.empty();
    int n_tokens = -llama_tokenize(h->vocab, prompt.c_str(), static_cast<int>(prompt.size()), nullptr, 0, first_context, true);
    if (n_tokens <= 0) { fail(env, "فشل ترميز prompt"); return nullptr; }
    std::vector<llama_token> prompt_tokens(static_cast<size_t>(n_tokens));
    if (llama_tokenize(h->vocab, prompt.c_str(), static_cast<int>(prompt.size()), prompt_tokens.data(), n_tokens, first_context, true) < 0) { fail(env, "فشل ترميز prompt"); return nullptr; }
    int common = 0, kv_prefix = 0;
    if (!h->cached_prompt_tokens.empty()) { size_t n = std::min(prompt_tokens.size(), h->cached_prompt_tokens.size() - 1); while (common < static_cast<int>(n) && prompt_tokens[common] == h->cached_prompt_tokens[1 + common]) ++common; kv_prefix = common + 1; }
    auto mem = llama_get_memory(h->ctx); const llama_pos pos_max = llama_memory_seq_pos_max(mem, 0);
    const bool reuse = !h->cached_prompt_tokens.empty() && common > 0 && pos_max >= kv_prefix - 1;
    if (!reuse) { llama_memory_clear(mem, true); h->cached_prompt_tokens.clear(); common = 0; kv_prefix = 0; } else llama_memory_seq_rm(mem, 0, kv_prefix, -1);
    h->cancel = false; const auto prompt_start = std::chrono::steady_clock::now(); size_t begin = reuse ? static_cast<size_t>(common) : 0;
    std::vector<llama_token> suffix(prompt_tokens.begin() + std::min(begin, prompt_tokens.size()), prompt_tokens.end());
    if (!suffix.empty()) { auto batch = llama_batch_get_one(suffix.data(), suffix.size()); if (llama_decode(h->ctx, batch) != 0) { fail(env, "فشل llama_decode للـprompt"); return nullptr; } }
    h->cached_prompt_tokens = prompt_tokens;
    auto sp = llama_sampler_chain_default_params(); sp.no_perf = true; llama_sampler * sampler = llama_sampler_chain_init(sp); if (!sampler) { fail(env, "تعذر إنشاء sampler"); return nullptr; }
    if (code_mode == JNI_TRUE || temp <= 0.01f) llama_sampler_chain_add(sampler, llama_sampler_init_greedy()); else { llama_sampler_chain_add(sampler, llama_sampler_init_top_k(std::max(1, static_cast<int>(topk)))); llama_sampler_chain_add(sampler, llama_sampler_init_min_p(0.05f, 1)); llama_sampler_chain_add(sampler, llama_sampler_init_temp(temp)); llama_sampler_chain_add(sampler, llama_sampler_init_dist(LLAMA_DEFAULT_SEED)); }
    std::string output; int generated = 0; long long first_ms = -1; const auto gen_start = std::chrono::steady_clock::now();
    while (generated < max_tokens && !h->cancel.load()) { llama_token token = llama_sampler_sample(sampler, h->ctx, -1); llama_sampler_accept(sampler, token); if (llama_vocab_is_eog(h->vocab, token)) break; char piece[512]; int z = llama_token_to_piece(h->vocab, token, piece, sizeof(piece), 0, false); if (z > 0) output.append(piece, z); ++generated; if (first_ms < 0) first_ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - prompt_start).count(); if (has_stop_suffix(output)) { strip_special_suffix(output); break; } llama_token one[1] = {token}; auto next = llama_batch_get_one(one, 1); if (llama_decode(h->ctx, next) != 0) break; }
    llama_sampler_free(sampler); strip_special_suffix(output); const auto end = std::chrono::steady_clock::now(); const double sec = std::chrono::duration<double>(end - gen_start).count(); const double tps = generated > 0 && sec > 0 ? generated / sec : 0.0;
    LOGI("prompt=%d generated=%d first_ms=%lld tok_s=%.2f total_ms=%lld", n_tokens, generated, first_ms, tps, static_cast<long long>(std::chrono::duration_cast<std::chrono::milliseconds>(end - total_start).count()));
    return env->NewStringUTF(result_pack(output, n_tokens, generated, first_ms, tps).c_str());
}
JNIEXPORT void JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeCancel(JNIEnv *, jobject, jlong hp) { auto *h = reinterpret_cast<EngineHandle *>(hp); if (h) h->cancel = true; }
JNIEXPORT void JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeReset(JNIEnv *, jobject, jlong hp) { auto *h = reinterpret_cast<EngineHandle *>(hp); if (!h || !h->ctx) return; llama_memory_clear(llama_get_memory(h->ctx), true); h->cached_prompt_tokens.clear(); }
JNIEXPORT void JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeFree(JNIEnv *, jobject, jlong hp) { auto *h = reinterpret_cast<EngineHandle *>(hp); if (!h) return; if (h->ctx) llama_free(h->ctx); if (h->model) llama_model_free(h->model); delete h; }
}
