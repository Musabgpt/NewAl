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
bool decode_prompt(EngineHandle * h, const std::vector<llama_token> & tokens, int & reused) {
    size_t common = 0;
    while (common < h->kv.size() && common < tokens.size() && h->kv[common] == tokens[common]) ++common;
    // The last prompt token must be decoded again to get fresh logits for sampling.
    if (common == tokens.size() && common > 0) --common;
    auto * mem = llama_get_memory(h->ctx);
    if (common == 0 || !llama_memory_seq_rm(mem, 0, static_cast<llama_pos>(common), -1)) {
        llama_memory_clear(mem, true);
        common = 0;
    }
    h->kv.resize(common);
    reused = static_cast<int>(common);
    for (size_t i = common; i < tokens.size(); i += static_cast<size_t>(h->n_batch)) {
        const int n = static_cast<int>(std::min(tokens.size() - i, static_cast<size_t>(h->n_batch)));
        auto batch = llama_batch_get_one(const_cast<llama_token *>(tokens.data() + i), n);
        if (llama_decode(h->ctx, batch) != 0) {
            llama_memory_clear(mem, true);
            h->kv.clear();
            return false;
        }
        h->kv.insert(h->kv.end(), tokens.begin() + static_cast<long>(i), tokens.begin() + static_cast<long>(i) + n);
    }
    return true;
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
    LOGI("model loaded; context=%d batch=%d threads=%d kv=%s chat_template=%s",
         handle->n_ctx, handle->n_batch, static_cast<int>(cp.n_threads),
         cp.type_k == GGML_TYPE_Q8_0 ? "q8_0" : "f16", handle->has_template ? "yes" : "no");
    return reinterpret_cast<jlong>(handle);
}

// Generates a reply. Text streams to sink.onBytes(byte[]) as raw UTF-8 (a multi-byte
// character may be split across calls; Java decodes incrementally). Returns only metrics.
// max_tokens <= 0 means: until end of turn or the context is full.
JNIEXPORT jstring JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeGenerate(
        JNIEnv * env, jobject, jlong hp, jstring jt, jint max_tokens, jfloat temp,
        jint topk, jint flags, jstring jgrammar, jobject sink) {
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
    LOGI("prompt tokens=%d reused=%d budget=%d", static_cast<int>(prompt_tokens.size()), reused, max_prompt_tokens);

    auto sp = llama_sampler_chain_default_params();
    sp.no_perf = true;
    llama_sampler * sampler = llama_sampler_chain_init(sp);
    if (!sampler) { fail(env, "تعذر إنشاء sampler"); return nullptr; }

    if (temp <= 0.01f) {
        llama_sampler_chain_add(sampler, llama_sampler_init_greedy());
    } else {
        llama_sampler_chain_add(sampler, llama_sampler_init_top_k(std::max(1, static_cast<int>(topk))));
        llama_sampler_chain_add(sampler, llama_sampler_init_min_p(0.05f, 1));
        llama_sampler_chain_add(sampler, llama_sampler_init_temp(temp));
        llama_sampler_chain_add(sampler, llama_sampler_init_penalties(llama_vocab_n_tokens(h->vocab), 64, 1.10f, 0.0f, 0.0f));
        llama_sampler_chain_add(sampler, llama_sampler_init_dist(LLAMA_DEFAULT_SEED));
    }

    // Optional GBNF grammar that constrains the output format. Each sampled token is first
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
    int generated = 0;
    long long first_ms = -1;
    int stop_reason = STOP_MAX_TOKENS;
    const auto generation_start = std::chrono::steady_clock::now();

    while (true) {
        if (!until_full && generated >= max_tokens) { stop_reason = STOP_MAX_TOKENS; break; }
        if (h->cancel.load()) { stop_reason = STOP_CANCELLED; break; }
        if (static_cast<int>(h->kv.size()) >= h->n_ctx) { stop_reason = STOP_CONTEXT_FULL; break; }

        // llama_sampler_sample() already records the token in the chain (e.g. for penalties).
        llama_token token = llama_sampler_sample(sampler, h->ctx, -1);
        if (grammar) {
            llama_token_data single = {token, 1.0f, 0.0f};
            llama_token_data_array one = {&single, 1, -1, false};
            llama_sampler_apply(grammar, &one);
            if (std::isinf(single.logit) && single.logit < 0) {
                const float * logits = llama_get_logits_ith(h->ctx, -1);
                candidates.resize(static_cast<size_t>(n_vocab));
                for (llama_token id = 0; id < n_vocab; ++id) candidates[static_cast<size_t>(id)] = {id, logits[id], 0.0f};
                llama_token_data_array all = {candidates.data(), candidates.size(), -1, false};
                llama_sampler_apply(grammar, &all);
                llama_token best = LLAMA_TOKEN_NULL;
                float best_logit = -INFINITY;
                for (size_t i = 0; i < all.size; ++i) {
                    if (all.data[i].logit > best_logit) { best_logit = all.data[i].logit; best = all.data[i].id; }
                }
                if (best == LLAMA_TOKEN_NULL) { stop_reason = STOP_EOG; break; }  // grammar allows nothing more
                token = best;
            }
            llama_sampler_accept(grammar, token);
        }
        if (llama_vocab_is_eog(h->vocab, token)) { stop_reason = STOP_EOG; break; }

        const size_t before = output.size();
        append_piece(h->vocab, token, output);
        ++generated;
        if (first_ms < 0) {
            first_ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - total_start).count();
        }

        size_t stop_len = 0;
        size_t stop_at = find_stop(output, before, stops, stop_len);
        if (stop_at != std::string::npos) {
            output.resize(stop_at);
            stop_reason = STOP_STRING;
            break;
        }
        const size_t safe = output.size() - holdback(output, stops);
        if (safe > emitted) {
            if (!emit(env, sink, on_bytes, output.data() + emitted, safe - emitted)) { stop_reason = STOP_CALLBACK; break; }
            emitted = safe;
        }

        llama_token one[1] = {token};
        auto next = llama_batch_get_one(one, 1);
        if (llama_decode(h->ctx, next) != 0) {
            llama_memory_clear(llama_get_memory(h->ctx), true);
            h->kv.clear();
            stop_reason = STOP_DECODE_ERROR;
            break;
        }
        h->kv.push_back(token);
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

    LOGI("prompt=%d reused=%d generated=%d first_ms=%lld tok_s=%.2f total_ms=%lld stop=%d",
         static_cast<int>(prompt_tokens.size()), reused, generated, first_ms, tok_per_sec, total_ms, stop_reason);

    return env->NewStringUTF(result_pack(static_cast<int>(prompt_tokens.size()), generated, first_ms,
                                         tok_per_sec, total_ms, stop_reason, reused).c_str());
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
}

JNIEXPORT void JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeFree(JNIEnv *, jobject, jlong hp) {
    auto * h = reinterpret_cast<EngineHandle *>(hp);
    if (!h) return;
    if (h->ctx) llama_free(h->ctx);
    if (h->model) llama_model_free(h->model);
    delete h;
}
}
