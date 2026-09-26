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
    int n_batch = 0;
    bool has_template = false;
    // Exactly the tokens currently held in the KV cache (sequence 0), in order. Lets the
    // next prompt skip re-decoding the prefix it shares with the previous one.
    std::vector<llama_token> kv;
    std::atomic<bool> cancel{false};
    // Recurrent / hybrid models (Qwen3.5, Mamba, LFM2 ...) cannot drop the end of their state, so
    // the shared prefix of consecutive prompts is kept as a saved state instead (as llama-server
    // does): the tokens it covers and the model's recurrent state right after them.
    bool recurrent = false;
    std::vector<llama_token> ckpt_tokens;
    std::vector<uint8_t> ckpt_state;
    std::vector<llama_token> last_prompt;
};

// Flags passed from Java.
constexpr int FLAG_CODE_MODE = 1;  // add the built-in programming system prompt
constexpr int FLAG_RAW = 2;        // caller supplies its own system turn; no chat-only stop markers

enum StopReason { STOP_EOG = 0, STOP_MAX_TOKENS = 1, STOP_CONTEXT_FULL = 2, STOP_CANCELLED = 3,
                  STOP_STRING = 4, STOP_DECODE_ERROR = 5, STOP_CALLBACK = 6 };

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
    if (n <= 0) return fallback(turns);
    std::string out(buffer.data(), static_cast<size_t>(n));
    // Qwen3 / Qwen3.5 style templates: an empty think block is their documented "no thinking"
    // switch. The built-in ChatML formatter omits it, so the model would reason at length first.
    static const std::string assistant_open = "<|im_start|>assistant\n";
    if (std::strstr(model_tmpl, "<think>") && out.size() >= assistant_open.size()
        && out.compare(out.size() - assistant_open.size(), assistant_open.size(), assistant_open) == 0) {
        out += "<think>\n\n</think>\n\n";
    }
    return out;
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

const std::vector<std::string> & special_stops() {
    static const std::vector<std::string> v = {"<|EOT|>", "<|eot_id|>", "<|im_end|>", "<|end_of_turn|>", "<|endoftext|>"};
    return v;
}

// Text that means the model has started writing the next turn itself.
const std::vector<std::string> & turn_stops() {
    static const std::vector<std::string> v = {"\nUser:", "\n### Instruction", "\nAssistant:"};
    return v;
}

// Position of the first complete stop string in text[from..], or npos.
size_t find_stop(const std::string & text, size_t from, const std::vector<std::string> & stops, size_t & stop_len) {
    size_t best = std::string::npos;
    for (const auto & s : stops) {
        size_t start = from > s.size() ? from - s.size() : 0;
        size_t at = text.find(s, start);
        if (at != std::string::npos && at < best) { best = at; stop_len = s.size(); }
    }
    return best;
}

// Length of the longest suffix of text that is a proper prefix of a stop string. That many
// bytes are held back from streaming until it is clear they are not a stop marker.
size_t holdback(const std::string & text, const std::vector<std::string> & stops) {
    size_t keep = 0;
    for (const auto & s : stops) {
        for (size_t n = std::min(s.size() - 1, text.size()); n > keep; --n) {
            if (text.compare(text.size() - n, n, s, 0, n) == 0) { keep = n; break; }
        }
    }
    return keep;
}

// Appends a token's text; grows the buffer instead of dropping long pieces.
void append_piece(const llama_vocab * vocab, llama_token token, std::string & out) {
    char small[256];
    int n = llama_token_to_piece(vocab, token, small, sizeof(small), 0, false);
    if (n >= 0) { out.append(small, static_cast<size_t>(n)); return; }
    std::vector<char> big(static_cast<size_t>(-n));
    n = llama_token_to_piece(vocab, token, big.data(), static_cast<int32_t>(big.size()), 0, false);
    if (n > 0) out.append(big.data(), static_cast<size_t>(n));
}

// Decodes tokens[from..] after the reusable prefix, in n_batch chunks, tracking the KV cache.
bool decode_range(EngineHandle * h, const std::vector<llama_token> & tokens, size_t from, size_t to) {
    for (size_t i = from; i < to; i += static_cast<size_t>(h->n_batch)) {
        const int n = static_cast<int>(std::min(to - i, static_cast<size_t>(h->n_batch)));
        auto batch = llama_batch_get_one(const_cast<llama_token *>(tokens.data() + i), n);
        if (llama_decode(h->ctx, batch) != 0) {
            llama_memory_clear(llama_get_memory(h->ctx), true);
            h->kv.clear();
            return false;
        }
        h->kv.insert(h->kv.end(), tokens.begin() + static_cast<long>(i), tokens.begin() + static_cast<long>(i) + n);
    }
    return true;
}

size_t common_prefix(const std::vector<llama_token> & a, const std::vector<llama_token> & b) {
    size_t n = 0;
    while (n < a.size() && n < b.size() && a[n] == b[n]) ++n;
    return n;
}

// Restores the saved prefix state when it matches the start of this prompt.
bool restore_checkpoint(EngineHandle * h, const std::vector<llama_token> & tokens) {
    const size_t n = h->ckpt_tokens.size();
    if (n == 0 || n >= tokens.size() || common_prefix(h->ckpt_tokens, tokens) != n) return false;
    auto * mem = llama_get_memory(h->ctx);
    llama_memory_clear(mem, true);
    if (llama_state_seq_set_data_ext(h->ctx, h->ckpt_state.data(), h->ckpt_state.size(), 0,
                                     LLAMA_STATE_SEQ_FLAGS_NONE) == 0) {
        llama_memory_clear(mem, true);
        h->ckpt_tokens.clear();
        h->ckpt_state.clear();
        return false;
    }
    h->kv = h->ckpt_tokens;
    return true;
}

void save_checkpoint(EngineHandle * h) {
    const size_t size = llama_state_seq_get_size_ext(h->ctx, 0, LLAMA_STATE_SEQ_FLAGS_NONE);
    h->ckpt_state.resize(size);
    if (size == 0 || llama_state_seq_get_data_ext(h->ctx, h->ckpt_state.data(), size, 0, LLAMA_STATE_SEQ_FLAGS_NONE) != size) {
        h->ckpt_tokens.clear();
        h->ckpt_state.clear();
        return;
    }
    h->ckpt_tokens = h->kv;
    LOGI("prefix checkpoint saved: %zu tokens, %.1f MB", h->ckpt_tokens.size(), size / 1048576.0);
}

bool decode_prompt(EngineHandle * h, const std::vector<llama_token> & tokens, int & reused) {
    size_t common = common_prefix(h->kv, tokens);
    // The last prompt token must be decoded again to get fresh logits for sampling.
    if (common == tokens.size() && common > 0) --common;
    auto * mem = llama_get_memory(h->ctx);
    if (common == 0 || !llama_memory_seq_rm(mem, 0, static_cast<llama_pos>(common), -1)) {
        if (h->recurrent && restore_checkpoint(h, tokens)) {
            common = h->kv.size();
        } else {
            llama_memory_clear(mem, true);
            common = 0;
            h->kv.clear();
        }
    }
    h->kv.resize(common);
    reused = static_cast<int>(common);

    // Where this prompt departs from the previous one: the part before it is likely shared with
    // the next prompt too (system prompt, goal, earlier steps), so save the state there.
    size_t split = 0;
    if (h->recurrent) {
        split = common_prefix(h->last_prompt, tokens);
        if (split >= tokens.size()) split = tokens.size() - 1;
        if (split < common + 32) split = 0;
    }
    h->last_prompt = tokens;
    if (split > 0) {
        if (!decode_range(h, tokens, common, split)) return false;
        save_checkpoint(h);
        common = split;
    }
    return decode_range(h, tokens, common, tokens.size());
}

std::string result_pack(int prompt_tokens, int generated_tokens, long long first_ms, double tok_per_sec,
                        long long total_ms, int stop_reason, int reused_tokens) {
    return std::string() + RESULT_SEP + std::to_string(prompt_tokens) + RESULT_SEP
         + std::to_string(generated_tokens) + RESULT_SEP + std::to_string(first_ms)
         + RESULT_SEP + std::to_string(tok_per_sec) + RESULT_SEP + std::to_string(total_ms)
         + RESULT_SEP + std::to_string(stop_reason) + RESULT_SEP + std::to_string(reused_tokens);
}

// Sends bytes to the Java sink; false when Java threw (the caller stops generating).
bool emit(JNIEnv * env, jobject sink, jmethodID on_bytes, const char * data, size_t len) {
    if (!sink || len == 0) return true;
    jbyteArray arr = env->NewByteArray(static_cast<jsize>(len));
    if (!arr) return false;
    env->SetByteArrayRegion(arr, 0, static_cast<jsize>(len), reinterpret_cast<const jbyte *>(data));
    env->CallVoidMethod(sink, on_bytes, arr);
    env->DeleteLocalRef(arr);
    return !env->ExceptionCheck();
}
// ---------------------------------------------------------------- speculative decoding helpers

llama_token argmax_token(const float * logits, int n_vocab) {
    llama_token best = 0;
    float best_logit = logits[0];
    for (llama_token i = 1; i < n_vocab; ++i) {
        if (logits[i] > best_logit) { best_logit = logits[i]; best = i; }
    }
    return best;
}

// True when the grammar allows this token next (the grammar state is not changed).
bool grammar_allows(llama_sampler * grammar, llama_token token) {
    llama_token_data single = {token, 1.0f, 0.0f};
    llama_token_data_array one = {&single, 1, -1, false};
    llama_sampler_apply(grammar, &one);
    return !(std::isinf(single.logit) && single.logit < 0);
}

// Best token the grammar allows, or LLAMA_TOKEN_NULL when it allows nothing.
llama_token grammar_best(llama_sampler * grammar, const float * logits, int n_vocab, std::vector<llama_token_data> & cand) {
    cand.resize(static_cast<size_t>(n_vocab));
    for (llama_token id = 0; id < n_vocab; ++id) cand[static_cast<size_t>(id)] = {id, logits[id], 0.0f};
    llama_token_data_array all = {cand.data(), cand.size(), -1, false};
    llama_sampler_apply(grammar, &all);
    llama_token best = LLAMA_TOKEN_NULL;
    float best_logit = -INFINITY;
    for (size_t i = 0; i < all.size; ++i) {
        if (all.data[i].logit > best_logit) { best_logit = all.data[i].logit; best = all.data[i].id; }
    }
    return best;
}

// Draft and target must use the same tokenizer for their tokens to be compared.
bool same_vocab(const EngineHandle * a, const EngineHandle * b) {
    const int n = llama_vocab_n_tokens(a->vocab);
    if (n != llama_vocab_n_tokens(b->vocab)) return false;
    const llama_token probes[] = {0, 1, 100, 1000, 5000, n / 2, n - 1000, n - 1};
    for (llama_token t : probes) {
        if (t < 0 || t >= n) continue;
        const char * x = llama_vocab_get_text(a->vocab, t);
        const char * y = llama_vocab_get_text(b->vocab, t);
        if (!x || !y || std::strcmp(x, y) != 0) return false;
    }
    return true;
}

// Makes the draft's KV cache hold exactly `tokens` (reusing the common prefix).
bool sync_kv(EngineHandle * d, const std::vector<llama_token> & tokens) {
    size_t common = 0;
    while (common < d->kv.size() && common < tokens.size() && d->kv[common] == tokens[common]) ++common;
    auto * mem = llama_get_memory(d->ctx);
    if (common < d->kv.size()) {
        if (!llama_memory_seq_rm(mem, 0, static_cast<llama_pos>(common), -1)) {
            llama_memory_clear(mem, true);
            common = 0;
        }
        d->kv.resize(common);
    }
    for (size_t i = common; i < tokens.size(); i += static_cast<size_t>(d->n_batch)) {
        const int n = static_cast<int>(std::min(tokens.size() - i, static_cast<size_t>(d->n_batch)));
        auto batch = llama_batch_get_one(const_cast<llama_token *>(tokens.data() + i), n);
        if (llama_decode(d->ctx, batch) != 0) {
            llama_memory_clear(mem, true);
            d->kv.clear();
            return false;
        }
        d->kv.insert(d->kv.end(), tokens.begin() + static_cast<long>(i), tokens.begin() + static_cast<long>(i) + n);
    }
    return true;
}

bool decode_one(EngineHandle * h, llama_token t) {
    llama_token one[1] = {t};
    if (llama_decode(h->ctx, llama_batch_get_one(one, 1)) != 0) return false;
    h->kv.push_back(t);
    return true;
}

}  // namespace

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

    // Preferred: requested context with an 8-bit KV cache (half the RAM of f16, same speed
    // class on ARM). Fall back to f16, then to a smaller context, if the device refuses.
    struct Attempt { int n_ctx; bool q8; };
    const int requested = std::max(512, static_cast<int>(nc));
    const Attempt attempts[] = {{requested, true}, {requested, false}, {std::min(requested, 2048), false}};
    llama_context * ctx = nullptr;
    llama_context_params cp{};
    for (const auto & a : attempts) {
        cp = llama_context_default_params();
        cp.n_ctx = static_cast<uint32_t>(a.n_ctx);
        cp.n_batch = std::min(cp.n_ctx, 512u);
        cp.n_ubatch = cp.n_batch;
        cp.n_threads = std::max(1, static_cast<int>(nt));
        cp.n_threads_batch = cp.n_threads;
        if (a.q8) {
            cp.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_ENABLED;
            cp.type_k = GGML_TYPE_Q8_0;
            cp.type_v = GGML_TYPE_Q8_0;
        }
        ctx = llama_init_from_model(model, cp);
        if (ctx) break;
        LOGE("context init failed (n_ctx=%d q8=%d); trying next configuration", a.n_ctx, a.q8 ? 1 : 0);
    }
    if (!ctx) {
        llama_model_free(model);
        fail(env, "تم فتح GGUF لكن فشل إنشاء سياق llama.cpp");
        return 0;
    }

    auto * handle = new EngineHandle();
    handle->model = model;
    handle->ctx = ctx;
    handle->vocab = llama_model_get_vocab(model);
    handle->n_ctx = static_cast<int>(llama_n_ctx(ctx));
    handle->n_batch = static_cast<int>(cp.n_batch);

    const char * tmpl_ptr = llama_model_chat_template(model, nullptr);
    handle->has_template = tmpl_ptr && *tmpl_ptr;
    handle->recurrent = llama_model_is_recurrent(model) || llama_model_is_hybrid(model);
    LOGI("model loaded; context=%d batch=%d threads=%d kv=%s chat_template=%s",
         handle->n_ctx, handle->n_batch, static_cast<int>(cp.n_threads),
         cp.type_k == GGML_TYPE_Q8_0 ? "q8_0" : "f16", handle->has_template ? "yes" : "no");
    return reinterpret_cast<jlong>(handle);
}

// Generates a reply. Text streams to sink.onBytes(byte[]) as raw UTF-8 (a multi-byte
// character may be split across calls; Java decodes incrementally). Returns only metrics.
// max_tokens <= 0 means: until end of turn or the context is full.
// Generates a reply. Text streams to sink.onBytes(byte[]) as raw UTF-8 (a multi-byte
// character may be split across calls; Java decodes incrementally). Returns only metrics.
// max_tokens <= 0 means: until end of turn or the context is full.
// With a draft model (greedy decoding only), the draft proposes up to n_draft tokens and the
// target verifies them in one batch: the output is exactly what the target alone would produce.
JNIEXPORT jstring JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeGenerate(
        JNIEnv * env, jobject, jlong hp, jstring jt, jint max_tokens, jfloat temp,
        jint topk, jint flags, jstring jgrammar, jlong draft_hp, jint n_draft, jobject sink) {
    auto * h = reinterpret_cast<EngineHandle *>(hp);
    if (!h || !h->ctx) { fail(env, "المحرك غير محمل"); return nullptr; }

    jmethodID on_bytes = nullptr;
    if (sink) {
        jclass cls = env->GetObjectClass(sink);
        on_bytes = env->GetMethodID(cls, "onBytes", "([B)V");
        env->DeleteLocalRef(cls);
        if (!on_bytes) return nullptr;
    }

    const auto total_start = std::chrono::steady_clock::now();
    auto turns = parse(js(env, jt));
    if (turns.empty()) { fail(env, "لا توجد رسائل للتوليد"); return nullptr; }

    const bool code_mode = (flags & FLAG_CODE_MODE) != 0;
    const bool raw = (flags & FLAG_RAW) != 0;
    const bool until_full = max_tokens <= 0;
    const bool greedy = temp <= 0.01f;
    // Room kept for the answer when fitting the prompt.
    const int answer_room = until_full ? std::max(256, h->n_ctx / 4)
                                       : std::min(static_cast<int>(max_tokens), std::max(1, h->n_ctx - 32));
    const int max_prompt_tokens = std::max(128, h->n_ctx - answer_room - 8);

    std::string prompt;
    std::vector<llama_token> prompt_tokens;
    if (!fit_prompt(h, turns, code_mode, max_prompt_tokens, prompt, prompt_tokens)) {
        fail(env, "فشل تجهيز prompt");
        return nullptr;
    }

    h->cancel = false;
    int reused = 0;
    if (!decode_prompt(h, prompt_tokens, reused)) { fail(env, "فشل llama_decode للـprompt"); return nullptr; }

    auto * draft = reinterpret_cast<EngineHandle *>(draft_hp);
    // Rejected draft tokens are removed from the cache, which recurrent state cannot do.
    if (draft && (!greedy || !draft->ctx || draft == h || h->recurrent || draft->recurrent || !same_vocab(h, draft)
                  || draft->n_ctx < h->n_ctx)) {
        LOGI("speculative decoding off for this call (greedy=%d)", greedy ? 1 : 0);
        draft = nullptr;
    }
    const int k_draft = std::max(1, std::min(16, static_cast<int>(n_draft)));
    LOGI("prompt tokens=%d reused=%d budget=%d draft=%s", static_cast<int>(prompt_tokens.size()), reused,
         max_prompt_tokens, draft ? "on" : "off");

    auto sp = llama_sampler_chain_default_params();
    sp.no_perf = true;
    llama_sampler * sampler = llama_sampler_chain_init(sp);
    if (!sampler) { fail(env, "تعذر إنشاء sampler"); return nullptr; }
    if (greedy) {
        llama_sampler_chain_add(sampler, llama_sampler_init_greedy());
    } else {
        llama_sampler_chain_add(sampler, llama_sampler_init_top_k(std::max(1, static_cast<int>(topk))));
        llama_sampler_chain_add(sampler, llama_sampler_init_min_p(0.05f, 1));
        llama_sampler_chain_add(sampler, llama_sampler_init_temp(temp));
        llama_sampler_chain_add(sampler, llama_sampler_init_penalties(llama_vocab_n_tokens(h->vocab), 64, 1.10f, 0.0f, 0.0f));
        llama_sampler_chain_add(sampler, llama_sampler_init_dist(LLAMA_DEFAULT_SEED));
    }

    // Optional GBNF grammar that constrains the output format. Each chosen token is first
    // checked alone (cheap); only a rejected token triggers filtering of the whole vocabulary.
    llama_sampler * grammar = nullptr;
    if (jgrammar) {
        const std::string g = js(env, jgrammar);
        if (!g.empty()) {
            grammar = llama_sampler_init_grammar(h->vocab, g.c_str(), "root");
            if (!grammar) LOGE("grammar failed to parse; generating without it");
        }
    }
    const int n_vocab = llama_vocab_n_tokens(h->vocab);
    std::vector<llama_token_data> candidates;

    std::vector<std::string> stops = special_stops();
    if (!raw || !h->has_template) {
        for (const auto & t : turn_stops()) stops.push_back(t);
    }

    std::string output;
    size_t emitted = 0;
    int generated = 0, drafted = 0, accepted = 0;
    long long first_ms = -1;
    int stop_reason = STOP_MAX_TOKENS;
    const auto generation_start = std::chrono::steady_clock::now();

    // Greedy choice at batch index idx, respecting the grammar.
    auto choose = [&](int idx) -> llama_token {
        const float * logits = llama_get_logits_ith(h->ctx, idx);
        llama_token t = argmax_token(logits, n_vocab);
        if (grammar && !grammar_allows(grammar, t)) t = grammar_best(grammar, logits, n_vocab, candidates);
        return t;
    };

    // Appends one chosen token to the answer. False (with stop_reason set) ends generation.
    auto commit = [&](llama_token token) -> bool {
        if (token == LLAMA_TOKEN_NULL) { stop_reason = STOP_EOG; return false; }  // grammar allows nothing more
        if (grammar) llama_sampler_accept(grammar, token);
        if (llama_vocab_is_eog(h->vocab, token)) { stop_reason = STOP_EOG; return false; }
        const size_t before = output.size();
        append_piece(h->vocab, token, output);
        ++generated;
        if (first_ms < 0) {
            first_ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - total_start).count();
        }
        size_t stop_len = 0;
        size_t stop_at = find_stop(output, before, stops, stop_len);
        if (stop_at != std::string::npos) { output.resize(stop_at); stop_reason = STOP_STRING; return false; }
        const size_t safe = output.size() - holdback(output, stops);
        if (safe > emitted) {
            if (!emit(env, sink, on_bytes, output.data() + emitted, safe - emitted)) { stop_reason = STOP_CALLBACK; return false; }
            emitted = safe;
        }
        return true;
    };

    auto limits_hit = [&]() -> bool {
        if (!until_full && generated >= max_tokens) { stop_reason = STOP_MAX_TOKENS; return true; }
        if (h->cancel.load()) { stop_reason = STOP_CANCELLED; return true; }
        if (static_cast<int>(h->kv.size()) >= h->n_ctx) { stop_reason = STOP_CONTEXT_FULL; return true; }
        return false;
    };

    auto decode_failed = [&]() {
        llama_memory_clear(llama_get_memory(h->ctx), true);
        h->kv.clear();
        stop_reason = STOP_DECODE_ERROR;
    };

    if (!draft) {
        while (!limits_hit()) {
            llama_token token;
            if (greedy) {
                token = choose(-1);
            } else {
                // sample() already records the token in the chain (e.g. for penalties).
                token = llama_sampler_sample(sampler, h->ctx, -1);
                if (grammar && !grammar_allows(grammar, token)) {
                    token = grammar_best(grammar, llama_get_logits_ith(h->ctx, -1), n_vocab, candidates);
                }
            }
            if (!commit(token)) break;
            if (!decode_one(h, token)) { decode_failed(); break; }
        }
    } else {
        std::vector<llama_token> drafts;
        llama_batch batch = llama_batch_init(k_draft + 1, 0, 1);
        llama_token cur = choose(-1);
        while (!limits_hit()) {
            if (!commit(cur)) break;
            // 1. Draft proposes up to k tokens following `cur`.
            int k = std::min(k_draft, h->n_ctx - static_cast<int>(h->kv.size()) - 1);
            if (!until_full) k = std::min(k, static_cast<int>(max_tokens) - generated);
            drafts.clear();
            if (k > 0 && sync_kv(draft, h->kv) && decode_one(draft, cur)) {
                for (int j = 0; j < k; ++j) {
                    llama_token d = argmax_token(llama_get_logits_ith(draft->ctx, -1), n_vocab);
                    drafts.push_back(d);
                    if (llama_vocab_is_eog(h->vocab, d) || j + 1 == k || !decode_one(draft, d)) break;
                }
            }
            drafted += static_cast<int>(drafts.size());
            // 2. Target checks `cur` + all drafts in one batch (logits for every position).
            const int base = static_cast<int>(h->kv.size());
            batch.n_tokens = 0;
            for (int i = 0; i <= static_cast<int>(drafts.size()); ++i) {
                const int n = batch.n_tokens++;
                batch.token[n] = i == 0 ? cur : drafts[static_cast<size_t>(i - 1)];
                batch.pos[n] = base + i;
                batch.n_seq_id[n] = 1;
                batch.seq_id[n][0] = 0;
                batch.logits[n] = true;
            }
            if (llama_decode(h->ctx, batch) != 0) { decode_failed(); break; }
            h->kv.push_back(cur);
            h->kv.insert(h->kv.end(), drafts.begin(), drafts.end());
            // 3. Keep drafts while they equal what the target itself would choose.
            size_t ok = 0;
            llama_token next = LLAMA_TOKEN_NULL;
            bool stopped = false;
            for (size_t i = 0; i <= drafts.size(); ++i) {
                const llama_token t = choose(static_cast<int>(i));
                if (i < drafts.size() && t == drafts[i]) {
                    if (limits_hit() || !commit(t)) { stopped = true; ok = i + 1; break; }
                    ++ok;
                    continue;
                }
                next = t;
                break;
            }
            accepted += static_cast<int>(std::min(ok, drafts.size()));
            // 4. Drop the rejected drafts from the target's KV cache.
            const size_t valid = static_cast<size_t>(base) + 1 + std::min(ok, drafts.size());
            if (valid < h->kv.size()) {
                llama_memory_seq_rm(llama_get_memory(h->ctx), 0, static_cast<llama_pos>(valid), -1);
                h->kv.resize(valid);
            }
            if (stopped) break;
            cur = next;
        }
        llama_batch_free(batch);
    }
    llama_sampler_free(sampler);
    if (grammar) llama_sampler_free(grammar);

    // Flush text that was held back but did not turn into a stop marker.
    if (stop_reason != STOP_CALLBACK && output.size() > emitted) {
        emit(env, sink, on_bytes, output.data() + emitted, output.size() - emitted);
    }
    if (env->ExceptionCheck()) return nullptr;

    const auto total_end = std::chrono::steady_clock::now();
    const double generation_seconds = std::chrono::duration<double>(total_end - generation_start).count();
    const double tok_per_sec = generated > 0 && generation_seconds > 0.0 ? generated / generation_seconds : 0.0;
    const long long total_ms = std::chrono::duration_cast<std::chrono::milliseconds>(total_end - total_start).count();

    LOGI("prompt=%d reused=%d generated=%d first_ms=%lld tok_s=%.2f total_ms=%lld stop=%d drafted=%d accepted=%d",
         static_cast<int>(prompt_tokens.size()), reused, generated, first_ms, tok_per_sec, total_ms, stop_reason,
         drafted, accepted);

    return env->NewStringUTF((result_pack(static_cast<int>(prompt_tokens.size()), generated, first_ms,
                                          tok_per_sec, total_ms, stop_reason, reused)
                              + RESULT_SEP + std::to_string(drafted) + RESULT_SEP + std::to_string(accepted)).c_str());
}

JNIEXPORT jint JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeContextSize(JNIEnv *, jobject, jlong hp) {
    auto * h = reinterpret_cast<EngineHandle *>(hp);
    return h ? h->n_ctx : 0;
}

JNIEXPORT void JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeCancel(JNIEnv *, jobject, jlong hp) {
    auto * h = reinterpret_cast<EngineHandle *>(hp);
    if (h) h->cancel = true;
}

JNIEXPORT void JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeReset(JNIEnv *, jobject, jlong hp) {
    auto * h = reinterpret_cast<EngineHandle *>(hp);
    if (!h || !h->ctx) return;
    llama_memory_clear(llama_get_memory(h->ctx), true);
    h->kv.clear();
    h->ckpt_tokens.clear();
    h->ckpt_state.clear();
    h->last_prompt.clear();
}

JNIEXPORT void JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeFree(JNIEnv *, jobject, jlong hp) {
    auto * h = reinterpret_cast<EngineHandle *>(hp);
    if (!h) return;
    if (h->ctx) llama_free(h->ctx);
    if (h->model) llama_model_free(h->model);
    delete h;
}
}
