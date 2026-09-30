// The interface in Arabic (right to left). The English strings the interface shows are the keys: text and
// titles are translated as the page draws them (a MutationObserver), never what the model or the user wrote
// (the conversation, code, the terminal, what is typed). NCi18n.apply(lang) at the start: "ar", "en", or "" for
// the system's language (a phone set to Arabic gets Arabic).
(() => {
  const AR = {
    // the page
    "NewAl Code": "NewAl Code",
    "New thread": "محادثة جديدة",
    "Models": "النماذج",
    "Skills, agents & MCP": "المهارات والوكلاء وMCP",
    "Plugins & skills": "الإضافات والمهارات",
    "Sync": "مزامنة",
    "Pull the remote's changes, then push this branch's": "اجلب تغييرات المستودع البعيد ثم ارفع تغييرات هذا الفرع",
    "NewAl's plugins": "إضافات NewAl",
    "built in · no download": "مدمجة · بلا تنزيل",
    "Installed plugins": "الإضافات المثبّتة",
    "⚡ runs at once": "⚡ تعمل فوراً",
    "commands": "أوامر",
    "hooks": "إجراءات تلقائية",
    "Cloud tasks": "مهام سحابية",
    "GitHub": "GitHub",
    "This phone: Termux, screen, files": "هذا الهاتف: Termux والشاشة والملفات",
    "Threads": "المحادثات",
    "Clone from GitHub": "استنساخ من GitHub",
    "Open a project folder": "فتح مجلد مشروع",
    "Settings": "الإعدادات",
    "Sidebar": "القائمة الجانبية",
    "Open": "فتح",
    "Open the folder": "فتح المجلد",
    "Commit": "حفظ التغييرات (Commit)",
    "Terminal (Ctrl `)": "الطرفية (Ctrl `)",
    "Terminal": "الطرفية",
    "Review changes": "مراجعة التغييرات",
    "What should we build?": "شو بدنا نبني؟",
    "Open a project folder, then describe the change. NewAl Code reads, edits, runs and checks it.":
      "افتح مجلد مشروع، ثم صِف التغيير. NewAl Code يقرأ ويعدّل ويشغّل ويتحقق.",
    "Ask NewAl Code anything. @ to add files, / for commands": "اطلب أي شيء من NewAl Code\u200f. اكتب @\u200f لإضافة ملفات، و/\u200f للأوامر",
    "Attach an image": "إرفاق صورة",
    "Speak your request": "قل طلبك بصوتك",
    "Send (Enter)": "إرسال (Enter)",
    "Where a new thread works": "أين تعمل المحادثة الجديدة",
    "Local": "محلي",
    "Worktree": "نسخة عمل (Worktree)",
    "Cloud": "سحابة",
    "In the project folder itself": "في مجلد المشروع نفسه",
    "In a git worktree of the project; apply the changes when they are good": "في نسخة git منفصلة من المشروع؛ طبّق التغييرات عندما تكون جيدة",
    "On GitHub Actions, with the repository: review the diff here, apply it or open a pull request":
      "على GitHub Actions مع المستودع: راجع الفروقات هنا، ثم طبّقها أو افتح طلب دمج",
    "Sandboxed": "معزول",
    "Full access": "وصول كامل",
    "Full access: NewAl Code works without the sandbox and without asking (catastrophic commands are still refused). Click to change.":
      "وصول كامل: يعمل NewAl Code بلا عزل وبلا سؤال (الأوامر المدمّرة تبقى مرفوضة). انقر للتغيير.",
    "Explain this project": "اشرح هذا المشروع",
    "Explain what this project does and how its code is organized.": "اشرح ما يفعله هذا المشروع وكيف تنتظم شيفرته.",
    "Find and fix a bug": "ابحث عن خطأ وأصلحه",
    "Run the tests, find what fails, and fix it.": "شغّل الاختبارات، واعثر على ما يفشل، وأصلحه.",
    "Write tests": "اكتب اختبارات",
    "Add tests for the main module and make sure they pass.": "أضف اختبارات للوحدة الرئيسية وتأكد أنها تنجح.",
    "Create AGENTS.md": "أنشئ AGENTS.md",
    "Create or improve AGENTS.md for this project": "أنشئ أو حسّن AGENTS.md لهذا المشروع",
    // pickers
    "Agent": "وكيل",
    "Agent · full access": "وكيل · وصول كامل",
    "Chat": "محادثة فقط",
    "Ask first": "اسأل أولاً",
    "Reads and explains; changes nothing": "يقرأ ويشرح؛ لا يغيّر شيئاً",
    "Asks before every edit and command": "يسأل قبل كل تعديل وكل أمر",
    "Edits the project and runs commands; asks before risky ones": "يعدّل المشروع ويشغّل الأوامر؛ يسأل قبل الخطِر منها",
    "Never asks (catastrophic commands are still refused)": "لا يسأل أبداً (الأوامر المدمّرة تبقى مرفوضة)",
    "Auto": "تلقائي",
    "Off": "إيقاف",
    "Low": "قليل",
    "Medium": "متوسط",
    "High": "عالٍ",
    "Off for local models, medium for APIs": "إيقاف للنماذج المحلية، متوسط لواجهات API",
    "Fastest: answers directly": "الأسرع: يجيب مباشرة",
    "A short think first": "تفكير قصير أولاً",
    "Thinks before acting": "يفكر قبل أن يتصرف",
    "Thinks as long as needed": "يفكر قدر ما يلزم",
    "How much the model thinks before answering": "كم يفكر النموذج قبل أن يجيب",
    "Reasoning": "التفكير",
    "Manage models…": "إدارة النماذج…",
    "Connect Gemini, DeepSeek…": "ربط Gemini وDeepSeek…",
    "Download, add an API or a server": "تنزيل، أو إضافة API أو خادم",
    "APIs and servers": "واجهات API والخوادم",
    "The best local model for this computer (qwen3.5-9b)": "أفضل نموذج محلي لهذا الجهاز (qwen3.5-9b)",
    "File manager": "مدير الملفات",
    // the terminal
    "Run a command in the project": "شغّل أمراً في المشروع",
    "Clear": "مسح",
    "Close": "إغلاق",
    "The shell": "الصَّدَفة (Shell)",
    "Open a terminal window here, with NewAl Code": "افتح نافذة طرفية هنا مع NewAl Code",
    // review
    "Review": "مراجعة",
    "Commit message": "رسالة الحفظ",
    "Commit and push": "حفظ ورفع (Push)",
    "Commit and create PR": "حفظ وإنشاء طلب دمج",
    "After the commit": "بعد الحفظ",
    "Undo last turn": "تراجع عن آخر دور",
    "Revert the files changed by the last turn": "أرجِع الملفات التي غيّرها آخر دور",
    "Apply to project": "تطبيق على المشروع",
    "Apply this worktree's changes to the project": "طبّق تغييرات نسخة العمل هذه على المشروع",
    "Discard worktree": "تجاهل نسخة العمل",
    "Delete this thread's worktree and branch": "احذف نسخة العمل والفرع الخاصين بهذه المحادثة",
    // the welcome
    "Welcome to NewAl Code": "أهلاً بك في NewAl Code",
    "Set up once, then just ask. All of it can be changed later in Settings.": "اضبطه مرة واحدة، ثم اطلب فقط. يمكن تغيير كل هذا لاحقاً من الإعدادات.",
    "1 · A model": "١ · نموذج",
    "2 · Access": "٢ · الوصول",
    "3 · GitHub": "٣ · GitHub",
    "(optional)": "(اختياري)",
    "4 · Your terminal": "٤ · طرفيتك",
    "A local model runs on this computer, free and offline. Or an API model with your key, in one tap.":
      "نموذج محلي يعمل على هذا الجهاز مجاناً وبلا إنترنت. أو نموذج API بمفتاحك، بلمسة واحدة.",
    "The model this phone's memory fits runs on the phone, offline. An API model (Gemini, DeepSeek…) is much faster and smarter: one tap with your key.":
      "النموذج الذي تتسع له ذاكرة هذا الهاتف يعمل عليه بلا إنترنت. نموذج API (Gemini وDeepSeek…) أسرع وأذكى بكثير: لمسة واحدة بمفتاحك.",
    "One permission: with full access NewAl Code edits, runs commands and works anywhere on this computer without asking each time. Commands that would wipe a drive or your home folder are always refused.":
      "إذن واحد: مع الوصول الكامل يعدّل NewAl Code ويشغّل الأوامر ويعمل في أي مكان على هذا الجهاز دون أن يسأل كل مرة. الأوامر التي تمسح قرصاً أو مجلدك الشخصي مرفوضة دائماً.",
    "One permission: with full access NewAl Code edits, runs commands (bash and PowerShell) and works anywhere on this computer without asking each time. Commands that would wipe a drive or your home folder are always refused.":
      "إذن واحد: مع الوصول الكامل يعدّل NewAl Code ويشغّل الأوامر (bash وPowerShell) ويعمل في أي مكان على هذا الجهاز دون أن يسأل كل مرة. الأوامر التي تمسح قرصاً أو مجلدك الشخصي مرفوضة دائماً.",
    "One permission: with full access NewAl Code edits, runs commands, uses the phone (apps, screen, files) and Termux without asking each time. Commands that would wipe a drive or your home folder are always refused. Android then asks for its own permissions, one after another: files, screen control, notifications and Termux.":
      "إذن واحد: مع الوصول الكامل يعدّل NewAl Code ويشغّل الأوامر ويستخدم الهاتف (التطبيقات والشاشة والملفات) وTermux دون أن يسأل كل مرة. الأوامر التي تمسح قرصاً أو مجلدك الشخصي مرفوضة دائماً. بعدها يطلب أندرويد أذوناته واحداً تلو الآخر: الملفات، التحكم بالشاشة، الإشعارات وTermux.",
    "Give full access": "امنح وصولاً كاملاً",
    "Ask me first": "اسألني أولاً",
    "Ask me first again": "عُد لتسألني أولاً",
    "✓ Full access": "✓ وصول كامل",
    "NewAl Code will ask first": "سيسأل NewAl Code أولاً",
    "Add them": "أضِفها",
    "✓ Added": "✓ أُضيفت",
    "(new terminals find newal)": "(الطرفيات الجديدة تجد newal)",
    "Start": "ابدأ",
    "for this computer": "لهذا الجهاز",
    "for this phone": "لهذا الهاتف",
    "Set up again (model, access, GitHub, terminal)": "الإعداد من جديد (النموذج، الوصول، GitHub، الطرفية)",
    // models
    "An API in one tap": "API بلمسة واحدة",
    "Copy your key (Gemini, DeepSeek…) and tap its name; without a key copied, its key page opens and NewAl Code connects when you come back with it.":
      "انسخ مفتاحك (Gemini وDeepSeek…) والمس اسمه؛ إن لم يكن منسوخاً تُفتح صفحة المفاتيح، ويتصل NewAl Code حين تعود والمفتاح منسوخ.",
    "Your GGUF files": "ملفات GGUF الخاصة بك",
    "Let NewAl Code read the phone's files": "اسمح لـ NewAl Code بقراءة ملفات الهاتف",
    "Pick a GGUF file…": "اختر ملف GGUF…",
    "Copy a GGUF into the app…": "انسخ ملف GGUF إلى التطبيق…",
    "Pick a GGUF file": "اختر ملف GGUF",
    "None found yet. Once NewAl Code may read the phone's files, the GGUF files in Download, Documents (and other folders) appear here; or pick one, or copy one into the app.":
      "لا شيء بعد. حين يُسمح لـ NewAl Code بقراءة ملفات الهاتف تظهر هنا ملفات GGUF في التنزيلات والمستندات (ومجلدات أخرى)؛ أو اختر ملفاً، أو انسخه إلى التطبيق.",
    "This computer": "هذا الجهاز",
    "This phone": "هذا الهاتف",
    "Local models (llama.cpp, free, offline)": "نماذج محلية (llama.cpp، مجانية، بلا إنترنت)",
    "Other models": "نماذج أخرى",
    "Roles: which model does what": "الأدوار: أي نموذج يفعل ماذا",
    "Save roles": "حفظ الأدوار",
    "Sub-agents use them: explore → fast, reviewer → review, /plan → plan. Empty = the thread's model.":
      "يستخدمها الوكلاء الفرعيون: explore ← fast، reviewer ← review، ‏/plan ← plan. فارغ = نموذج المحادثة.",
    "Add an API or a server": "إضافة API أو خادم",
    "Add model": "إضافة نموذج",
    "OpenAI-compatible": "متوافق مع OpenAI",
    "Local GGUF file": "ملف GGUF محلي",
    "Download": "تنزيل",
    "Use": "استخدم",
    "ready": "جاهز",
    "recommended here": "المقترح هنا",
    "needs more RAM": "يحتاج ذاكرة أكبر",
    "in use": "قيد الاستخدام",
    "connected": "متصل",
    "id, e.g. my-gpu-box": "المعرّف، مثل my-gpu-box",
    "base URL, e.g. http://192.168.1.20:8080/v1": "العنوان الأساسي، مثل http://192.168.1.20:8080/v1",
    "model name (or GGUF path)": "اسم النموذج (أو مسار GGUF)",
    "API key environment variable, e.g. OPENROUTER_API_KEY": "متغير البيئة للمفتاح، مثل OPENROUTER_API_KEY",
    "context tokens (optional)": "حجم السياق بالرموز (اختياري)",
    "Or type any provider/model in /model, e.g. ollama/qwen3-coder:30b, openrouter/qwen/qwen3-coder, anthropic/claude-sonnet-4-5.":
      "أو اكتب أي مزود/نموذج في ‎/model، مثل ollama/qwen3-coder:30b أو openrouter/qwen/qwen3-coder أو anthropic/claude-sonnet-4-5.",
    "For 2-3 GB phones (NewAl Code Lite): simple edits, explanations and small scripts in about 0.8-1 GB of RAM.":
      "لهواتف 2-3 غيغابايت (NewAl Code Lite): تعديلات بسيطة وشروح وسكربتات صغيرة بنحو 0.8-1 غيغابايت من الذاكرة.",
    "For 2 GB phones that cannot fit the 4-bit 0.8B: short edits and small scripts in about 0.7 GB (it follows instructions less closely).":
      "لهواتف 2 غيغابايت التي لا تتسع لنسخة 0.8B رباعية البت: تعديلات قصيرة وسكربتات صغيرة بنحو 0.7 غيغابايت (يتبع التعليمات بدقة أقل).",
    "Phones with 4 GB and the smallest computers; on bigger ones, the quick helper for titles and summaries.":
      "هواتف 4 غيغابايت وأصغر الحواسيب؛ وعلى الأكبر منها مساعد سريع للعناوين والملخصات.",
    "The 8 GB pick: a real coding agent (tool calls, edits, tests) in about 4.5 GB of RAM with a 32k context.":
      "خيار 8 غيغابايت: وكيل برمجة حقيقي (أدوات وتعديلات واختبارات) بنحو 4.5 غيغابايت من الذاكرة وسياق 32 ألفاً.",
    "Stronger than 4B; the 12 GB and 16 GB pick (about 7.5 GB of RAM with a 32k context).":
      "أقوى من 4B؛ خيار 12 و16 غيغابايت (نحو 7.5 غيغابايت من الذاكرة بسياق 32 ألفاً).",
    // extensions
    "Plugins": "الإضافات",
    "Install": "تثبيت",
    "Remove": "إزالة",
    "Add marketplace": "إضافة سوق",
    "A plugin: git URL, owner/repo or name@marketplace": "إضافة: رابط git أو owner/repo أو name@marketplace",
    "A plugin marketplace: owner/repo or git URL": "سوق إضافات: owner/repo أو رابط git",
    "Instructions (AGENTS.md / CLAUDE.md)": "التعليمات (AGENTS.md / CLAUDE.md)",
    "Skills": "المهارات",
    "Sub-agents": "الوكلاء الفرعيون",
    "Slash commands": "أوامر الشرطة المائلة",
    "MCP servers": "خوادم MCP",
    "Hooks": "الخطافات (Hooks)",
    "None.": "لا شيء.",
    "installed": "مثبّتة",
    "Fast read-only exploration: finds where things are and how they work, and reports files and line numbers.":
      "استكشاف سريع للقراءة فقط: يجد أين توجد الأشياء وكيف تعمل، ويذكر الملفات وأرقام الأسطر.",
    "General sub-task in its own context (a separate change, a focused fix).": "مهمة فرعية عامة في سياقها الخاص (تغيير منفصل، إصلاح محدد).",
    "Reviews a change for bugs, then reports findings (it does not edit).": "يراجع تغييراً بحثاً عن الأخطاء ثم يبلّغ بما وجد (لا يعدّل).",
    // cloud
    "No cloud tasks yet: choose Cloud under the message box, then send a task.": "لا مهام سحابية بعد: اختر «سحابة» تحت مربع الرسالة، ثم أرسل مهمة.",
    "Tasks run on GitHub Actions with the repository (newal-code cloud \"task\" does the same).":
      "تعمل المهام على GitHub Actions مع المستودع (الأمر newal-code cloud \"task\" يفعل الشيء نفسه).",
    // GitHub
    "Connect GitHub": "ربط GitHub",
    "Connected": "متصل",
    "Clone a repository": "استنساخ مستودع",
    "Disconnect": "قطع الاتصال",
    "Search your repositories": "ابحث في مستودعاتك",
    "Connected, NewAl Code lists your repositories to clone as projects, pushes with git (on a phone too) and opens pull requests from Commit.":
      "بعد الربط يعرض NewAl Code مستودعاتك لاستنساخها كمشاريع، ويرفع بـ git (على الهاتف أيضاً)، ويفتح طلبات الدمج من زر الحفظ.",
    "Uses this computer's GitHub login (GitHub CLI or Git Credential Manager, which signs in through the browser); else a token.":
      "يستخدم تسجيل دخول GitHub الموجود على هذا الجهاز (GitHub CLI أو Git Credential Manager الذي يسجّل الدخول عبر المتصفح)؛ وإلا فرمز وصول.",
    "Copy a token and tap (without one, GitHub's page for a token opens with the scopes NewAl Code needs).":
      "انسخ رمز وصول والمس (بدونه تُفتح صفحة GitHub لإنشاء رمز بالصلاحيات التي يحتاجها NewAl Code).",
    // settings
    "Access": "الوصول",
    "Edits and commands in the project; NewAl Code asks before anything outside it.": "تعديلات وأوامر داخل المشروع؛ ويسأل NewAl Code قبل أي شيء خارجه.",
    "Full access: new threads work without the sandbox and without asking. Commands that would wipe a drive or your home folder are still refused.":
      "وصول كامل: المحادثات الجديدة تعمل بلا عزل وبلا سؤال. الأوامر التي تمسح قرصاً أو مجلدك الشخصي تبقى مرفوضة.",
    "Default permission mode": "وضع الأذونات الافتراضي",
    "Check changes with the tests": "افحص التغييرات بالاختبارات",
    "Read files the request names": "اقرأ الملفات التي يذكرها الطلب",
    "Web fetch tool": "أداة جلب صفحات الويب",
    "Speculative decoding": "التوليد التخميني",
    "Theme": "المظهر",
    "Language": "اللغة",
    "system": "النظام",
    "light": "فاتح",
    "dark": "داكن",
    "Save": "حفظ",
    "Saved": "تم الحفظ",
    "newal in every terminal": "newal في كل طرفية",
    "Explorer's right-click menu": "قائمة الزر الأيمن في المستكشف",
    "Windows Terminal profile": "ملف تعريف في Windows Terminal",
    "Open with NewAl Code · NewAl Code terminal here": "فتح باستخدام NewAl Code · طرفية NewAl Code هنا",
    "Shells": "الصَّدَفات",
    "Open a terminal here": "افتح طرفية هنا",
    "Add": "إضافة",
    "on": "مفعّل",
    "off": "متوقف",
    // the phone
    "Screen control": "التحكم بالشاشة",
    "Turn on": "تفعيل",
    "App info": "معلومات التطبيق",
    "Android 13 and up: if the switch is greyed out (\"Restricted setting\"), open App info, tap ⋮ at the top, \"Allow restricted settings\", then turn it on.":
      "أندرويد 13 وأحدث: إن كان المفتاح رمادياً («إعداد محظور»)، افتح معلومات التطبيق، والمس ⋮ في الأعلى، ثم «السماح بالإعدادات المحظورة»، ثم فعّله.",
    "Termux": "Termux",
    "Get Termux": "احصل على Termux",
    "Connect Termux": "ربط Termux",
    "Let this app start it": "اسمح لهذا التطبيق بتشغيله",
    "Open the Termux workspace": "فتح مساحة عمل Termux",
    "Start in Termux": "تشغيل في Termux",
    "Back to the app's workspace": "العودة إلى مساحة عمل التطبيق",
    "Connected, NewAl Code also runs inside Termux: its whole Linux (git, compilers, packages), your projects there, this phone's model and screen control. The first time, paste one command in Termux.":
      "بعد الربط يعمل NewAl Code داخل Termux أيضاً: لينكس كامل (git والمترجمات والحزم) ومشاريعك هناك، مع نموذج الهاتف والتحكم بالشاشة. في المرة الأولى الصق أمراً واحداً في Termux.",
    "The phone's files": "ملفات الهاتف",
    "not readable": "غير مقروءة",
    "Allow": "سماح",
    "not installed": "غير مثبّت",
    // folders
    "Go": "اذهب",
    "Home": "المنزل",
    "Phone storage": "ذاكرة الهاتف",
    "Downloads": "التنزيلات",
    "Recent": "الأخيرة",
    "Make it": "أنشئه",
    "Open projects": "المشاريع المفتوحة",
    "Open with NewAl Code": "فتح باستخدام NewAl Code",
    // slash commands' descriptions
    "Start a new thread in this project": "ابدأ محادثة جديدة في هذا المشروع",
    "Clear the conversation (same as /new)": "امسح المحادثة (مثل ‎/new)",
    "Summarize the conversation to free context": "لخّص المحادثة لتحرير السياق",
    "Show or switch the model": "اعرض النموذج أو بدّله",
    "Show or switch the permission mode (also /approvals, /permissions)": "اعرض وضع الأذونات أو بدّله (أيضاً ‎/approvals و‎/permissions)",
    "Show the changes made in this thread": "اعرض التغييرات في هذه المحادثة",
    "Revert the last turn's changes": "أرجِع تغييرات الدور الأخير",
    "Review the changes for bugs (a reviewer sub-agent)": "راجع التغييرات بحثاً عن أخطاء (وكيل مراجعة فرعي)",
    "Review the changes for security problems (a reviewer sub-agent)": "راجع التغييرات بحثاً عن مشاكل أمنية (وكيل مراجعة فرعي)",
    "Plan without changing anything (read-only for one turn)": "خطّط دون تغيير أي شيء (قراءة فقط لدور واحد)",
    "Keep working until the condition holds (checked after every answer)": "واصل العمل حتى يتحقق الشرط (يُفحص بعد كل إجابة)",
    "Model, mode, context use, speed and this computer": "النموذج والوضع واستخدام السياق والسرعة وهذا الجهاز",
    "Tokens read and written in this thread": "الرموز المقروءة والمكتوبة في هذه المحادثة",
    "List threads, or reopen one": "اعرض المحادثات أو أعد فتح إحداها",
    "Rename this thread": "أعد تسمية هذه المحادثة",
    "Save this thread as Markdown in the project": "احفظ هذه المحادثة بصيغة Markdown في المشروع",
    "Show the commands": "اعرض الأوامر",
    "Let this thread read and edit another folder too": "اسمح لهذه المحادثة بقراءة وتعديل مجلد آخر أيضاً",
    "List the sub-agents": "اعرض الوكلاء الفرعيين",
    "List the skills": "اعرض المهارات",
    "List the MCP servers and their state": "اعرض خوادم MCP وحالتها",
    "List the configured hooks": "اعرض الخطافات المضبوطة",
    "Show the instruction files (# <note> adds a note to AGENTS.md)": "اعرض ملفات التعليمات (‎# <ملاحظة> تضيف ملاحظة إلى AGENTS.md)",
    "Show the permission mode and the allow / ask / deny rules": "اعرض وضع الأذونات وقواعد السماح / السؤال / الرفض",
    "Plugins and plugin marketplaces (Claude Code's formats)": "الإضافات وأسواق الإضافات (بصيغ Claude Code)",
    "Create or improve AGENTS.md for this project ": "أنشئ أو حسّن AGENTS.md لهذا المشروع",
    // what the agent did (a turn's items)
    "Ran": "شغّل",
    "Ran in PowerShell": "شغّل في PowerShell",
    "Edited": "عدّل",
    "Wrote": "كتب",
    "Patched": "رقّع",
    "Fetched": "جلب",
    "Searched the web": "بحث في الويب",
    "Delegated to": "أوكل إلى",
    "Updated plan": "حدّث الخطة",
    "Used the phone": "استخدم الهاتف",
    "Edited notebook": "عدّل الدفتر",
    "Called": "استدعى",
    "Explored": "استكشف",
    "Thinking": "يفكر",
    "Working": "يعمل",
    // check everything
    "Check everything": "افحص كل شيء",
    "Checking…": "جارٍ الفحص…",
    "Check again": "افحص مجدداً",
    "Copy report": "انسخ التقرير",
    "Copied: paste it where you ask for help": "نُسخ: الصقه حيث تطلب المساعدة",
    "Connect an API": "اربط API",
    "Open This phone": "افتح «هذا الهاتف»",
    "Fix": "أصلح",
    "Memory": "الذاكرة",
    "Free space": "المساحة الفارغة",
    "Shell": "الصَّدَفة (Shell)",
    "Internet": "الإنترنت",
    "Notifications": "الإشعارات",
    "llama.cpp (local models)": "llama.cpp (النماذج المحلية)",
    // approvals and the conversation's controls
    "Allow once": "اسمح مرة",
    "Always allow": "اسمح دائماً",
    "Deny": "ارفض",
    "Stop": "إيقاف",
    "Implement this plan": "نفّذ هذه الخطة",
    "Copy": "نسخ",
    "Delete": "حذف",
    "Delete this thread?": "حذف هذه المحادثة؟",
  };
  // Strings with a part that changes: [pattern, Arabic with $1...].
  const PATTERNS = [
    [/^New thread in (.+)$/, "محادثة جديدة في $1"],
    [/^What should we build in (.+)\?$/, "شو بدنا نبني في $1؟"],
    [/^Skills, agents & MCP · (.+)$/, "المهارات والوكلاء وMCP · $1"],
    [/^Plugins & skills · (.+)$/, "الإضافات والمهارات · $1"],
    [/^Running \/(.+)…$/, "جارٍ تشغيل /$1…"],
    [/^Installing (.+)…$/, "جارٍ تثبيت $1…"],
    [/^New folder in (.+)$/, "مجلد جديد في $1"],
    [/^Worked for ([\d.]+s|[\dm ]+s?)( · (\d+) steps)?( · interrupted)?$/, (m, t, _a, n, x) =>
      "عمل لمدة " + t + (n ? " · " + n + " خطوات" : "") + (x ? " · أُوقف" : "")],
    [/^(\d+)% context used$/, "استُخدم $1% من السياق"],
    [/^(\d+) of (\d+) tokens$/, "$1 من $2 رمزاً"],
    [/^((?:[\d.]+ tok\/s · )?)read (\d+)( \(\+(\d+) cached\))?( · wrote (\d+))?$/, (m, tps, r, _c, c, _w, w) =>
      (tps ? tps.replace("tok/s", "رمز/ث") : "") + "قرأ " + r + (c ? " (+" + c + " من الذاكرة)" : "") + (w ? " · كتب " + w : "")],
    [/^(\d+) steps$/, "$1 خطوات"],
    [/^now$/, "الآن"],
    [/^(\d+)m$/, "$1 د"],
    [/^(\d+)h$/, "$1 س"],
    [/^(\d+)d$/, "$1 ي"],
    [/^(\d+) cores · ([\d.]+) GB RAM · (\w+) tier$/, "$1 أنوية · $2 غيغابايت ذاكرة · فئة $3"],
    [/^(.+) · tap: your key from the clipboard, or its key page$/, "$1 · المس: مفتاحك من الحافظة، أو صفحة مفاتيحه"],
    [/^Connected as @(.+)$/, "متصل باسم ‎@$1"],
    [/^Using (.+) \(a local model\)$/, "يُستخدم الآن $1 (نموذج محلي)"],
    [/^Copying (.+)… (.+)$/, "نسخ $1… $2"],
    [/^Full access is on\. Still off on the phone: (.+) \(This phone, in the menu\)$/, "الوصول الكامل مفعّل. ما زال متوقفاً على الهاتف: $1 (هذا الهاتف، في القائمة)"],
    [/^Full access: everything on the phone is on$/, "وصول كامل: كل شيء على الهاتف مفعّل"],
    [/^Full access: NewAl Code works without asking \(commands that would wipe a drive or your home folder are still refused\)$/,
      "وصول كامل: يعمل NewAl Code دون سؤال (الأوامر التي تمسح قرصاً أو مجلدك الشخصي تبقى مرفوضة)"],
    [/^NewAl Code asks again before anything outside the project$/, "سيسأل NewAl Code مجدداً قبل أي شيء خارج المشروع"],
    [/^Shared with NewAl Code: say what to do with it$/, "شورك مع NewAl Code: قل ماذا تفعل به"],
  ];
  // Never translated: what the model and the user wrote, code, commands and their output. The interface's own parts
  // in the conversation (a turn's footer, the buttons) are. Placeholders and titles of inputs are too, not their text.
  const SKIP = ".msg-assistant, .bubble, .item-body, .what, #term-out, pre, code, textarea, input, .md, .diff, .cmd, [data-no-i18n]";
  const SKIP_ATTR = ".msg-assistant, .bubble, .item-body, pre, code, .md, .diff, [data-no-i18n]";
  let on = false;

  function tr(s) {
    const t = s.trim();
    if (!t) return null;
    if (Object.prototype.hasOwnProperty.call(AR, t)) return s.replace(t, AR[t]);
    for (const [re, rep] of PATTERNS) if (re.test(t)) return s.replace(t, t.replace(re, rep));   // (rep: text or a function)
    return null;
  }
  function node(n) {
    if (n.nodeType === 3) {
      const p = n.parentElement;
      if (!p || p.closest(SKIP)) return;
      const r = tr(n.nodeValue);
      if (r !== null && r !== n.nodeValue) n.nodeValue = r;
      return;
    }
    if (n.nodeType !== 1) return;
    if (!n.closest(SKIP_ATTR)) {
      for (const a of ["title", "placeholder", "aria-label"]) {
        const v = n.getAttribute(a);
        if (v) { const r = tr(v); if (r !== null && r !== v) n.setAttribute(a, r); }
      }
    }
    if (!n.closest(SKIP)) n.childNodes.forEach(node);
  }
  function wanted(lang) {
    if (lang === "ar" || lang === "en") return lang;
    return /^ar\b/i.test(navigator.language || "") ? "ar" : "en";
  }
  function apply(lang) {
    on = wanted(lang) === "ar";
    document.documentElement.lang = on ? "ar" : "en";
    document.documentElement.dir = on ? "rtl" : "ltr";
    if (!on) return;
    node(document.body);
    new MutationObserver(ms => ms.forEach(m => {
      if (m.type === "childList") m.addedNodes.forEach(node);
      else node(m.target);
    })).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true,
      attributeFilter: ["title", "placeholder", "aria-label"] });
  }
  // (toasts and other text the code writes itself: the same dictionary)
  window.NCi18n = { apply, active: () => on, t: s => (on && tr(s)) || s, wanted };
})();
