# NewAl لويندوز

مساعد محلي مجاني على جهازك: يبرمج ويجرّب الكود ويصلحه، يبحث بالنت، يشغّل PowerShell، يتذكر مشروعك،
ومربوط بـ GitHub وGitLab وGoogle Drive وKaggle وVS Code.

## التثبيت
1. من GitHub ← Actions ← «Build NewAl for Windows» ← آخر تشغيل أخضر ← نزّل `NewAl-Windows-Setup`.
2. فك الضغط وشغّل `NewAl-Setup.exe` (لا يحتاج صلاحيات مدير). لو ظهر تحذير SmartScreen: «مزيد من المعلومات» ← «تشغيل على أي حال».
3. افتح NewAl ← 🧠 النماذج ← «تنزيل الكل» (~16.7 GB، يكمل من حيث توقف لو انقطع النت).

## النماذج
| الدور | النموذج | الحجم |
|---|---|---|
| 🧠 العقل والمبرمج: يخطط، يبرمج، يجرّب، يصلح ويحكم | Qwen3.6-35B-A3B (UD-IQ3_S، MoE 3B نشط) | 13.7 GB |
| 🛠 مساعد: الأدوات والنت والمحادثة السريعة | LFM2.5-2.6B | 1.6 GB |
| 🧭 مساعد: التوجيه وكلمات البحث | LFM2.5-1.2B | 0.7 GB |
| 🔎 مساعد: فهرسة المشروع والذاكرة | Qwen3-Embedding 0.6B + Qwen3-Reranker 0.6B | 1.3 GB |
| (اختياري) حكم احتياطي | Qwen3.5-4B | 2.7 GB |

## البرمجة
النموذج يكتب الكود ← NewAl يشغّله (بعد موافقتك) ← Qwen3.5 يحكم على النتيجة ← إذا فشل يحوّل الخطأ لبرومت إصلاح
← نموذج البرمجة يصلح (حتى مرتين).

## VS Code
ثبّت إضافة **Continue** وأضف نموذجاً من نوع OpenAI:
`apiBase: http://127.0.0.1:8766/v1` و `model: newal-auto` (أو `newal-coder`). يبقى NewAl مفتوحاً.

## «تحديث» (مؤجل، البنية جاهزة)
- كل جواب يُحفظ في `%USERPROFILE%\NewAl\training\<model>.jsonl` مع نتيجة الطرفية وحكم Qwen3.5 وتقييمك.
- الأمثلة الناجحة تُستخدم فوراً كأمثلة للنماذج.
- «تجهيز بيانات التدريب» يصدّر `<model>.sft.jsonl` جاهزاً لـ LoRA.
- عند وضع `adapters\<role>.gguf` يحمّله المحرك تلقائياً مع النموذج.

## التطوير
```
cd desktop
pip install pywebview pypdf numpy
set NEWAL_BIN=C:\path\to\llama.cpp   (مجلد llama-server.exe)
python NewAl.py            (أو --browser لفتحه بالمتصفح)
python -m unittest discover -s tests
```
