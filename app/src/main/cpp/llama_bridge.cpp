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
// Tokens always kept free at the end of the context window.
constexpr int CTX_SAFETY_TOKENS = 8;
// Refuse to generate if less than this many tokens of room are left.
constexpr int MIN_GEN_TOKENS = 32;

// Generation stops when the output ends with any of these. Besides the usual
// end-of-turn markers, this catches a model that starts writing the NEXT turn
// itself (inventing "User:" / "### Instruction:" lines) -- the fake dialogue
// seen with base models or when the GGUF has no chat template.
const char * const STOPS[] = {
    "<|EOT|>", "<|eot_id|>", "<|im_end|>", "<|end_of_turn|>", "<|endoftext|>",
    "### Instruction", "\nUser:", "\nuser:", "\nHuman:",
};

struct Turn { std::string role; std::string text; };
struct EngineHandle {
    llama_model * model = nullptr;
    llama_context * ctx = nullptr;
    const llama_vocab * vocab = nullptr;
    int n_ctx = 0;
    int n_batch = 0;
    // Every token currently stored in KV sequence 0, position by position
    // (prompt tokens AND generated tokens). Used for prefix reuse.
    std::vector<llama_token> kv_tokens;
    std::atomic<bool> cancel{false};
};
bool initialized = false;
void init() { if (!initialized) { llama_backend_init(); initialized = true; } }
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
// Used when the GGUF has no chat template. DeepSeek-Coder-Instruct format.
std::string fallback(const std::vector<Turn> & v, const std::string & system) {
    std::string o;
    if (!system.empty()) { o += system; o += '\n'; }
    for (const auto & t : v) {
        if (t.role == "system") continue;
        if (t.role == "user") {
            o += "### Instruction:\n"; o += t.text; o += '\n';
        } else {
            o += "### Response:\n"; o += t.text; o += "\n<|EOT|>\n";
        }
    }
    o += "### Response:\n";
    return o;
}
std::string templ(const EngineHandle * handle, const std::vector<Turn> & turns, bool code_mode) {
    if (!handle || !handle->model || turns.empty()) return {};
    std::vector<llama_chat_message> messages;
    messages.reserve(turns.size() + 1);

    std::string system;
    if (code_mode) {
        system = "You are a precise offline programming assistant. "
                 "Prioritize correct, runnable Python and explain assumptions briefly. "
                 "Do not invent APIs. When code is requested, return complete code.";
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
    if (messages.empty()) return fallback(turns, system);

    const char * model_tmpl = llama_model_chat_template(handle->model, nullptr);
    if (!model_tmpl || !*model_tmpl) return fallback(turns, system);
    std::vector<char> buffer(8192);
    int n = llama_chat_apply_template(model_tmpl, messages.data(), messages.size(), true,
                                      buffer.data(), static_cast<int32_t>(buffer.size()));
    if (n < 0) return fallback(turns, system);
    if (n >= static_cast<int>(buffer.size())) {
        buffer.resize(static_cast<size_t>(n) + 1);
        n = llama_chat_apply_template(model_tmpl, messages.data(), messages.size(), true,
                                      buffer.data(), static_cast<int32_t>(buffer.size()));
    }
    if (n <= 0) return fallback(turns, system);
    return std::string(buffer.data(), static_cast<size_t>(n));
}

// Tokenizes the full prompt (with BOS). Many chat templates (DeepSeek included)
// already emit the BOS text themselves, which would produce a double BOS and
// degrade output quality, so a duplicated leading BOS is removed.
bool tokenize_prompt(const EngineHandle * h, const std::string & text, std::vector<llama_token> & out) {
    const int needed = -llama_tokenize(h->vocab, text.c_str(), static_cast<int>(text.size()),
                                       nullptr, 0, true, true);
    if (needed <= 0) return false;
    out.resize(static_cast<size_t>(needed));
    const int n = llama_tokenize(h->vocab, text.c_str(), static_cast<int>(text.size()),
                                 out.data(), needed, true, true);
    if (n < 0) return false;
    out.resize(static_cast<size_t>(n));
    const llama_token bos = llama_vocab_bos(h->vocab);
    if (bos != LLAMA_TOKEN_NULL && out.size() >= 2 && out[0] == bos && out[1] == bos) {
        out.erase(out.begin());
    }
    return !out.empty();
}

// Removes the oldest conversation turn (never system turns) and keeps the
// conversation starting with a user turn. Returns false if nothing can be dropped.
bool drop_oldest_turn(std::vector<Turn> & turns) {
    auto non_system = [](const Turn & t) { return t.role != "system"; };
    if (std::count_if(turns.begin(), turns.end(), non_system) <= 1) return false;
    turns.erase(std::find_if(turns.begin(), turns.end(), non_system));
    for (;;) {
        auto it = std::find_if(turns.begin(), turns.end(), non_system);
        if (it == turns.end() || it->role == "user") break;
        if (std::count_if(turns.begin(), turns.end(), non_system) <= 1) break;
        turns.erase(it);
    }
    return true;
}

// Decodes tokens in n_batch-sized chunks (a single llama_decode call cannot
// take more than n_batch tokens) and records them in kv_tokens.
bool decode_tokens(EngineHandle * h, const llama_token * data, int n) {
    for (int i = 0; i < n; i += h->n_batch) {
        const int len = std::min(h->n_batch, n - i);
        llama_batch batch = llama_batch_get_one(const_cast<llama_token *>(data + i), len);
        if (llama_decode(h->ctx, batch) != 0) return false;
        h->kv_tokens.insert(h->kv_tokens.end(), data + i, data + i + len);
    }
    return true;
}

void reset_kv(EngineHandle * h) {
    llama_memory_clear(llama_get_memory(h->ctx), true);
    h->kv_tokens.clear();
}

// If the output ends with a stop marker, removes it (plus trailing whitespace)
// and returns true.
bool strip_stop_suffix(std::string & text) {
    for (const char * stop : STOPS) {
        const size_t len = std::strlen(stop);
        if (text.size() >= len && text.compare(text.size() - len, len, stop) == 0) {
            text.erase(text.size() - len);
            while (!text.empty() && (text.back() == '\n' || text.back() == ' ')) text.pop_back();
            return true;
        }
    }
    return false;
}
std::string result_pack(const std::string & answer, int prompt_tokens, int generated_tokens,
                       long long first_ms, double tok_per_sec) {
    return answer + RESULT_SEP + std::to_string(prompt_tokens) + RESULT_SEP
         + std::to_string(generated_tokens) + RESULT_SEP + std::to_string(first_ms)
         + RESULT_SEP + std::to_string(tok_per_sec);
}
}

extern "C" {
JNIEXPORT jlong JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeLoadModel(
        JNIEnv * env, jobject, jstring jp, jint nc, jint nt) {
    init();
    std::string path = js(env, jp);
    auto mp = llama_model_default_params();
    mp.n_gpu_layers = 0;
    llama_model * model = llama_model_load_from_file(path.c_str(), mp);
    if (!model) { fail(env, "تعذر تحميل ملف GGUF"); return 0; }
    auto cp = llama_context_default_params();
    cp.n_ctx = std::max(512, static_cast<int>(nc));
    cp.n_batch = std::min(cp.n_ctx, 512u);
    cp.n_threads = std::max(1, static_cast<int>(nt));
    cp.n_threads_batch = cp.n_threads;
    llama_context * ctx = llama_init_from_model(model, cp);
    if (!ctx) { llama_model_free(model); fail(env, "تعذر إنشاء سياق النموذج"); return 0; }
    auto * handle = new EngineHandle();
    handle->model = model; handle->ctx = ctx; handle->vocab = llama_model_get_vocab(model);
    handle->n_ctx = static_cast<int>(llama_n_ctx(ctx));
    handle->n_batch = std::max(1, static_cast<int>(llama_n_batch(ctx)));
    LOGE("model loaded; context=%d batch=%d threads=%d chat_template=%s", handle->n_ctx,
         handle->n_batch, static_cast<int>(cp.n_threads),
         llama_model_chat_template(model, nullptr) ? "yes" : "no");
    return reinterpret_cast<jlong>(handle);
}

JNIEXPORT jbyteArray JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeGenerate(
        JNIEnv * env, jobject, jlong hp, jstring jt, jint max_tokens, jfloat temp,
        jint topk, jboolean code_mode) {
    auto * handle = reinterpret_cast<EngineHandle *>(hp);
    if (!handle || !handle->ctx) { fail(env, "المحرك غير محمل"); return nullptr; }
    const auto total_start = std::chrono::steady_clock::now();
    auto turns = parse(js(env, jt));
    if (turns.empty()) { fail(env, "لا توجد رسالة لإرسالها"); return nullptr; }

    // 1) Build a prompt that, together with the requested output, fits n_ctx.
    int max_new = std::max(1, static_cast<int>(max_tokens));
    std::vector<llama_token> prompt_tokens;
    int dropped_turns = 0;
    for (;;) {
        std::string prompt = templ(handle, turns, code_mode == JNI_TRUE);
        if (prompt.empty()) prompt = fallback(turns, "");
        if (!tokenize_prompt(handle, prompt, prompt_tokens)) { fail(env, "فشل ترميز النص"); return nullptr; }
        if (static_cast<int>(prompt_tokens.size()) + max_new + CTX_SAFETY_TOKENS <= handle->n_ctx) break;
        if (!drop_oldest_turn(turns)) break;
        ++dropped_turns;
    }
    const int room = handle->n_ctx - static_cast<int>(prompt_tokens.size()) - CTX_SAFETY_TOKENS;
    if (room < MIN_GEN_TOKENS) {
        fail(env, "الرسالة أطول من سياق النموذج — قصّرها أو امسح المحادثة");
        return nullptr;
    }
    max_new = std::min(max_new, room);

    // 2) Reuse the longest prefix already in the KV cache.
    auto mem = llama_get_memory(handle->ctx);
    size_t common = 0;
    const size_t limit = std::min(handle->kv_tokens.size(), prompt_tokens.size());
    while (common < limit && handle->kv_tokens[common] == prompt_tokens[common]) ++common;
    // At least one prompt token must be decoded to get fresh logits.
    if (common == prompt_tokens.size()) --common;
    const bool reused = common > 0 &&
            llama_memory_seq_rm(mem, 0, static_cast<llama_pos>(common), -1);
    if (reused) {
        handle->kv_tokens.resize(common);
    } else {
        reset_kv(handle);
        common = 0;
    }

    handle->cancel = false;
    const auto prompt_start = std::chrono::steady_clock::now();
    if (!decode_tokens(handle, prompt_tokens.data() + common,
                       static_cast<int>(prompt_tokens.size() - common))) {
        reset_kv(handle);
        fail(env, "فشل تشغيل النموذج");
        return nullptr;
    }

    // 3) Sampler. A mild repetition penalty stops greedy decoding from looping.
    auto sparams = llama_sampler_chain_default_params();
    sparams.no_perf = true;
    llama_sampler * sampler = llama_sampler_chain_init(sparams);
    if (!sampler) { fail(env, "تعذر إنشاء sampler"); return nullptr; }
    const bool greedy = code_mode == JNI_TRUE || temp <= 0.01f;
    llama_sampler_chain_add(sampler, llama_sampler_init_penalties(64, greedy ? 1.05f : 1.10f, 0.0f, 0.0f));
    if (greedy) {
        llama_sampler_chain_add(sampler, llama_sampler_init_greedy());
    } else {
        llama_sampler_chain_add(sampler, llama_sampler_init_top_k(std::max(1, static_cast<int>(topk))));
        llama_sampler_chain_add(sampler, llama_sampler_init_min_p(0.05f, 1));
        llama_sampler_chain_add(sampler, llama_sampler_init_temp(temp));
        llama_sampler_chain_add(sampler, llama_sampler_init_dist(LLAMA_DEFAULT_SEED));
    }

    // 4) Generate, never letting the KV cache overflow n_ctx.
    std::string output;
    int generated = 0;
    long long first_ms = -1;
    const auto gen_start = std::chrono::steady_clock::now();
    while (generated < max_new && !handle->cancel.load()) {
        if (static_cast<int>(handle->kv_tokens.size()) >= handle->n_ctx - 1) break;
        llama_token token = llama_sampler_sample(sampler, handle->ctx, -1);
        llama_sampler_accept(sampler, token);
        if (llama_vocab_is_eog(handle->vocab, token)) break;
        char piece[512];
        int z = llama_token_to_piece(handle->vocab, token, piece, sizeof(piece), 0, false);
        if (z > 0) output.append(piece, z);
        ++generated;
        if (first_ms < 0) first_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - prompt_start).count();
        if (strip_stop_suffix(output)) break;
        if (!decode_tokens(handle, &token, 1)) break;
    }
    llama_sampler_free(sampler);
    strip_stop_suffix(output);

    const int n_prompt = static_cast<int>(prompt_tokens.size());
    const auto total_end = std::chrono::steady_clock::now();
    const double gen_seconds = std::chrono::duration<double>(total_end - gen_start).count();
    const double tok_per_sec = generated > 0 && gen_seconds > 0.0 ? generated / gen_seconds : 0.0;
    LOGE("prompt=%d reused=%d dropped_turns=%d generated=%d/%d first_ms=%lld tok_s=%.2f total_ms=%lld",
         n_prompt, static_cast<int>(common), dropped_turns, generated, max_new, first_ms, tok_per_sec,
         static_cast<long long>(std::chrono::duration_cast<std::chrono::milliseconds>(
             total_end - total_start).count()));

    const std::string packed = result_pack(output, n_prompt, generated, first_ms, tok_per_sec);
    jbyteArray arr = env->NewByteArray(static_cast<jsize>(packed.size()));
    if (arr) {
        env->SetByteArrayRegion(arr, 0, static_cast<jsize>(packed.size()),
                                reinterpret_cast<const jbyte *>(packed.data()));
    }
    return arr;
}

JNIEXPORT void JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeCancel(JNIEnv *, jobject, jlong hp) {
    auto * handle = reinterpret_cast<EngineHandle *>(hp);
    if (handle) handle->cancel = true;
}
JNIEXPORT void JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeReset(JNIEnv *, jobject, jlong hp) {
    auto * handle = reinterpret_cast<EngineHandle *>(hp);
    if (!handle || !handle->ctx) return;
    reset_kv(handle);
}
JNIEXPORT void JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeFree(JNIEnv *, jobject, jlong hp) {
    auto * handle = reinterpret_cast<EngineHandle *>(hp);
    if (!handle) return;
    if (handle->ctx) llama_free(handle->ctx);
    if (handle->model) llama_model_free(handle->model);
    delete handle;
}
}
