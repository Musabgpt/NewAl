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
constexpr char FIELD_SEP='\x1F'; constexpr char RECORD_SEP='\x1E';
struct Turn { std::string role; std::string text; };
struct EngineHandle { llama_model* model=nullptr; llama_context* ctx=nullptr; const llama_vocab* vocab=nullptr; std::mt19937 rng{std::random_device{}()}; std::atomic<bool> cancel{false}; };
bool initialized=false;
void init(){if(!initialized){llama_backend_init();initialized=true;}}
void fail(JNIEnv* e,const std::string& m){jclass c=e->FindClass("java/lang/RuntimeException");if(c)e->ThrowNew(c,m.c_str());}
std::string js(JNIEnv* e,jstring s){if(!s)return {};const char* p=e->GetStringUTFChars(s,nullptr);std::string r(p?p:"");e->ReleaseStringUTFChars(s,p);return r;}
std::vector<Turn> parse(const std::string& x){std::vector<Turn> v;size_t p=0;while(p<=x.size()){size_t q=x.find(RECORD_SEP,p);std::string r=q==std::string::npos?x.substr(p):x.substr(p,q-p);if(!r.empty()){size_t s=r.find(FIELD_SEP);if(s!=std::string::npos)v.push_back({r.substr(0,s),r.substr(s+1)});}if(q==std::string::npos)break;p=q+1;}return v;}
std::string fallback(const std::vector<Turn>& v){std::string o;for(auto&t:v){o+=(t.role=="user"?"User: ":"Assistant: ");o+=t.text;o+='\n';}o+="Assistant:";return o;}
std::string templ(llama_model* model,const std::vector<Turn>& turns){if(turns.empty())return {};std::vector<llama_chat_message> m;for(auto&t:turns)m.push_back({t.role.c_str(),t.text.c_str()});std::vector<char>b(4096);int n=llama_chat_apply_template(model,nullptr,m.data(),m.size(),LLAMA_CHAT_TEMPLATE_CHATML,true,b.data(),(int)b.size());if(n>(int)b.size()){b.resize(n);n=llama_chat_apply_template(model,nullptr,m.data(),m.size(),LLAMA_CHAT_TEMPLATE_CHATML,true,b.data(),(int)b.size());}return n>0?std::string(b.data(),n):std::string();}
llama_token sample(const float* l,int n,int k,float temp,std::mt19937&rng){k=std::max(1,std::min(k,n));std::vector<int> ix(n);for(int i=0;i<n;i++)ix[i]=i;std::partial_sort(ix.begin(),ix.begin()+k,ix.end(),[&](int a,int b){return l[a]>l[b];});if(temp<=.01f)return ix[0];std::vector<double>p(k);double mx=l[ix[0]]/temp,sum=0;for(int i=0;i<k;i++){p[i]=std::exp(l[ix[i]]/temp-mx);sum+=p[i];}double x=std::uniform_real_distribution<double>(0,sum)(rng);for(int i=0;i<k;i++){x-=p[i];if(x<=0)return ix[i];}return ix[0];}
}
extern "C" {
JNIEXPORT jlong JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeLoadModel(JNIEnv*e,jobject,jstring jp,jint nc,jint nt){init();std::string path=js(e,jp);auto mp=llama_model_default_params();mp.n_gpu_layers=0;llama_model*m=llama_model_load_from_file(path.c_str(),mp);if(!m){fail(e,"تعذر تحميل ملف GGUF");return 0;}auto cp=llama_context_default_params();cp.n_ctx=std::max(256,(int)nc);cp.n_batch=cp.n_ctx;cp.n_threads=std::max(1,(int)nt);cp.n_threads_batch=cp.n_threads;llama_context*c=llama_init_from_model(m,cp);if(!c){llama_model_free(m);fail(e,"تعذر إنشاء سياق النموذج");return 0;}auto*h=new EngineHandle();h->model=m;h->ctx=c;h->vocab=llama_model_get_vocab(m);return (jlong)h;}
JNIEXPORT jstring JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeGenerate(JNIEnv*e,jobject,jlong hp,jstring jt,jint max,float temp,jint topk){auto*h=(EngineHandle*)hp;if(!h||!h->ctx){fail(e,"المحرك غير محمل");return nullptr;}auto turns=parse(js(e,jt));std::string prompt=templ(h->model,turns);if(prompt.empty())prompt=fallback(turns);int nt=-llama_tokenize(h->vocab,prompt.c_str(),(int)prompt.size(),nullptr,0,true,true);if(nt<=0){fail(e,"فشل ترميز النص");return nullptr;}std::vector<llama_token>tok(nt);if(llama_tokenize(h->vocab,prompt.c_str(),(int)prompt.size(),tok.data(),nt,true,true)<0){fail(e,"فشل ترميز النص");return nullptr;}llama_memory_clear(llama_get_memory(h->ctx),true);h->cancel=false;auto b=llama_batch_get_one(tok.data(),nt);if(llama_decode(h->ctx,b)!=0){fail(e,"فشل تشغيل النموذج");return nullptr;}std::string out;int n=0,vn=llama_vocab_n_tokens(h->vocab);while(n<max&&!h->cancel.load()){llama_token t=sample(llama_get_logits_ith(h->ctx,-1),vn,topk,temp,h->rng);if(llama_vocab_is_eog(h->vocab,t))break;char piece[256];int z=llama_token_to_piece(h->vocab,t,piece,sizeof(piece),0,false);if(z>0)out.append(piece,z);llama_token one[1]={t};auto nb=llama_batch_get_one(one,1);if(llama_decode(h->ctx,nb)!=0)break;n++;}return e->NewStringUTF(out.c_str());}
JNIEXPORT void JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeCancel(JNIEnv*,jobject,jlong hp){auto*h=(EngineHandle*)hp;if(h)h->cancel=true;}
JNIEXPORT void JNICALL Java_com_musab_aragpt2_LlamaEngine_nativeFree(JNIEnv*,jobject,jlong hp){auto*h=(EngineHandle*)hp;if(!h)return;if(h->ctx)llama_free(h->ctx);if(h->model)llama_model_free(h->model);delete h;}
}
