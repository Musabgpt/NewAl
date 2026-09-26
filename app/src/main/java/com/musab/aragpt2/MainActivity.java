package com.musab.aragpt2;

import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.TextUtils;
import android.view.View;
import android.view.Window;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;
import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

public class MainActivity extends AppCompatActivity {
    private static final String PREFS="h33_prefs", PREF_MODEL_URI="model_uri", PREF_MODEL_NAME="model_name", PREF_MODEL_LOCAL_PATH="model_local_path", PREF_MODEL_SIZE="model_local_size";
    private static final String PREF_AGENT_MODE="agent_mode", PREF_AGENT_TOKEN="agent_token", PREF_AGENT_PROFILE="agent_profile",
            PREF_MAX_ATTEMPTS="max_attempts", PREF_TIMEOUT_S="exec_timeout_s", PREF_GRAMMAR="grammar", PREF_AUTO_TEST="auto_test",
            PREF_ROLE="role_path_", PREF_ORCHESTRATE="orchestrate", PREF_LANG_SUMMARY="lang_summary",
            PREF_SPECULATIVE="speculative", PREF_DRAFT="draft_path",
            PREF_ASSISTANT="assistant_mode", PREF_CONFIRM_SENDS="confirm_sends", PREF_SPEAK="speak_replies",
            PREF_WEB="web_search", PREF_THINK="think", PREF_ONLINE="online_tools", PREF_CONTEXT="context_tokens";
    /** Draft tokens per step; measured best on CPU for a 3B model with a 0.5B draft. */
    private static final int DRAFT_TOKENS=3;
    /** Model roles: each may use its own GGUF; models are loaded one at a time (sequentially). */
    private static final String ROLE_MANAGER="manager", ROLE_CODER="coder", ROLE_LANGUAGE="language";
    private static final int CONTEXT_TOKENS=4096, MAX_NEW_TOKENS=2048, TOP_K=40, AGENT_PORT=47811, REQ_TERMUX=41, REQ_NOTIFY=42, REQ_ASSIST=43, REQ_ALL=44;
    private static final float TEMPERATURE=0.70f;

    private final ExecutorService executor=Executors.newSingleThreadExecutor();
    private final Handler ui=new Handler(Looper.getMainLooper());
    private final LiveBubble live=new LiveBubble();
    private volatile TermuxBridge bridge;
    private volatile AgentLoop agentLoop;
    private ProjectWorkspace workspace;
    private boolean agentMode;
    private volatile String loadedPath;
    /** Several models stay loaded together while RAM allows (manager, coder, language, draft). */
    private ModelPool<LlamaEngine> pool;
    private DeviceController device;
    /** Phone assistant: everyday commands without a model, anything else by the screen agent. */
    private PhoneAssistant assistant;
    private boolean assistantMode;
    private volatile boolean resumed;
    private android.speech.tts.TextToSpeech tts;
    private volatile boolean ttsReady;
    private volatile CountDownLatch permissionLatch;
    private volatile boolean permissionGranted;
    private ActivityResultLauncher<Intent> voiceLauncher;
    private ExperienceStore experience;
    private AgentProfile selectedAgent=AgentProfile.AUTO;
    private Skills.Skill selectedSkill;
    private View agentBar,emptyView;
    private com.google.android.material.chip.ChipGroup agentChips;
    private android.widget.LinearLayout examples;
    private volatile LlamaEngine engine;
    private volatile boolean generating=false;
    private ChatHistoryStore historyStore;
    private MemoryManager memoryManager;
    private ChatAdapter adapter;
    private SharedPreferences prefs;
    private TextView status;
    private RecyclerView chatList;
    private EditText inputBox;
    private View rootView,headerView,inputBar;
    private Button loadModelButton,sendButton,agentButton,assistantButton,webButton,thinkButton;
    private TextView clearButton,micButton,attachButton,menuButton;
    private androidx.drawerlayout.widget.DrawerLayout drawer;
    private android.widget.ListView conversationList;
    private ChatTools chatTools;
    private volatile ChatSession chatSession;
    private ActivityResultLauncher<String[]> attachLauncher;
    private int headerBasePaddingTop;
    private ActivityResultLauncher<String[]> pickModelLauncher;

    @Override protected void onCreate(Bundle savedInstanceState){
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        prefs=getSharedPreferences(PREFS,MODE_PRIVATE);
        historyStore=new ChatHistoryStore(this);
        device=new DeviceController(this);
        assistant=new PhoneAssistant(this,device,new AssistantUi());
        chatTools=new ChatTools(new WebTools(WebTools.DEFAULT_HTTP));
        chatTools.online=prefs.getBoolean(PREF_ONLINE,true);
        pool=new ModelPool<>(this::newEngine,this::availableRam);
        pool.setEvictListener((path,evicted)->{
            for(LlamaEngine e:pool.engines())if(e.draft()==evicted)e.setDraft(null,DRAFT_TOKENS);
            if(engine==evicted)engine=null;
        });
        experience=new ExperienceStore(new File(getFilesDir(),"learning"));
        memoryManager=new MemoryManager(historyStore);
        rootView=findViewById(R.id.rootView);
        headerView=findViewById(R.id.headerView);
        inputBar=findViewById(R.id.inputBar);
        status=findViewById(R.id.status);
        chatList=findViewById(R.id.chatList);
        inputBox=findViewById(R.id.inputBox);
        loadModelButton=findViewById(R.id.loadModelButton);
        sendButton=findViewById(R.id.sendButton);
        clearButton=findViewById(R.id.clearButton);
        agentButton=findViewById(R.id.agentButton);
        assistantButton=findViewById(R.id.assistantButton);
        micButton=findViewById(R.id.micButton);
        webButton=findViewById(R.id.webButton);
        thinkButton=findViewById(R.id.thinkButton);
        attachButton=findViewById(R.id.attachButton);
        menuButton=findViewById(R.id.menuButton);
        drawer=findViewById(R.id.drawer);
        conversationList=findViewById(R.id.conversationList);
        agentBar=findViewById(R.id.agentBar);
        emptyView=findViewById(R.id.emptyView);
        agentChips=findViewById(R.id.agentChips);
        examples=findViewById(R.id.examples);

        adapter=new ChatAdapter();
        adapter.setActions(new ResultActions());
        adapter.registerAdapterDataObserver(new RecyclerView.AdapterDataObserver(){
            @Override public void onChanged(){updateEmptyState();}
            @Override public void onItemRangeInserted(int a,int b){updateEmptyState();}
        });
        chatList.setLayoutManager(new LinearLayoutManager(this));
        chatList.setAdapter(adapter);
        adapter.setAll(historyStore.loadAll());
        scrollToEnd();
        installKeyboardInsetsFix();

        pickModelLauncher=registerForActivityResult(new ActivityResultContracts.OpenDocument(), this::onModelPicked);
        loadModelButton.setOnClickListener(v->chooseModelSource());
        sendButton.setOnClickListener(v->onSendOrStopClicked());
        clearButton.setOnClickListener(v->newChat());
        menuButton.setOnClickListener(v->{refreshConversations();drawer.openDrawer(androidx.core.view.GravityCompat.START);});
        findViewById(R.id.drawerNewChat).setOnClickListener(v->{drawer.closeDrawers();newChat();});
        findViewById(R.id.drawerModels).setOnClickListener(v->{drawer.closeDrawers();showModelRoles();});
        findViewById(R.id.drawerPermissions).setOnClickListener(v->{drawer.closeDrawers();showPermissions();});
        findViewById(R.id.drawerSpeed).setOnClickListener(v->{drawer.closeDrawers();tuneSpeed(true);});
        findViewById(R.id.drawerSettings).setOnClickListener(v->{drawer.closeDrawers();showSettings();});
        findViewById(R.id.drawerClearAll).setOnClickListener(v->new AlertDialog.Builder(this).setTitle("حذف كل المحادثات؟")
                .setMessage("تُحذف كل المحادثات وما طلبت من التطبيق تذكّره.").setPositiveButton("حذف",(d,w)->{drawer.closeDrawers();clearChat();})
                .setNegativeButton("إلغاء",null).show());
        conversationList.setOnItemClickListener((parent,view,pos,id)->{drawer.closeDrawers();openConversation(id);});
        conversationList.setOnItemLongClickListener((parent,view,pos,id)->{conversationMenu(id);return true;});
        webButton.setOnClickListener(v->{prefs.edit().putBoolean(PREF_WEB,!prefs.getBoolean(PREF_WEB,false)).apply();renderAgentButton();});
        thinkButton.setOnClickListener(v->{prefs.edit().putBoolean(PREF_THINK,!prefs.getBoolean(PREF_THINK,false)).apply();renderAgentButton();});
        attachLauncher=registerForActivityResult(new ActivityResultContracts.OpenDocument(),this::onAttach);
        attachButton.setOnClickListener(v->attachLauncher.launch(new String[]{"text/*","application/json","application/xml","application/javascript","application/x-python"}));
        agentButton.setOnClickListener(v->setAgentMode(!agentMode));
        agentButton.setOnLongClickListener(v->{showTools();return true;});
        assistantButton.setOnClickListener(v->setAssistantMode(!assistantMode));
        voiceLauncher=registerForActivityResult(new ActivityResultContracts.StartActivityForResult(),r->{
            java.util.ArrayList<String> said=r.getData()==null?null:r.getData().getStringArrayListExtra(android.speech.RecognizerIntent.EXTRA_RESULTS);
            if(r.getResultCode()!=RESULT_OK||said==null||said.isEmpty())return;
            if(assistantMode)startAssistant(said.get(0),true);else startChat(said.get(0),true);
        });
        micButton.setOnClickListener(v->listen());
        assistant.confirmSends=prefs.getBoolean(PREF_CONFIRM_SENDS,true);
        agentMode=prefs.getBoolean(PREF_AGENT_MODE,false);
        assistantMode=prefs.getBoolean(PREF_ASSISTANT,false)&&!agentMode;
        AgentProfile saved=AgentProfile.byId(prefs.getString(PREF_AGENT_PROFILE,"auto"));
        if(saved!=null)selectedAgent=saved;
        renderAgentButton();
        updateEmptyState();
        restoreSavedModel();
        handleAssistIntent(getIntent());
        if(!prefs.getBoolean("permissions_offered",false)){
            prefs.edit().putBoolean("permissions_offered",true).apply();
            ui.postDelayed(()->new AlertDialog.Builder(this).setTitle("أهلاً بك في NewAl")
                    .setMessage("امنحه الصلاحيات مرة وحدة ليقدر يفتح التطبيقات ويرسل ويتصل ويعمل بالخلفية بدون ما يسألك كل مرة. تقدر تغيّر هذا من ☰ ← الصلاحيات.")
                    .setPositiveButton("🔓 امنح الكل",(d,w)->startGrantAll()).setNegativeButton("لاحقاً",null).show(),800);
        }
        // While a task runs, Back sends the app to the background instead of closing it.
        getOnBackPressedDispatcher().addCallback(this,new androidx.activity.OnBackPressedCallback(true){
            @Override public void handleOnBackPressed(){
                if(agentLoop!=null)moveTaskToBack(true);
                else{setEnabled(false);getOnBackPressedDispatcher().onBackPressed();setEnabled(true);}
            }
        });
    }

    private void installKeyboardInsetsFix(){
        Window window=getWindow();
        WindowCompat.setDecorFitsSystemWindows(window,false);
        headerBasePaddingTop=headerView.getPaddingTop();
        ViewCompat.setOnApplyWindowInsetsListener(rootView,(view,insets)->{
            Insets bars=insets.getInsets(WindowInsetsCompat.Type.systemBars());
            Insets ime=insets.getInsets(WindowInsetsCompat.Type.ime());
            headerView.setPadding(headerView.getPaddingLeft(),headerBasePaddingTop+bars.top,headerView.getPaddingRight(),headerView.getPaddingBottom());
            int bottom=Math.max(bars.bottom,ime.bottom);
            android.widget.LinearLayout.LayoutParams lp=(android.widget.LinearLayout.LayoutParams)inputBar.getLayoutParams();
            lp.bottomMargin=bottom;
            inputBar.setLayoutParams(lp);
            if(ime.bottom>0) inputBar.post(this::scrollToEnd);
            return insets;
        });
        ViewCompat.requestApplyInsets(rootView);
    }

    // ------------------------------------------------------------------ model catalog / download

    /** Free GGUF models from Hugging Face: name, size in GB, minimum phone RAM in GB, URL. */
    private static final String LFM_26B="https://huggingface.co/LiquidAI/LFM2.5-2.6B-GGUF/resolve/main/LFM2.5-2.6B-QAD-Q4_0.gguf",
            QWEN35_2B="https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf",
            LFM_12B="https://huggingface.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF/resolve/main/LFM2.5-1.2B-Instruct-QAD-Q4_0.gguf",
            PACK="pack:a16";
    /**
     * Free GGUF models from Hugging Face: name, size in GB, minimum phone RAM in GB, URL. Chosen by
     * our measurements: LFM2.5 2.6B (hybrid, Arabic, agent-trained, quantisation-aware Q4_0 that is
     * fastest on ARM) answers best; Qwen3.5 drives the screen best.
     */
    private static final Object[][] MODEL_CATALOG={
            {"⭐ الحزمة المثالية لهاتفك (تُختار حسب الرام)",2.0,3,PACK},
            {"⭐ LFM2.5 2.6B — ذكي بالعربي، أدوات، سياق 128K (هجين، Q4_0 مسرّع)",1.59,6,LFM_26B},
            {"🪶 LFM2.5 1.2B — الأفضل لهواتف 4GB، سريع جداً",0.70,3,LFM_12B},
            {"📱 Qwen3.5 2B — الأفضل للتحكم بالشاشة (يُحمّل عند الحاجة)",1.28,4,QWEN35_2B},
            {"🧠 Qwen3.5 4B — الأدق بالتحكم بالهاتف (أبطأ)",2.74,8,"https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf"},
            {"💻 Qwen2.5-Coder 3B — للبرمجة مع Termux",2.1,6,"https://huggingface.co/Qwen/Qwen2.5-Coder-3B-Instruct-GGUF/resolve/main/qwen2.5-coder-3b-instruct-q4_k_m.gguf"},
            {"💻 Qwen2.5-Coder 0.5B — مسودة تسريع للـCoder",0.68,3,"https://huggingface.co/Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-0.5b-instruct-q8_0.gguf"},
    };



    private void chooseModelSource(){
        android.app.ActivityManager am=(android.app.ActivityManager)getSystemService(ACTIVITY_SERVICE);
        android.app.ActivityManager.MemoryInfo mi=new android.app.ActivityManager.MemoryInfo();
        if(am!=null)am.getMemoryInfo(mi);
        double ramGb=mi.totalMem/1e9;
        // Models already on the phone first: one tap switches.
        final File[] files=modelFiles().toArray(new File[0]);
        List<String> items=new java.util.ArrayList<>();
        for(File f:files)items.add((f.getAbsolutePath().equals(loadedPath)?"✅ ":"📦 ")+modelTitle(f.getName()).replace(" ▾","")+String.format(java.util.Locale.US,"  • %.1f GB",f.length()/1e9));
        items.add("📂 اختيار ملف GGUF من الهاتف");
        for(Object[] m:MODEL_CATALOG){
            boolean fits=ramGb<=0||ramGb>=((Number)m[2]).doubleValue();
            items.add((fits?"📥 ":"⚠️ ")+m[0]+String.format(java.util.Locale.US," • %.1f GB",((Number)m[1]).doubleValue())+(fits?"":" (يحتاج RAM أكبر)"));
        }
        new AlertDialog.Builder(this).setTitle(String.format(java.util.Locale.US,"النموذج (ذاكرة الهاتف %.1f GB)",ramGb)).setItems(items.toArray(new String[0]),(d,which)->{
            if(which<files.length){loadModelFromLocalFile(files[which],files[which].getName());return;}
            which-=files.length;
            if(which==0)pickModelLauncher.launch(new String[]{"*/*"});
            else if(PACK.equals(MODEL_CATALOG[which-1][3])){
                // Screen control model first (assigned to the assistant role), then the chat model, which is loaded.
                // Under 6 GB of RAM the 1.2B chat model: Android itself needs about 2 GB of a 4 GB phone.
                boolean small=ramGb>0&&ramGb<5.5;
                prefs.edit().putString("pending_role_"+fileOf(QWEN35_2B),ROLE_MANAGER).apply();
                downloadModel("Qwen3.5 2B",QWEN35_2B,false);
                downloadModel(small?"LFM2.5 1.2B":"LFM2.5 2.6B",small?LFM_12B:LFM_26B,true);
                setWorking(true,"📥 الحزمة لهاتف "+String.format(java.util.Locale.US,"%.0f",ramGb)+"GB: "+(small?"LFM2.5 1.2B":"LFM2.5 2.6B")+" + Qwen3.5 2B");
            }
            else downloadModel((String)MODEL_CATALOG[which-1][0],(String)MODEL_CATALOG[which-1][3]);
        }).show();
    }

    /** Downloads with Android's DownloadManager (resumes, survives app restarts), then loads it. */
    /** Downloaded models live in the phone's Downloads/NewAl folder: visible in Files, kept if the app is removed. */
    static File publicModelsDir(){
        return new File(android.os.Environment.getExternalStoragePublicDirectory(android.os.Environment.DIRECTORY_DOWNLOADS),"NewAl");
    }

    /** GGUF files in the app's folder, in Downloads/NewAl and directly in Downloads (the last needs all-files access). */
    private List<File> modelFiles(){
        List<File> out=new java.util.ArrayList<>();
        java.util.Set<String> names=new java.util.HashSet<>();
        File[] dirs={publicModelsDir(),getExternalFilesDir("models"),android.os.Environment.getExternalStoragePublicDirectory(android.os.Environment.DIRECTORY_DOWNLOADS)};
        for(File d:dirs){
            File[] fs=d==null?null:d.listFiles((x,n)->n.toLowerCase(java.util.Locale.ROOT).endsWith(".gguf"));
            if(fs==null)continue;
            for(File f:fs)if(f.canRead()&&names.add(f.getName()))out.add(f);
        }
        java.util.Collections.sort(out,(a,b)->a.getName().compareToIgnoreCase(b.getName()));
        return out;
    }

    private static String fileOf(String url){return url.substring(url.lastIndexOf('/')+1);}

    private void downloadModel(String title,String url){downloadModel(title,url,true);}

    /** {@code load}: open the model when the download finishes (false for a role model of a pack). */
    private void downloadModel(String title,String url,boolean load){
        File dir=publicModelsDir();
        String fileName=url.substring(url.lastIndexOf('/')+1);
        File target=new File(dir,fileName);
        if(isValidGgufFile(target)){downloaded(target,load);return;}
        File old=getExternalFilesDir("models")==null?null:new File(getExternalFilesDir("models"),fileName);
        if(old!=null&&isValidGgufFile(old)){downloaded(old,load);return;}
        android.app.DownloadManager dm=(android.app.DownloadManager)getSystemService(DOWNLOAD_SERVICE);
        if(dm==null)return;
        target.delete();
        android.app.DownloadManager.Request r=new android.app.DownloadManager.Request(Uri.parse(url))
                .setTitle(title).setDescription("NewAl model")
                .setNotificationVisibility(android.app.DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setAllowedOverMetered(true).setAllowedOverRoaming(false)
                .setDestinationInExternalPublicDir(android.os.Environment.DIRECTORY_DOWNLOADS,"NewAl/"+fileName);
        long id=dm.enqueue(r);
        loadModelButton.setEnabled(false);
        pollDownload(dm,id,target,fileName,load);
    }

    /** A model file is ready: take a pending role assignment, then load it if asked. */
    private void downloaded(File f,boolean load){
        String role=prefs.getString("pending_role_"+f.getName(),"");
        if(!role.isEmpty()){
            prefs.edit().putString(PREF_ROLE+role,f.getAbsolutePath()).remove("pending_role_"+f.getName()).apply();
            setWorking(false,"✅ "+f.getName()+" ← "+roleTitle(role));
        }
        if(load)loadModelFromLocalFile(f,f.getName());
    }

    private void pollDownload(android.app.DownloadManager dm,long id,File target,String name,boolean load){
        try(android.database.Cursor c=dm.query(new android.app.DownloadManager.Query().setFilterById(id))){
            if(c==null||!c.moveToFirst()){loadModelButton.setEnabled(true);setWorking(false,"أُلغي التنزيل");return;}
            int status=c.getInt(c.getColumnIndexOrThrow(android.app.DownloadManager.COLUMN_STATUS));
            long done=c.getLong(c.getColumnIndexOrThrow(android.app.DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR));
            long total=c.getLong(c.getColumnIndexOrThrow(android.app.DownloadManager.COLUMN_TOTAL_SIZE_BYTES));
            if(status==android.app.DownloadManager.STATUS_SUCCESSFUL){
                if(isValidGgufFile(target)){loadModelButton.setEnabled(true);downloaded(target,load);}
                else if(target.exists()&&!target.canRead()){
                    // Some Android versions hide downloads from the app that asked for them until it has all-files access.
                    loadModelButton.setEnabled(true);
                    setWorking(false,"✅ تنزّل إلى Download/NewAl — اسمح بالوصول للملفات ثم اختره من قائمة النماذج");
                    openAllFilesAccess();
                }
                else{loadModelButton.setEnabled(true);setWorking(false,"الملف المنزّل ليس GGUF صالحاً");}
                return;
            }
            if(status==android.app.DownloadManager.STATUS_FAILED){
                loadModelButton.setEnabled(true);
                setWorking(false,"فشل التنزيل (رمز "+c.getInt(c.getColumnIndexOrThrow(android.app.DownloadManager.COLUMN_REASON))+")");
                return;
            }
            setWorking(true,total>0?String.format(java.util.Locale.US,"📥 تنزيل %s… %d%% (%.0f / %.0f MB)",name,done*100/total,done/1e6,total/1e6)
                    :"📥 تنزيل "+name+"…");
        }
        ui.postDelayed(()->pollDownload(dm,id,target,name,load),1000);
    }

    private void restoreSavedModel(){
        String savedName=prefs.getString(PREF_MODEL_NAME,"النموذج");
        String localPath=prefs.getString(PREF_MODEL_LOCAL_PATH,null);
        if(localPath!=null){
            File local=new File(localPath);
            long expected=prefs.getLong(PREF_MODEL_SIZE,-1L);
            if(isValidGgufFile(local)&&(expected<=0||local.length()==expected)){
                loadModelFromLocalFile(local,savedName);
                return;
            }
        }
        String savedUri=prefs.getString(PREF_MODEL_URI,null);
        if(savedUri!=null) prepareAndLoadFromUri(Uri.parse(savedUri),savedName);
        else setWorking(false,"اضغط «تحميل نموذج» واختر ملف GGUF من الهاتف");
    }

    private void onModelPicked(Uri uri){
        if(uri==null)return;
        String name=queryDisplayName(uri);
        if(!name.toLowerCase(java.util.Locale.ROOT).endsWith(".gguf")){setWorking(false,"الملف المختار ليس GGUF");return;}
        long size=querySize(uri);
        prefs.edit().putString(PREF_MODEL_URI,uri.toString()).putString(PREF_MODEL_NAME,name).apply();
        try{getContentResolver().takePersistableUriPermission(uri,Intent.FLAG_GRANT_READ_URI_PERMISSION);}catch(SecurityException ignored){}
        prepareAndLoadFromUri(uri,name,size);
    }

    private void prepareAndLoadFromUri(Uri uri,String displayName){prepareAndLoadFromUri(uri,displayName,querySize(uri));}

    private void prepareAndLoadFromUri(Uri uri,String displayName,long expectedSize){
        setWorking(true,"جاري تجهيز ملف GGUF: "+displayName);
        loadModelButton.setEnabled(false);
        executor.execute(()->{
            try{
                File staged=stageModelToAppStorage(uri,expectedSize,displayName);
                runOnUiThread(()->setWorking(true,"تم تجهيز النموذج — جاري فتحه: "+displayName));
                loadModelFromLocalFileInternal(staged,displayName,uri.toString(),expectedSize);
            }catch(Exception ex){
                runOnUiThread(()->{loadModelButton.setEnabled(true);setWorking(false,"تعذر تجهيز GGUF: "+safeMessage(ex));});
            }
        });
    }

    /** Copies a picked GGUF into the app's models folder under its own file name (several models can be kept). */
    private File stageModelToAppStorage(Uri uri,long expectedSize,String displayName)throws Exception{
        File dir=getExternalFilesDir("models");
        if(dir==null)throw new IllegalStateException("تخزين التطبيق غير متاح");
        if(!dir.exists()&&!dir.mkdirs())throw new IllegalStateException("تعذر إنشاء مجلد النماذج");
        String safe=displayName==null?"":displayName.replaceAll("[^\\p{L}\\p{N}._-]+","_");
        if(safe.isEmpty()||safe.equals("_"))safe="model";
        if(!safe.toLowerCase(java.util.Locale.ROOT).endsWith(".gguf"))safe+=".gguf";
        File tmp=new File(dir,safe+".part"),target=new File(dir,safe);
        if(tmp.exists()&&!tmp.delete())throw new IllegalStateException("تعذر حذف ملف مؤقت قديم");
        try(InputStream raw=getContentResolver().openInputStream(uri)){
            if(raw==null)throw new IllegalStateException("تعذر فتح ملف GGUF");
            try(BufferedInputStream in=new BufferedInputStream(raw,1024*1024);BufferedOutputStream out=new BufferedOutputStream(new FileOutputStream(tmp),1024*1024)){
                byte[] buffer=new byte[1024*1024];long copied=0,lastUi=0;int read;
                while((read=in.read(buffer))!=-1){
                    out.write(buffer,0,read);copied+=read;long now=System.currentTimeMillis();
                    if(now-lastUi>=400){
                        final long done=copied;
                        runOnUiThread(()->{if(expectedSize>0){int pct=(int)Math.min(99L,(done*100L)/expectedSize);setWorking(true,"جاري تجهيز GGUF… "+pct+"%");}else setWorking(true,"جاري تجهيز GGUF… "+(done/(1024*1024))+" MB");});
                        lastUi=now;
                    }
                }
                if(expectedSize>0&&copied!=expectedSize)throw new IllegalStateException("اكتمل النسخ بحجم غير متوقع للـGGUF");
            }
        }
        if(!isValidGgufFile(tmp)){tmp.delete();throw new IllegalStateException("ملف GGUF غير صالح أو تالف");}
        if(target.exists()&&!target.delete()){tmp.delete();throw new IllegalStateException("تعذر استبدال النموذج السابق");}
        if(!tmp.renameTo(target)){tmp.delete();throw new IllegalStateException("تعذر حفظ نسخة النموذج داخل التطبيق");}
        prefs.edit().putString(PREF_MODEL_LOCAL_PATH,target.getAbsolutePath()).putLong(PREF_MODEL_SIZE,target.length()).apply();
        return target;
    }

    private void loadModelFromLocalFile(File file,String displayName){
        setWorking(true,"جاري فتح النموذج: "+displayName);
        loadModelButton.setEnabled(false);
        executor.execute(()->loadModelFromLocalFileInternal(file,displayName,prefs.getString(PREF_MODEL_URI,""),file.length()));
    }

    private void loadModelFromLocalFileInternal(File file,String displayName,String sourceUri,long expectedSize){
        try{
            if(!isValidGgufFile(file))throw new IllegalStateException("ملف GGUF غير صالح أو تالف");
            LlamaEngine loaded=pool.get(file.getAbsolutePath(),java.util.Collections.emptyList());
            engine=loaded;
            loadedPath=file.getAbsolutePath();
            prefs.edit().putString(PREF_MODEL_LOCAL_PATH,file.getAbsolutePath()).putString(PREF_MODEL_NAME,displayName).putString(PREF_MODEL_URI,sourceUri).putLong(PREF_MODEL_SIZE,file.length()>0?file.length():expectedSize).apply();
            runOnUiThread(()->{
                loadModelButton.setEnabled(true);loadModelButton.setText(modelTitle(displayName));setWorking(false,"جاهز — "+displayName);updateEmptyState();resumeInterruptedAgentTask();
                if(!prefs.contains("gen_threads"))ui.postDelayed(()->tuneSpeed(false),600);
            });
        }catch(Exception ex){
            runOnUiThread(()->{loadModelButton.setEnabled(true);setWorking(false,"تعذر تحميل النموذج: "+safeMessage(ex));});
        }
    }

    /** "Qwen3.5-4B-Q4_K_M.gguf" → "Qwen3.5 4B ▾". */
    static String modelTitle(String file){
        String n=file.replaceAll("(?i)\\.gguf$","").replaceAll("(?i)[-_.](q\\d.*|iq\\d.*|f16|bf16|f32|ud-.*)$","").replaceAll("(?i)-instruct(-\\d+)?","").replace('-',' ').replace('_',' ');
        return (n.length()>24?n.substring(0,24)+"…":n)+" ▾";
    }

    private long querySize(Uri uri){
        try(android.database.Cursor c=getContentResolver().query(uri,new String[]{android.provider.OpenableColumns.SIZE},null,null,null)){
            if(c!=null&&c.moveToFirst()){int idx=c.getColumnIndex(android.provider.OpenableColumns.SIZE);if(idx>=0&&!c.isNull(idx))return c.getLong(idx);}
        }catch(Exception ignored){}
        return -1L;
    }

    private String queryDisplayName(Uri uri){
        try(android.database.Cursor c=getContentResolver().query(uri,null,null,null,null)){
            if(c!=null&&c.moveToFirst()){int idx=c.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME);if(idx>=0){String name=c.getString(idx);if(!TextUtils.isEmpty(name))return name;}}
        }catch(Exception ignored){}
        return "النموذج.gguf";
    }

    private boolean isValidGgufFile(File file){
        if(file==null||!file.isFile()||file.length()<4)return false;
        try(FileInputStream in=new FileInputStream(file)){byte[] magic=new byte[4];int n=in.read(magic);return n==4&&magic[0]=='G'&&magic[1]=='G'&&magic[2]=='U'&&magic[3]=='F';}catch(Exception ignored){return false;}
    }

    private void onSendOrStopClicked(){
        if(generating){AgentLoop loop=agentLoop;if(loop!=null)loop.cancel();assistant.cancel();ChatSession cs=chatSession;if(cs!=null)cs.cancel();for(LlamaEngine e:pool.engines())e.cancel();return;}
        String question=inputBox.getText().toString().trim();
        if(question.isEmpty())return;
        if(assistantMode){inputBox.setText("");startAssistant(question,false);return;}
        if(!agentMode){inputBox.setText("");startChat(question,false);return;}
        if(engine==null)return;
        inputBox.setText("");
        startAgent(question,false);
    }

    // ------------------------------------------------------------------ phone assistant

    private void setAssistantMode(boolean on){
        assistantMode=on;
        if(on)agentMode=false;
        prefs.edit().putBoolean(PREF_ASSISTANT,on).putBoolean(PREF_AGENT_MODE,agentMode).apply();
        renderAgentButton();
        setWorking(false,on?(ScreenControlService.instance==null?"📱 المساعد — فعّل 🖐 التحكم بالشاشة ليعمل بالكامل":"📱 المساعد جاهز"):"وضع المحادثة العادية");
    }

    /** Opened as the phone's assistant (long-press home): start listening right away. */
    private void handleAssistIntent(Intent intent){
        if(intent==null)return;
        String a=intent.getAction();
        if(Intent.ACTION_PROCESS_TEXT.equals(a)||(Intent.ACTION_SEND.equals(a)&&intent.getType()!=null&&intent.getType().startsWith("text/"))){
            CharSequence picked=Intent.ACTION_PROCESS_TEXT.equals(a)?intent.getCharSequenceExtra(Intent.EXTRA_PROCESS_TEXT):intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
            setIntent(new Intent(this,MainActivity.class));   // handled once, not again on rotation
            if(picked!=null&&picked.toString().trim().length()>0)askAboutText(picked.toString().trim());
            return;
        }
        if(Intent.ACTION_ASSIST.equals(a)||Intent.ACTION_VOICE_COMMAND.equals(a)){
            if(!assistantMode)setAssistantMode(true);
            ui.postDelayed(this::listen,300);
        }
    }

    /** Text selected or shared from another app: summarise, translate, fix, explain, or ask about it. */
    private void askAboutText(String text){
        String[] labels={"💬 اسأل عنه","📝 لخّص","🌐 ترجم للعربية","🌍 ترجم للإنجليزية","✍️ صحّح وحسّن الصياغة","📖 اشرح ببساطة"};
        String[] prompts={"","لخّص النص التالي بنقاط قصيرة:","ترجم النص التالي إلى العربية بدقة:","Translate the following text into natural English:",
                "صحّح الأخطاء وحسّن صياغة النص التالي مع الحفاظ على معناه، وأعطني النسخة المحسّنة فقط:","اشرح النص التالي ببساطة:"};
        new AlertDialog.Builder(this).setTitle(text.length()>80?text.substring(0,80)+"…":text).setItems(labels,(d,w)->{
            if(agentMode||assistantMode){agentMode=false;assistantMode=false;prefs.edit().putBoolean(PREF_AGENT_MODE,false).putBoolean(PREF_ASSISTANT,false).apply();renderAgentButton();}
            String quoted="«"+text+"»";
            if(w==0||engine==null){
                // Ask yourself (or wait for the model): the text is ready in the box.
                inputBox.setText(w==0?quoted+"\n":prompts[w]+"\n\n"+text);
                inputBox.setSelection(inputBox.getText().length());
                inputBox.requestFocus();
                if(engine==null)setWorking(false,"النموذج قيد التحميل… اضغط إرسال بعد قليل");
            }else startChat(prompts[w]+"\n\n"+text,false);
        }).show();
    }

    /** Speech to text with the phone's own recogniser (works offline when the language pack is installed). */
    private android.speech.SpeechRecognizer recognizer;
    private boolean listening;
    /** Tried in order: Google has no Levantine code, so Saudi then Egyptian Arabic, then plain "ar". */
    private static final String[] VOICE_LANGS={"ar-SA","ar-EG","ar","en-US"};

    /**
     * Voice input with Android's recogniser inside the app (no Google pop-up, online or offline,
     * whatever the phone has). Tapping 🎙 again stops listening.
     */
    private void listen(){
        if(listening){if(recognizer!=null)recognizer.stopListening();return;}
        if(checkSelfPermission(android.Manifest.permission.RECORD_AUDIO)!=android.content.pm.PackageManager.PERMISSION_GRANTED){
            requestPermissions(new String[]{android.Manifest.permission.RECORD_AUDIO},REQ_ASSIST);
            setWorking(false,"اسمح بالميكروفون ثم اضغط 🎙 مرة ثانية");
            return;
        }
        if(!android.speech.SpeechRecognizer.isRecognitionAvailable(this)){listenWithIntent();return;}
        listenIn(0);
    }

    private void listenIn(int lang){
        if(recognizer!=null)recognizer.destroy();
        recognizer=android.speech.SpeechRecognizer.createSpeechRecognizer(this);
        recognizer.setRecognitionListener(new android.speech.RecognitionListener(){
            @Override public void onReadyForSpeech(Bundle b){listening=true;micButton.setText("⏺");setWorking(false,"🎙 أسمعك… تكلّم");}
            @Override public void onBeginningOfSpeech(){}
            @Override public void onRmsChanged(float v){}
            @Override public void onBufferReceived(byte[] b){}
            @Override public void onEndOfSpeech(){setWorking(false,"🎙 …");}
            @Override public void onPartialResults(Bundle b){
                java.util.ArrayList<String> r=b.getStringArrayList(android.speech.SpeechRecognizer.RESULTS_RECOGNITION);
                if(r!=null&&!r.isEmpty())inputBox.setText(r.get(0));
            }
            @Override public void onEvent(int t,Bundle b){}
            @Override public void onResults(Bundle b){
                stopListeningUi();
                java.util.ArrayList<String> r=b.getStringArrayList(android.speech.SpeechRecognizer.RESULTS_RECOGNITION);
                if(r==null||r.isEmpty()||r.get(0).trim().isEmpty()){setWorking(false,"ما سمعت شي، جرّب مرة ثانية");return;}
                String said=r.get(0).trim();
                inputBox.setText("");
                if(generating)return;
                if(assistantMode)startAssistant(said,true);else if(agentMode)startAgent(said,false);else startChat(said,true);
            }
            @Override public void onError(int error){
                stopListeningUi();
                boolean language=error==12||error==13;   // ERROR_LANGUAGE_NOT_SUPPORTED / _UNAVAILABLE (API 31)
                if(language&&lang+1<VOICE_LANGS.length){listenIn(lang+1);return;}
                if(error==android.speech.SpeechRecognizer.ERROR_NO_MATCH||error==android.speech.SpeechRecognizer.ERROR_SPEECH_TIMEOUT){setWorking(false,"ما سمعت شي واضح، جرّب مرة ثانية");return;}
                if(error==android.speech.SpeechRecognizer.ERROR_NETWORK||error==android.speech.SpeechRecognizer.ERROR_NETWORK_TIMEOUT){
                    setWorking(false,"التعرّف على الصوت يحتاج نت، أو نزّل العربية للاستخدام بدون نت من إعدادات Google ← الصوت");return;}
                // The phone's service refused: the classic Google dialog is the last resort.
                listenWithIntent();
            }
        });
        Intent i=new Intent(android.speech.RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE_MODEL,android.speech.RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                .putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE,VOICE_LANGS[lang])
                .putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE,VOICE_LANGS[lang])
                .putExtra(android.speech.RecognizerIntent.EXTRA_PARTIAL_RESULTS,true)
                .putExtra(android.speech.RecognizerIntent.EXTRA_MAX_RESULTS,1);
        try{recognizer.startListening(i);}catch(RuntimeException e){stopListeningUi();listenWithIntent();}
    }

    private void stopListeningUi(){listening=false;micButton.setText("🎙");}

    private void listenWithIntent(){
        Intent i=new Intent(android.speech.RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE_MODEL,android.speech.RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                .putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE,"ar-SA")
                .putExtra(android.speech.RecognizerIntent.EXTRA_PROMPT,"قل طلبك…");
        try{voiceLauncher.launch(i);}
        catch(android.content.ActivityNotFoundException e){setWorking(false,"لا يوجد تعرّف على الكلام بهذا الهاتف: ثبّت أو حدّث تطبيق Google");}
    }

    private void speak(String text){
        if(text==null||text.isEmpty()||!prefs.getBoolean(PREF_SPEAK,true))return;
        String clean=text.replaceAll("[\\p{So}\\p{Cn}\\x{FE0F}]","").replaceAll("[«»]","").trim();
        if(tts==null){
            tts=new android.speech.tts.TextToSpeech(getApplicationContext(),status->{
                if(status!=android.speech.tts.TextToSpeech.SUCCESS)return;
                tts.setLanguage(new java.util.Locale("ar"));
                ttsReady=true;
                tts.speak(clean,android.speech.tts.TextToSpeech.QUEUE_FLUSH,null,"reply");
            });
            return;
        }
        if(ttsReady)tts.speak(clean,android.speech.tts.TextToSpeech.QUEUE_FLUSH,null,"reply");
    }

    private void buildAssistantChips(){
        agentChips.removeAllViews();
        boolean on=ScreenControlService.instance!=null;
        com.google.android.material.chip.Chip screen=chip(on?"🖐 التحكم مفعّل":"🖐 فعّل التحكم بالشاشة",on);
        screen.setOnClickListener(v->{
            if(ScreenControlService.instance!=null){setWorking(false,"التحكم بالشاشة مفعّل");return;}
            guideScreenControl();
        });
        agentChips.addView(screen);
        boolean confirm=prefs.getBoolean(PREF_CONFIRM_SENDS,true);
        com.google.android.material.chip.Chip c=chip(confirm?"✅ يسأل قبل الإرسال":"⚡ يرسل بدون سؤال",!confirm);
        c.setOnClickListener(v->{
            boolean next=!prefs.getBoolean(PREF_CONFIRM_SENDS,true);
            prefs.edit().putBoolean(PREF_CONFIRM_SENDS,next).apply();
            assistant.confirmSends=next;
            buildAssistantChips();
        });
        agentChips.addView(c);
        boolean speakOn=prefs.getBoolean(PREF_SPEAK,true);
        com.google.android.material.chip.Chip sp=chip(speakOn?"🔈 يرد بالصوت":"🔇 بدون صوت",speakOn);
        sp.setOnClickListener(v->{prefs.edit().putBoolean(PREF_SPEAK,!prefs.getBoolean(PREF_SPEAK,true)).apply();buildAssistantChips();});
        agentChips.addView(sp);
        com.google.android.material.chip.Chip models=chip("🧠 النماذج",false);
        models.setOnClickListener(v->showModelRoles());
        agentChips.addView(models);
        com.google.android.material.chip.Chip assist=chip("🏠 اجعله مساعد الهاتف",false);
        assist.setOnClickListener(v->{
            try{startActivity(new Intent(android.provider.Settings.ACTION_VOICE_INPUT_SETTINGS));}
            catch(Exception e){startActivity(new Intent(android.provider.Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS));}
            setWorking(false,"اختر NewAl كتطبيق المساعد الرقمي، ثم اضغط مطولاً على زر الرئيسية لتتكلم معه");
        });
        agentChips.addView(assist);
    }

    /** Everyday commands run at once without a model; other phone tasks go to the screen agent; questions to the chat model. */
    private void startAssistant(String text,boolean spoken){
        setGenerating(true);
        executor.execute(()->{
            long userId=historyStore.append(ChatMessage.ROLE_USER,text);long t0=System.currentTimeMillis();
            runOnUiThread(()->{adapter.add(new ChatMessage(userId,ChatMessage.ROLE_USER,text,t0));scrollToEnd();setWorking(true,"📱 …");});
            String reply;
            String metric="";
            try{
                long start=System.currentTimeMillis();
                List<QuickCommands.Command> commands=QuickCommands.parse(text,java.time.LocalTime.now());
                if(commands!=null){
                    reply=assistant.runQuick(commands);
                    metric="⚡ بدون نموذج • "+(System.currentTimeMillis()-start)+"ms";
                }else if(QuickCommands.looksLikePhoneTask(text)){
                    reply=runScreenAgent(text);
                    metric="🤖 وكيل الشاشة • "+(System.currentTimeMillis()-start)/1000+"s";
                }else if(engine!=null){
                    reply=null;
                    String said=runChatTurn();
                    if(spoken&&said!=null)speak(said);
                }else reply="الأوامر اليومية (افتح، اتصل، ابعت، منبه، كشاف، بلوتوث…) تعمل بدون نموذج. للأسئلة والمهام المركّبة حمّل نموذجاً من 📂 (المقترح: Qwen3.5 4B أو 2B).";
            }catch(Exception e){reply="❌ "+safeMessage(e);}
            final String r=reply,m=metric;
            if(r!=null){
                long id=historyStore.append(ChatMessage.ROLE_ASSISTANT,r);long t=System.currentTimeMillis();
                runOnUiThread(()->{adapter.add(new ChatMessage(id,ChatMessage.ROLE_ASSISTANT,r,t));scrollToEnd();});
                if(spoken)speak(r);
            }
            runOnUiThread(()->{setGenerating(false);setWorking(false,m.isEmpty()?"جاهز":m);if(assistantMode)buildAssistantChips();});
        });
    }

    private String runScreenAgent(String goal) throws Exception{
        if(engine==null)return "هذه المهمة تحتاج نموذجاً يفهم الشاشة: من 📂 نزّل «Qwen3.5 4B» (الأدق) أو «Qwen3.5 2B» (الأسرع).";
        ScreenControlService s=ScreenControlService.instance;
        if(s==null)return PhoneAssistant.needScreenControl();
        AgentService.start(this,AgentLoop.tail(goal,200));
        s.showStatus(AgentLoop.tail(goal,80),()->{assistant.cancel();for(LlamaEngine e:pool.engines())e.cancel();});
        live.start("",ChatMessage.ROLE_TERMINAL);
        PhoneAgent.Result res;
        try{
            res=assistant.runAgent(goal,(turns,grammar)->engineForRole(ROLE_MANAGER).generate(turns,96,0f,1,LlamaEngine.FLAG_RAW,grammar,null).text,
                    new PhoneAgent.Listener(){
                        @Override public void onStep(int step,PhoneAction action,String reason,String result){
                            String line=step+". "+(action==null?"؟":action.toString())+(result.isEmpty()?"":" → "+result);
                            live.append(line+"\n");
                            s.showStatus(line,null);
                            AgentService.update(getApplicationContext(),line);
                        }
                        @Override public boolean confirm(String what){return new AssistantUi().confirm("تأكيد",what+"؟");}
                    });
        }finally{
            live.end();
            s.hideStatus();
        }
        String reply=(res.done?"✅ ":"⚠️ ")+res.message;
        AgentService.finish(getApplicationContext(),res.done?"✅ تمت المهمة":"⚠️ المهمة لم تكتمل",res.message);
        // Unfinished or a question for the user: come back so the answer is seen.
        if(!res.done)startActivity(new Intent(this,MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT|Intent.FLAG_ACTIVITY_NEW_TASK));
        return reply;
    }

    /** Dialogs and permissions for the assistant, asked from its worker thread. */
    private final class AssistantUi implements PhoneAssistant.Ui{
        @Override public boolean confirm(String title,String message){
            // Another app is in front (the screen agent is working): ask over it.
            ScreenControlService s=ScreenControlService.instance;
            if(!resumed&&s!=null)return s.confirmOverlay(title+"\n"+message,"نعم",120_000);
            CountDownLatch done=new CountDownLatch(1);boolean[] ok={false};
            runOnUiThread(()->new AlertDialog.Builder(MainActivity.this).setTitle(title).setMessage(message).setCancelable(false)
                    .setPositiveButton("نعم",(d,w)->{ok[0]=true;done.countDown();})
                    .setNegativeButton("إلغاء",(d,w)->done.countDown()).show());
            try{if(!done.await(5,TimeUnit.MINUTES))return false;}catch(InterruptedException e){Thread.currentThread().interrupt();return false;}
            return ok[0];
        }
        @Override public int choose(String title,String[] options){
            CountDownLatch done=new CountDownLatch(1);int[] pick={-1};
            runOnUiThread(()->new AlertDialog.Builder(MainActivity.this).setTitle(title)
                    .setItems(options,(d,w)->{pick[0]=w;done.countDown();})
                    .setOnCancelListener(d->done.countDown()).show());
            try{if(!done.await(5,TimeUnit.MINUTES))return -1;}catch(InterruptedException e){Thread.currentThread().interrupt();return -1;}
            return pick[0];
        }
        @Override public boolean permission(String permission){
            if(checkSelfPermission(permission)==android.content.pm.PackageManager.PERMISSION_GRANTED)return true;
            CountDownLatch done=new CountDownLatch(1);
            permissionLatch=done;permissionGranted=false;
            runOnUiThread(()->requestPermissions(new String[]{permission},REQ_ASSIST));
            try{if(!done.await(2,TimeUnit.MINUTES))return false;}catch(InterruptedException e){Thread.currentThread().interrupt();return false;}
            return permissionGranted;
        }
        @Override public void progress(String line){runOnUiThread(()->status.setText(line));}
    }

    // ------------------------------------------------------------------ Termux agent

    private void setAgentMode(boolean on){
        agentMode=on;
        if(on)assistantMode=false;
        prefs.edit().putBoolean(PREF_AGENT_MODE,on).putBoolean(PREF_ASSISTANT,assistantMode).apply();
        renderAgentButton();
        if(on)ensureTermuxReady();
        else setWorking(false,"وضع المحادثة العادية");
    }

    private void renderAgentButton(){
        agentButton.setSelected(agentMode);
        assistantButton.setSelected(assistantMode);
        webButton.setSelected(prefs.getBoolean(PREF_WEB,false));
        thinkButton.setSelected(prefs.getBoolean(PREF_THINK,false));
        webButton.setVisibility(agentMode?View.GONE:View.VISIBLE);
        thinkButton.setVisibility(agentMode?View.GONE:View.VISIBLE);
        inputBox.setHint(assistantMode?"افتح، اتصل، ابعت، شغّل… أو أي مهمة على الهاتف":agentMode?"اطلب برنامجاً… أو /test /fix /explain":"اسأل أي شيء");
        agentBar.setVisibility(agentMode||assistantMode?View.VISIBLE:View.GONE);
        if(assistantMode)buildAssistantChips();
        else if(agentMode)buildAgentChips();
        if(!generating)sendButton.setEnabled(engine!=null||assistantMode);
        updateEmptyState();
    }

    // ------------------------------------------------------------------ agent bar, welcome, dialogs

    private void buildAgentChips(){
        agentChips.removeAllViews();
        for(AgentProfile a:AgentProfile.values()){
            com.google.android.material.chip.Chip c=chip(a.icon+" "+a.title,a==selectedAgent);
            c.setOnClickListener(v->{
                selectedAgent=a;
                prefs.edit().putString(PREF_AGENT_PROFILE,a.id).apply();
                buildAgentChips();
            });
            agentChips.addView(c);
        }
        com.google.android.material.chip.Chip sk=chip(selectedSkill==null?"✨ مهارة":"✨ "+selectedSkill.title,selectedSkill!=null);
        sk.setOnClickListener(v->showSkills());
        agentChips.addView(sk);
        com.google.android.material.chip.Chip files=chip("📂 الملفات",false);
        files.setOnClickListener(v->showFiles());
        agentChips.addView(files);
        com.google.android.material.chip.Chip tools=chip("🧰 الأدوات",false);
        tools.setOnClickListener(v->showTools());
        agentChips.addView(tools);
        com.google.android.material.chip.Chip models=chip(prefs.getBoolean(PREF_ORCHESTRATE,false)?"🧠 الفريق ✓":"🧠 النماذج",prefs.getBoolean(PREF_ORCHESTRATE,false));
        models.setOnClickListener(v->showModelRoles());
        agentChips.addView(models);
        com.google.android.material.chip.Chip learning=chip("📈 التعلّم",false);
        learning.setOnClickListener(v->showLearning());
        agentChips.addView(learning);
        com.google.android.material.chip.Chip screen=chip(ScreenControlService.instance==null?"🖐 تحكم بالشاشة":"🖐 التحكم مفعّل",ScreenControlService.instance!=null);
        screen.setOnClickListener(v->{
            if(ScreenControlService.instance!=null){setWorking(false,"التحكم بالشاشة مفعّل");return;}
            guideScreenControl();
        });
        agentChips.addView(screen);
        com.google.android.material.chip.Chip settings=chip("⚙",false);
        settings.setOnClickListener(v->showSettings());
        agentChips.addView(settings);
    }

    private com.google.android.material.chip.Chip chip(String text,boolean selected){
        com.google.android.material.chip.Chip c=new com.google.android.material.chip.Chip(this);
        c.setText(text);
        c.setTextColor(android.graphics.Color.WHITE);
        c.setChipBackgroundColor(android.content.res.ColorStateList.valueOf(android.graphics.Color.parseColor(selected?"#7C3AED":"#22304F")));
        c.setChipStrokeWidth(0f);
        return c;
    }

    private void updateEmptyState(){
        boolean empty=adapter.getItemCount()==0;
        emptyView.setVisibility(empty?View.VISIBLE:View.GONE);
        if(!empty)return;
        examples.removeAllViews();
        TextView title=new TextView(this);
        title.setGravity(android.view.Gravity.CENTER);
        title.setText(!assistantMode&&!agentMode?"كيف أقدر أساعدك؟\n"+(engine==null?"اختر نموذجاً من الأعلى (المقترح: Qwen3.5 4B)":"🌐 بحث في النت • 🧮 حسابات دقيقة • 🌤 طقس • 💱 عملات • 💭 تفكير"):assistantMode?"📱 مساعد الهاتف\nالأوامر اليومية تُنفَّذ فوراً بدون نموذج. المهام الأخرى يقوم بها النموذج على الشاشة خطوة بخطوة.":agentMode?"🛠 وضع البرمجة مع Termux\nاكتب طلبك وسيُكتب الكود ويُشغَّل ويُصلَح تلقائياً.":"💬 محادثة محلية بالكامل على هاتفك");
        title.setTextColor(getResources().getColor(R.color.text_primary,getTheme()));
        title.setTextSize(17);
        title.setPadding(0,0,0,24);
        examples.addView(title);
        String[] prompts=assistantMode?new String[]{
                "افتح يوتيوب وابحث عن فيروز",
                "ابعت لماما على الواتس: رح اتأخر شوي",
                "اتصل بأحمد",
                "صحيني الساعة 7 الصبح",
                "شغّل الكشاف",
                "شغّل البلوتوث",
                "ابعت لسامر على تلغرام: وصلت",
                "افتح الإعدادات وشوف نسخة أندرويد"}
                :agentMode?new String[]{
                "اكتب آلة حاسبة بسيطة بلغة Python",
                "اكتب برنامجاً يطبع أول 20 عدداً أولياً مع اختبارات",
                "/data اكتب برنامجاً يحسب متوسط الدرجات من ملف CSV",
                "/sqlite اصنع دفتر ملاحظات بقاعدة بيانات",
                "كم نسبة البطارية؟",
                "/explain كيف يعمل main.py؟"}
                :new String[]{"شو آخر أخبار التكنولوجيا اليوم؟","كيف الطقس بدمشق بكرا؟","كم 17.5% من 2340؟","حوّل 100 دولار لليرة التركية","اكتب دالة Python ترتب قائمة بدون sort مع شرح","لخّص لي قصة فيلم Inception بخمس نقاط"};
        for(String p:prompts){
            com.google.android.material.button.MaterialButton b=new com.google.android.material.button.MaterialButton(this,null,com.google.android.material.R.attr.materialButtonOutlinedStyle);
            b.setText(p);b.setAllCaps(false);b.setTextColor(android.graphics.Color.WHITE);
            b.setOnClickListener(v->{inputBox.setText(p);inputBox.setSelection(p.length());});
            examples.addView(b);
        }
        if(agentMode){
            TextView hint=new TextView(this);
            hint.setText("\nأوامر سريعة: /code /plan /test /fix /explain /phone\nومهارات: /interactive /scraper /api /data /sqlite /bash /files /algo …");
            hint.setTextColor(getResources().getColor(R.color.text_secondary,getTheme()));
            hint.setTextSize(13);
            examples.addView(hint);
        }
    }

    /** Assign a GGUF to each role (manager / coder / language) and turn team mode on or off. */
    private void showModelRoles(){
        final File[] files=modelFiles().toArray(new File[0]);
        String[] roles={ROLE_MANAGER,ROLE_CODER,ROLE_LANGUAGE};
        String[] labels=new String[roles.length+4];
        for(int i=0;i<roles.length;i++){
            String p=prefs.getString(PREF_ROLE+roles[i],"");
            labels[i]=(ROLE_MANAGER.equals(roles[i])?"🧭 ":ROLE_CODER.equals(roles[i])?"💻 ":"🗣 ")+roleTitle(roles[i])+": "+(p.isEmpty()?"النموذج الأساسي":new File(p).getName());
        }
        boolean on=prefs.getBoolean(PREF_ORCHESTRATE,false);
        labels[3]=(on?"✅":"⬜")+" وضع الفريق: المدير يوجّه، والمختص ينفّذ، ونموذج اللغة يجيب";
        labels[4]=(prefs.getBoolean(PREF_LANG_SUMMARY,true)?"✅":"⬜")+" ملخص بلغتك من نموذج اللغة بعد كل برنامج";
        String draft=prefs.getString(PREF_DRAFT,"");
        labels[5]=(prefs.getBoolean(PREF_SPECULATIVE,true)?"✅":"⬜")+" ⚡ تسريع تخميني بنموذج مساعد صغير: "
                +(draft.isEmpty()?"تلقائي (ملف 0.5B)":new File(draft).getName())+" — يعمل مع نموذج 3B أو أكبر";
        StringBuilder live=new StringBuilder("🗂 محمّل الآن معاً: ");
        for(String p:pool.loadedPaths())live.append(new File(p).getName()).append("  ");
        labels[6]=pool.loadedPaths().isEmpty()?"🗂 لا يوجد نموذج محمّل":live.toString();
        new AlertDialog.Builder(this).setTitle("🧠 نماذج الفريق (تبقى محمّلة معاً حسب الذاكرة)").setItems(labels,(d,which)->{
            if(which==3){prefs.edit().putBoolean(PREF_ORCHESTRATE,!on).apply();buildAgentChips();showModelRoles();return;}
            if(which==4){prefs.edit().putBoolean(PREF_LANG_SUMMARY,!prefs.getBoolean(PREF_LANG_SUMMARY,true)).apply();showModelRoles();return;}
            if(which==6){showModelRoles();return;}
            if(which==5){
                String[] opts=new String[files.length+2];
                opts[0]=prefs.getBoolean(PREF_SPECULATIVE,true)?"إيقاف التسريع":"تشغيل التسريع (تلقائي)";
                opts[1]="اختيار تلقائي للنموذج المساعد";
                for(int i=0;i<files.length;i++)opts[i+2]=files[i].getName();
                new AlertDialog.Builder(this).setTitle("⚡ النموذج المساعد (نفس عائلة النموذج الرئيسي)").setItems(opts,(d3,o)->{
                    if(o==0)prefs.edit().putBoolean(PREF_SPECULATIVE,!prefs.getBoolean(PREF_SPECULATIVE,true)).apply();
                    else prefs.edit().putBoolean(PREF_SPECULATIVE,true).putString(PREF_DRAFT,o==1?"":files[o-2].getAbsolutePath()).apply();
                    showModelRoles();
                }).show();
                return;
            }
            String role=roles[which];
            String[] choices=new String[files.length+2];
            choices[0]="النموذج الأساسي";
            for(int i=0;i<files.length;i++)choices[i+1]=files[i].getName()+String.format(java.util.Locale.US," (%.1f GB)",files[i].length()/1e9);
            choices[files.length+1]="📥 تنزيل نموذج جديد…";
            new AlertDialog.Builder(this).setTitle(roleTitle(role)).setItems(choices,(d2,c)->{
                if(c==files.length+1){chooseModelSource();return;}
                prefs.edit().putString(PREF_ROLE+role,c==0?"":files[c-1].getAbsolutePath()).apply();
                showModelRoles();
            }).show();
        }).show();
    }

    /** What the agent has learned from real results, and export for fine-tuning elsewhere. */
    private void showLearning(){
        TextView v=new TextView(this);
        v.setText(experience.summary()+"\nكيف يتعلم: كل مهمة تُقيَّم بنتيجة Termux الحقيقية (مكافأة). يتذكر الحلول الناجحة ويعرضها كمثال للمهام المشابهة، ويتذكر الإصلاحات التي نجحت لكل خطأ، ويختار الاستراتيجية الأعلى مكافأة لكل نوع مهمة (خوارزمية UCB).\n\nتدريب أوزان النموذج نفسه يحتاج GPU: صدّر البيانات وادرّبه مجاناً على Google Colab (Unsloth) ثم حمّل ملف GGUF الناتج هنا.");
        v.setTextIsSelectable(true);v.setPadding(48,24,48,24);
        android.widget.ScrollView sv=new android.widget.ScrollView(this);sv.addView(v);
        new AlertDialog.Builder(this).setTitle("📈 التعلّم من التجربة").setView(sv)
                .setPositiveButton("تصدير بيانات التدريب",(d,w)->exportTraining())
                .setNegativeButton("إغلاق",null).show();
    }

    private void exportTraining(){
        executor.execute(()->{
            try{
                File dir=new File(getExternalFilesDir(null),"export");
                if(!dir.isDirectory()&&!dir.mkdirs())throw new java.io.IOException("export folder");
                File out=new File(dir,"newal_training.jsonl");
                int n=experience.exportTrainingData(out,AgentLoop.SYSTEM_PROMPT);
                Uri uri=androidx.core.content.FileProvider.getUriForFile(this,getPackageName()+".files",out);
                Intent send=new Intent(Intent.ACTION_SEND).setType("application/jsonl").putExtra(Intent.EXTRA_STREAM,uri)
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                runOnUiThread(()->{
                    setWorking(false,"صُدّر "+n+" مثال ناجح للتدريب");
                    if(n>0)startActivity(Intent.createChooser(send,"بيانات التدريب"));
                });
            }catch(Exception e){runOnUiThread(()->setWorking(false,"تعذر التصدير: "+safeMessage(e)));}
        });
    }

    private void showSkills(){
        List<Skills.Skill> all=Skills.all();
        String[] names=new String[all.size()+1];
        names[0]="بدون مهارة (اختيار تلقائي)";
        for(int i=0;i<all.size();i++)names[i+1]=all.get(i).icon+" "+all.get(i).title+"   /"+all.get(i).id;
        new AlertDialog.Builder(this).setTitle("✨ المهارات").setItems(names,(d,which)->{
            selectedSkill=which==0?null:all.get(which-1);
            buildAgentChips();
        }).show();
    }

    private void showFiles(){
        executor.execute(()->{
            ProjectWorkspace ws;
            try{ws=currentWorkspace();}catch(Exception e){return;}
            List<ProjectWorkspace.Entry> entries=ws.entries();
            runOnUiThread(()->{
                if(entries.isEmpty()){setWorking(false,"لا توجد ملفات في المشروع بعد");return;}
                String[] names=new String[entries.size()+1];
                for(int i=0;i<entries.size();i++){ProjectWorkspace.Entry e=entries.get(i);names[i]="📄 "+e.path+"  ("+e.size+" B"+(e.status==ProjectWorkspace.FileStatus.COMPLETE?"":", "+e.status)+")";}
                names[entries.size()]="⌨ آخر مخرجات التشغيل";
                new AlertDialog.Builder(this).setTitle("📂 ~/newal/projects/"+ws.id).setItems(names,(d,which)->{
                    if(which<entries.size())showFile(ws,entries.get(which).path);
                    else showLastRun(ws);
                }).show();
            });
        });
    }

    private void showFile(ProjectWorkspace ws,String path){
        String content;
        try{content=ws.read(path);}catch(Exception e){content="تعذرت القراءة: "+safeMessage(e);}
        showCode(path,content,true);
    }

    private void showLastRun(ProjectWorkspace ws){
        StringBuilder b=new StringBuilder();
        for(String stream:new String[]{"exec.stdout","exec.stderr"}){
            File f=ws.runLog(ws.attempt,stream);
            if(!f.isFile())continue;
            try{b.append("── ").append(stream).append(" ──\n").append(AgentLoop.tail(new String(ProjectWorkspace.readBytes(f),java.nio.charset.StandardCharsets.UTF_8),20000)).append('\n');}catch(Exception ignored){}
        }
        showCode("مخرجات المحاولة "+ws.attempt,b.length()==0?"لا توجد مخرجات محفوظة":b.toString(),false);
    }

    private void showCode(String title,String content,boolean highlight){
        TextView v=new TextView(this);
        v.setText(highlight?ChatAdapter.highlight(content):content);
        v.setTypeface(android.graphics.Typeface.MONOSPACE);v.setTextSize(12);v.setTextIsSelectable(true);
        v.setTextColor(android.graphics.Color.parseColor("#E6EDF3"));v.setBackgroundColor(android.graphics.Color.parseColor("#0D1117"));
        v.setTextDirection(View.TEXT_DIRECTION_LTR);v.setPadding(32,24,32,24);
        android.widget.HorizontalScrollView h=new android.widget.HorizontalScrollView(this);h.addView(v);
        android.widget.ScrollView sv=new android.widget.ScrollView(this);sv.addView(h);
        new AlertDialog.Builder(this).setTitle(title).setView(sv)
                .setPositiveButton("نسخ",(d,w)->copy(content))
                .setNegativeButton("إغلاق",null).show();
    }

    private void showSettings(){
        android.widget.LinearLayout box=new android.widget.LinearLayout(this);
        box.setOrientation(android.widget.LinearLayout.VERTICAL);box.setPadding(48,16,48,0);
        EditText attempts=numberField(box,"أقصى عدد محاولات إصلاح",prefs.getInt(PREF_MAX_ATTEMPTS,6));
        EditText timeout=numberField(box,"مهلة تشغيل البرنامج (ثوانٍ)",prefs.getInt(PREF_TIMEOUT_S,60));
        android.widget.CheckBox grammar=new android.widget.CheckBox(this);
        grammar.setText("إجبار صيغة الإخراج (أدق للنماذج الصغيرة)");grammar.setChecked(prefs.getBoolean(PREF_GRAMMAR,true));box.addView(grammar);
        android.widget.CheckBox online=new android.widget.CheckBox(this);
        online.setText("أدوات الإنترنت (بحث، طقس، عملات) — مجانية");online.setChecked(prefs.getBoolean(PREF_ONLINE,true));box.addView(online);
        EditText ctx=numberField(box,"طول الذاكرة (tokens): 2048 / 4096 / 8192 — الأكبر يحتاج RAM أكثر",prefs.getInt(PREF_CONTEXT,CONTEXT_TOKENS));
        android.widget.CheckBox autoTest=new android.widget.CheckBox(this);
        autoTest.setText("اختبارات تلقائية بعد نجاح البرنامج");autoTest.setChecked(prefs.getBoolean(PREF_AUTO_TEST,false));box.addView(autoTest);
        new AlertDialog.Builder(this).setTitle("⚙ الإعدادات").setView(box).setPositiveButton("حفظ",(d,w)->{
            prefs.edit().putInt(PREF_MAX_ATTEMPTS,clamp(attempts,1,20,6)).putInt(PREF_TIMEOUT_S,clamp(timeout,5,1800,60))
                    .putBoolean(PREF_GRAMMAR,grammar.isChecked()).putBoolean(PREF_AUTO_TEST,autoTest.isChecked())
                    .putBoolean(PREF_ONLINE,online.isChecked()).apply();
            int newCtx=clamp(ctx,1024,32768,CONTEXT_TOKENS);
            if(newCtx!=prefs.getInt(PREF_CONTEXT,CONTEXT_TOKENS)){
                prefs.edit().putInt(PREF_CONTEXT,newCtx).apply();
                engine=null;
                executor.execute(()->{pool.closeAll();runOnUiThread(this::restoreSavedModel);});
            }
            setWorking(false,"تم حفظ الإعدادات");
        }).setNegativeButton("إلغاء",null).show();
    }

    private EditText numberField(android.widget.LinearLayout box,String label,int value){
        TextView l=new TextView(this);l.setText(label);box.addView(l);
        EditText e=new EditText(this);e.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);e.setText(String.valueOf(value));box.addView(e);
        return e;
    }

    private static int clamp(EditText e,int min,int max,int def){
        try{return Math.max(min,Math.min(max,Integer.parseInt(e.getText().toString().trim())));}catch(NumberFormatException ex){return def;}
    }

    private void copy(String text){
        android.content.ClipboardManager cm=(android.content.ClipboardManager)getSystemService(CLIPBOARD_SERVICE);
        if(cm!=null)cm.setPrimaryClip(android.content.ClipData.newPlainText("NewAl",text));
        android.widget.Toast.makeText(this,"تم النسخ",android.widget.Toast.LENGTH_SHORT).show();
    }

    /** Buttons on result cards act on the current project. */
    private final class ResultActions implements ChatAdapter.Actions{
        @Override public void onRerun(){
            if(generating||engine==null)return;
            runAgentTask(null,AgentLoop::rerun);
        }
        @Override public void onFiles(){showFiles();}
        @Override public void onOpenTermux(){
            executor.execute(()->{
                try{
                    ProjectWorkspace ws=currentWorkspace();
                    CommandPlanner.Plan plan=CommandPlanner.plan(ws.completeShas().keySet(),p->{try{return ws.read(p);}catch(Exception e){return "";}},ws.runCommandOverride);
                    if(plan.command!=null)runOnUiThread(()->openInTermux(ws.id,plan.command));
                }catch(Exception e){runOnUiThread(()->setWorking(false,safeMessage(e)));}
            });
        }
        @Override public void onUndo(){
            if(generating)return;
            executor.execute(()->{
                try{
                    ProjectWorkspace ws=currentWorkspace();
                    ws.attach(termuxBridge());
                    List<String> reverted=ws.undoLastAttempt();
                    runOnUiThread(()->setWorking(false,reverted.isEmpty()?"لا يوجد ما يُتراجع عنه":"↩ تم التراجع عن: "+String.join(", ",reverted)));
                }catch(Exception e){runOnUiThread(()->setWorking(false,"تعذر التراجع: "+safeMessage(e)));}
            });
        }
        @Override public void onCopy(String text){copy(text);}
        @Override public void onRegenerate(ChatMessage answer){
            if(generating)return;
            int pos=adapter.indexOf(answer.id);
            if(pos<1)return;
            ChatMessage question=adapter.get(pos-1);
            if(question.role!=ChatMessage.ROLE_USER)return;
            adapter.truncate(pos-1);
            executor.execute(()->historyStore.deleteFrom(question.id));
            startChat(question.text,false);
        }
        @Override public void onEdit(ChatMessage question){
            if(generating)return;
            EditText e=new EditText(MainActivity.this);
            e.setText(question.text);e.setSelection(question.text.length());
            new AlertDialog.Builder(MainActivity.this).setTitle("تعديل السؤال").setView(e)
                    .setPositiveButton("إرسال",(d,w)->{
                        String t=e.getText().toString().trim();
                        if(t.isEmpty())return;
                        int pos=adapter.indexOf(question.id);
                        if(pos>=0)adapter.truncate(pos);
                        executor.execute(()->historyStore.deleteFrom(question.id));
                        if(assistantMode)startAssistant(t,false);else startChat(t,false);
                    })
                    .setNeutralButton("نسخ",(d,w)->copy(question.text))
                    .setNegativeButton("إلغاء",null).show();
        }
        @Override public void onSpeak(String text){speakNow(text);}
        @Override public void onShare(String text){
            startActivity(Intent.createChooser(new Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT,text),null));
        }
        @Override public void onOpenUrl(String url){
            try{startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(url)));}catch(Exception ignored){}
        }
    }

    // ------------------------------------------------------------------ chat (ChatGPT-style)

    /** Everyday phone commands also work in the chat, except web searches, which the chat answers itself. */
    private static boolean phoneCommandInChat(List<QuickCommands.Command> c){
        if(c==null)return false;
        for(QuickCommands.Command x:c)if(x.kind.equals("web_search")||x.kind.equals("type"))return false;
        return true;
    }

    private void startChat(String text,boolean spoken){
        List<QuickCommands.Command> commands=QuickCommands.parse(text,java.time.LocalTime.now());
        if(phoneCommandInChat(commands)){startAssistant(text,spoken);return;}
        if(engine==null){setWorking(false,"اختر نموذجاً أولاً من زر النموذج في الأعلى");return;}
        setGenerating(true);
        executor.execute(()->{
            long userId=historyStore.append(ChatMessage.ROLE_USER,text);long t0=System.currentTimeMillis();
            memoryManager.rememberExplicit(text);
            runOnUiThread(()->{adapter.add(new ChatMessage(userId,ChatMessage.ROLE_USER,text,t0));scrollToEnd();setWorking(true,"…");});
            String answer=null;
            try{answer=runChatTurn();}
            catch(Exception e){final String err="❌ "+safeMessage(e);runOnUiThread(()->setWorking(false,err));}
            final String said=answer;
            if(spoken&&said!=null)speak(said);
            runOnUiThread(()->{setGenerating(false);refreshConversations();});
        });
    }

    /**
     * Answers the last user message of the open conversation with tools and live streaming, and
     * saves the answer. Runs on the worker thread; returns the answer text.
     */
    private String runChatTurn() throws Exception{
        LlamaEngine model=engineForRole(ROLE_LANGUAGE);
        List<ChatMessage> all=historyStore.loadAll();
        List<ChatMessage> history=new java.util.ArrayList<>();
        for(ChatMessage m:all)if(m.role==ChatMessage.ROLE_USER||m.role==ChatMessage.ROLE_ASSISTANT)history.add(m);
        if(history.size()>24)history=history.subList(history.size()-24,history.size());
        ChatSession.Options o=new ChatSession.Options();
        o.web=prefs.getBoolean(PREF_WEB,false);
        String style=model.toolStyle();
        o.xmlToolCalls=style.equals("xml");
        o.lfmTools=style.equals("lfm");
        o.date=new java.text.SimpleDateFormat("EEEE yyyy-MM-dd",java.util.Locale.ENGLISH).format(new java.util.Date());
        o.extraSystem=memoryManager.memoryText();
        chatTools.online=prefs.getBoolean(PREF_ONLINE,true);
        chatTools.local=device;
        boolean think=prefs.getBoolean(PREF_THINK,false);
        int flags=LlamaEngine.FLAG_RAW|(think?LlamaEngine.FLAG_THINK:0);
        ChatSession session=new ChatSession((turns,l)->model.generate(turns,MAX_NEW_TOKENS,think?0.6f:TEMPERATURE,20,flags,null,l),chatTools);
        chatSession=session;
        AnswerStream stream=new AnswerStream();
        runOnUiThread(()->{adapter.add(stream.message(true));scrollToEnd();});
        ChatSession.Reply r;
        try{
            r=session.run(history,o,new ChatSession.Listener(){
                @Override public void onUpdate(String thinking,String answer){stream.update(thinking,answer);}
                @Override public void onTool(String name,String args,String result){stream.tool(name,args,result);}
            });
        }finally{chatSession=null;}
        stream.finish(r);
        org.json.JSONObject meta=stream.meta();
        String answer=r.answer.isEmpty()?"…":r.answer;
        long id=historyStore.append(ChatMessage.ROLE_ASSISTANT,answer,meta.toString());
        memoryManager.refreshExtractiveSummary(historyStore.loadAll());
        ChatMessage done=new ChatMessage(id,ChatMessage.ROLE_ASSISTANT,answer,System.currentTimeMillis(),meta.toString(),false);
        String metric=r.last==null?"":formatMetrics(r.last);
        runOnUiThread(()->{adapter.updateLast(done);setWorking(false,metric);});
        return answer;
    }

    /** The answer being written: throttled screen updates with thinking, tools and sources. */
    private final class AnswerStream{
        private String thinking="",answer="",stats="";
        private final java.util.LinkedHashMap<String,String> tools=new java.util.LinkedHashMap<>();
        private final org.json.JSONArray sources=new org.json.JSONArray();
        private boolean scheduled;

        synchronized void update(String th,String an){thinking=th;answer=an;post();}

        synchronized void tool(String name,String args,String result){
            String icon=name.equals("web_search")?"🔎 بحث":name.equals("calculator")?"🧮 حاسبة":name.equals("weather")?"🌤 طقس":name.equals("currency")?"💱 عملات"
                    :name.equals("read_page")?"📄 قراءة صفحة":name.equals("wikipedia")?"📚 ويكيبيديا":name.equals("current_time")?"🕒 الوقت":"🔧 "+name;
            tools.put(name,result==null?icon+"…":icon);
            post();
        }

        synchronized void finish(ChatSession.Reply r){
            thinking=r.thinking;answer=r.answer;
            for(String t:r.toolsUsed)if(t.equals("calculator")&&!tools.containsKey(t))tools.put(t,"🧮 حاسبة");
            for(WebTools.Result s:r.sources){
                try{sources.put(new org.json.JSONObject().put("t",s.title).put("u",s.url));}catch(org.json.JSONException ignored){}
            }
            if(r.last!=null&&r.last.tokensPerSecond>0)stats=String.format(java.util.Locale.US,"%.1f tok/s • %.1fs",r.last.tokensPerSecond,r.last.totalMs/1000.0);
        }

        synchronized org.json.JSONObject meta(){
            org.json.JSONObject m=new org.json.JSONObject();
            try{
                if(!thinking.isEmpty())m.put("thinking",thinking);
                if(!tools.isEmpty())m.put("tools",String.join("  •  ",tools.values()));
                if(sources.length()>0)m.put("sources",sources);
                if(!stats.isEmpty())m.put("stats",stats);
            }catch(org.json.JSONException ignored){}
            return m;
        }

        synchronized ChatMessage message(boolean streaming){
            return new ChatMessage(-1,ChatMessage.ROLE_ASSISTANT,answer,System.currentTimeMillis(),meta().toString(),streaming);
        }

        private void post(){
            if(scheduled)return;
            scheduled=true;
            ui.postDelayed(()->{
                ChatMessage m;
                synchronized(this){scheduled=false;m=message(true);}
                adapter.updateLast(m);
                scrollToEnd();
            },80);
        }
    }

    private void newChat(){
        if(generating)return;
        executor.execute(()->{
            historyStore.newConversation();
            for(LlamaEngine e:pool.engines())e.resetContext();
            runOnUiThread(()->{adapter.setAll(java.util.Collections.emptyList());setWorking(false,"محادثة جديدة");});
        });
    }

    private void openConversation(long id){
        if(generating)return;
        executor.execute(()->{
            historyStore.open(id);
            List<ChatMessage> all=historyStore.loadAll();
            runOnUiThread(()->{adapter.setAll(all);scrollToEnd();});
        });
    }

    private void refreshConversations(){
        executor.execute(()->{
            List<ChatHistoryStore.Conversation> list=historyStore.conversations();
            long current=historyStore.current();
            runOnUiThread(()->conversationList.setAdapter(new android.widget.BaseAdapter(){
                @Override public int getCount(){return list.size();}
                @Override public Object getItem(int i){return list.get(i);}
                @Override public long getItemId(int i){return list.get(i).id;}
                @Override public View getView(int i,View v,android.view.ViewGroup parent){
                    TextView t=v instanceof TextView?(TextView)v:new TextView(MainActivity.this);
                    ChatHistoryStore.Conversation c=list.get(i);
                    t.setText(c.title.isEmpty()?"محادثة":c.title);
                    t.setSingleLine(true);t.setEllipsize(android.text.TextUtils.TruncateAt.END);
                    t.setTextSize(15);t.setTextColor(getResources().getColor(R.color.text_primary,getTheme()));
                    t.setPadding(MarkdownView.dp(MainActivity.this,18),MarkdownView.dp(MainActivity.this,12),MarkdownView.dp(MainActivity.this,18),MarkdownView.dp(MainActivity.this,12));
                    t.setBackgroundColor(c.id==current?android.graphics.Color.parseColor("#1E2433"):android.graphics.Color.TRANSPARENT);
                    t.setTextDirection(View.TEXT_DIRECTION_FIRST_STRONG);
                    return t;
                }
            }));
        });
    }

    private void conversationMenu(long id){
        new AlertDialog.Builder(this).setItems(new String[]{"✏️ إعادة تسمية","🗑 حذف"},(d,w)->{
            if(w==0){
                EditText e=new EditText(this);
                new AlertDialog.Builder(this).setTitle("اسم المحادثة").setView(e).setPositiveButton("حفظ",(x,y)->
                        executor.execute(()->{historyStore.rename(id,e.getText().toString().trim());refreshConversations();})).setNegativeButton("إلغاء",null).show();
            }else executor.execute(()->{
                boolean wasOpen=historyStore.current()==id;
                historyStore.deleteConversation(id);
                List<ChatMessage> all=wasOpen?java.util.Collections.emptyList():null;
                runOnUiThread(()->{if(all!=null)adapter.setAll(all);refreshConversations();});
            });
        }).show();
    }

    /** A text file (code, notes, CSV …) goes into the message so the model can read it. */
    private void onAttach(Uri uri){
        if(uri==null)return;
        executor.execute(()->{
            try(InputStream in=getContentResolver().openInputStream(uri)){
                if(in==null)return;
                ByteArrayOutputStream out=new ByteArrayOutputStream();
                byte[] buf=new byte[8192];int n;
                while((n=in.read(buf))>0&&out.size()<60_000)out.write(buf,0,n);
                String text=new String(out.toByteArray(),java.nio.charset.StandardCharsets.UTF_8);
                if(text.length()>12_000)text=text.substring(0,12_000)+"\n…";
                String name=uri.getLastPathSegment()==null?"file":uri.getLastPathSegment().replaceFirst(".*[/:]","");
                String block="📎 "+name+"\n```\n"+text+"\n```\n";
                runOnUiThread(()->{inputBox.setText(block+inputBox.getText());inputBox.setSelection(inputBox.getText().length());});
            }catch(Exception e){runOnUiThread(()->setWorking(false,"تعذر قراءة الملف: "+safeMessage(e)));}
        });
    }

    private void speakNow(String text){
        boolean on=prefs.getBoolean(PREF_SPEAK,true);
        prefs.edit().putBoolean(PREF_SPEAK,true).apply();
        speak(text.replaceAll("(?s)```.*?```"," ").replaceAll("[#*`|>]",""));
        prefs.edit().putBoolean(PREF_SPEAK,on).apply();
    }

    /** One-time setup: Termux installed, permission granted, agent reachable. Afterwards it is automatic. */
    private void ensureTermuxReady(){
        if(!TermuxLauncher.isInstalled(this)){
            new AlertDialog.Builder(this).setTitle("Termux غير مثبت")
                    .setMessage("مساعد الهاتف (فتح التطبيقات، المنبه، الإعدادات…) يعمل الآن.\nلتشغيل البرامج التي يكتبها ثبّت Termux من F-Droid أو GitHub (وليس Google Play).")
                    .setPositiveButton("حسناً",null).show();
            return;
        }
        if(!TermuxLauncher.hasPermission(this)){requestPermissions(new String[]{TermuxLauncher.PERMISSION},REQ_TERMUX);return;}
        setWorking(true,"جاري الاتصال بـTermux…");
        new Thread(()->{
            try{
                TermuxBridge b=termuxBridge();
                b.ensureConnected(12000);
                long rtt=b.ping(3000);
                String py=b.agentInfo()==null?"":b.agentInfo().optString("python");
                int tools=b.listTools().length();
                runOnUiThread(()->setWorking(false,"Termux جاهز • Python "+py+" • "+rtt+"ms • "+tools+" أداة (اضغط مطولاً على Termux)"));
            }catch(Exception e){runOnUiThread(this::showTermuxSetup);}
        },"termux-connect").start();
    }

    @Override public void onRequestPermissionsResult(int requestCode,String[] permissions,int[] grantResults){
        super.onRequestPermissionsResult(requestCode,permissions,grantResults);
        if(requestCode==REQ_ALL){nextGrant();return;}
        if(requestCode==REQ_ASSIST){
            permissionGranted=grantResults.length>0&&grantResults[0]==android.content.pm.PackageManager.PERMISSION_GRANTED;
            CountDownLatch l=permissionLatch;if(l!=null)l.countDown();
            return;
        }
        if(requestCode!=REQ_TERMUX)return;
        if(grantResults.length>0&&grantResults[0]==android.content.pm.PackageManager.PERMISSION_GRANTED)ensureTermuxReady();
        else setWorking(false,"بدون إذن Termux: مساعد الهاتف يعمل، وتشغيل البرامج معطّل");
    }

    private void showTermuxSetup(){
        setWorking(false,"Termux يحتاج إعداداً لمرة واحدة");
        TextView cmd=new TextView(this);
        cmd.setText(TermuxLauncher.SETUP_COMMAND);cmd.setTextIsSelectable(true);cmd.setTextDirection(View.TEXT_DIRECTION_LTR);
        cmd.setTypeface(android.graphics.Typeface.MONOSPACE);cmd.setPadding(48,24,48,0);
        new AlertDialog.Builder(this).setTitle("إعداد Termux (مرة واحدة فقط)")
                .setMessage("1) اضغط «نسخ وفتح Termux» والصق الأمر في Termux واضغط Enter.\n2) عد إلى هنا واضغط «أعد المحاولة».\nبعدها يعمل التشغيل في الخلفية تلقائياً دون فتح Termux.")
                .setView(cmd)
                .setPositiveButton("نسخ وفتح Termux",(d,w)->{
                    android.content.ClipboardManager cm=(android.content.ClipboardManager)getSystemService(CLIPBOARD_SERVICE);
                    if(cm!=null)cm.setPrimaryClip(android.content.ClipData.newPlainText("termux setup",TermuxLauncher.SETUP_COMMAND));
                    Intent open=getPackageManager().getLaunchIntentForPackage(TermuxLauncher.PACKAGE);
                    if(open!=null)startActivity(open);
                })
                .setNeutralButton("أعد المحاولة",(d,w)->ensureTermuxReady())
                .setNegativeButton("لاحقاً",null)
                .show();
    }

    /** Lists the tools the model can use and explains how to add more (all free and local). */
    private void showTools(){
        if(!TermuxLauncher.hasPermission(this)){setAgentMode(true);return;}
        setWorking(true,"جاري قراءة الأدوات…");
        new Thread(()->{
            StringBuilder b=new StringBuilder();
            try{
                TermuxBridge br=termuxBridge();
                br.ensureConnected(12000);
                org.json.JSONArray tools=br.listTools();
                if(tools.length()==0)b.append("لا توجد أدوات بعد.\n");
                for(int i=0;i<tools.length();i++){
                    org.json.JSONObject t=tools.optJSONObject(i);
                    b.append("• ").append(t.optString("name")).append(" (").append(t.optString("source")).append(")\n  ")
                            .append(t.optString("description")).append('\n');
                }
            }catch(Exception e){b.append("تعذر الاتصال بـTermux: ").append(safeMessage(e)).append('\n');}
            b.append("\nإضافة أدوات (مجانية وتعمل محلياً):\n")
                    .append("1) ميزات الهاتف: ثبّت تطبيق Termux:API من F-Droid ثم في Termux:\n   pkg install termux-api\n")
                    .append("2) أداة خاصة: مجلد في ~/newal/tools/<الاسم>/ فيه tool.json:\n")
                    .append("   {\"name\":\"...\",\"description\":\"...\",\"parameters\":{\"x\":\"string\"},\"command\":\"python run.py\"}\n")
                    .append("   يستلم الأمر المدخلات JSON على stdin ويطبع النتيجة.\n")
                    .append("3) خوادم MCP: ملف ~/newal/mcp.json:\n")
                    .append("   {\"servers\":{\"اسم\":{\"command\":\"...\",\"args\":[...]}}}\n")
                    .append("الأدوات الجديدة تظهر للنموذج تلقائياً في الطلب التالي.");
            String text=b.toString();
            runOnUiThread(()->{
                setWorking(false,"جاهز");
                TextView v=new TextView(this);
                v.setText(text);v.setTextIsSelectable(true);v.setTextDirection(View.TEXT_DIRECTION_LTR);v.setPadding(48,24,48,24);
                android.widget.ScrollView sv=new android.widget.ScrollView(this);sv.addView(v);
                new AlertDialog.Builder(this).setTitle("الأدوات والإضافات").setView(sv).setPositiveButton("حسناً",null).show();
            });
        },"termux-tools").start();
    }

    private synchronized TermuxBridge termuxBridge()throws java.io.IOException{
        TermuxBridge b=bridge;
        if(b!=null)return b;
        String token=prefs.getString(PREF_AGENT_TOKEN,null);
        if(token==null){
            byte[] r=new byte[16];new java.security.SecureRandom().nextBytes(r);
            token=ProjectWorkspace.hex(r);prefs.edit().putString(PREF_AGENT_TOKEN,token).apply();
        }
        String source;
        try(InputStream in=getAssets().open("newal_agent.py")){
            ByteArrayOutputStream buf=new ByteArrayOutputStream();byte[] chunk=new byte[8192];int n;
            while((n=in.read(chunk))>0)buf.write(chunk,0,n);
            source=buf.toString("UTF-8");
        }
        b=new TermuxBridge("127.0.0.1",AGENT_PORT,token,source,new TermuxLauncher(this));
        bridge=b;
        return b;
    }

    private ProjectWorkspace currentWorkspace()throws java.io.IOException{
        if(workspace==null){
            File root=new File(getFilesDir(),"projects");
            if(!root.isDirectory()&&!root.mkdirs())throw new java.io.IOException("تعذر إنشاء مجلد المشاريع");
            workspace=ProjectWorkspace.openLatest(root);
            if(workspace==null)workspace=ProjectWorkspace.create(root);
        }
        return workspace;
    }

    /** After a crash or restart, an unfinished task continues from its last safe state. */
    private void resumeInterruptedAgentTask(){
        if(!agentMode||!TermuxLauncher.hasPermission(this)||generating)return;
        executor.execute(()->{
            try{
                ProjectWorkspace ws=currentWorkspace();
                java.util.Set<String> terminal=new java.util.HashSet<>(java.util.Arrays.asList("IDLE","SUCCESS","FAILED","WAITING_FOR_USER"));
                if(ws.request.isEmpty()||terminal.contains(ws.state))return;
                runOnUiThread(()->startAgent(ws.request,true));
            }catch(Exception ignored){}
        });
    }

    private void startAgent(String request,boolean resume){
        Skills.Parsed parsed=Skills.parse(request);
        AgentProfile agent=parsed.agent!=null?parsed.agent:selectedAgent;
        Skills.Skill skill=parsed.skill!=null?parsed.skill:selectedSkill;
        String text=parsed.text.isEmpty()?request:parsed.text;
        boolean orchestrate=!resume&&parsed.agent==null&&agent==AgentProfile.AUTO&&prefs.getBoolean(PREF_ORCHESTRATE,false);
        runAgentTask(resume?null:request,loop->{
            if(resume)return loop.resume();
            if(!orchestrate)return loop.run(text,agent,skill);
            // Manager → specialist → language model, one model at a time.
            Orchestrator.Route route=managerRoute(text);
            if(route.kind==Orchestrator.Kind.CHAT)return chatAnswer(text);
            if(!route.plan.isEmpty())loop.setPlan(route.planText());
            AgentLoop.Outcome o=loop.run(text,route.agent(),skill);
            if(o.state==AgentLoop.State.SUCCESS&&route.kind==Orchestrator.Kind.CODE&&prefs.getBoolean(PREF_LANG_SUMMARY,true)){
                String summary=languageSummary(text,o.message);
                if(!summary.isEmpty())return new AgentLoop.Outcome(o.state,summary+"\n\n"+o.message,o.interactiveCommand);
            }
            return o;
        });
    }

    private Orchestrator.Route managerRoute(String text){
        List<ChatMessage> turns=new java.util.ArrayList<>();
        turns.add(new ChatMessage(-1,ChatMessage.ROLE_SYSTEM,Orchestrator.MANAGER_PROMPT,0));
        turns.add(new ChatMessage(-1,ChatMessage.ROLE_USER,text,0));
        live.start("🧭 ",ChatMessage.ROLE_CODE);
        try{
            GenerationResult r=engineForRole(ROLE_MANAGER).generate(turns,120,0f,1,LlamaEngine.FLAG_RAW,Orchestrator.ROUTE_GRAMMAR,live::append);
            return Orchestrator.parse(r.text);
        }catch(Exception e){
            return Orchestrator.parse("ROUTE: code");
        }
    }

    private AgentLoop.Outcome chatAnswer(String text){
        live.start("",ChatMessage.ROLE_ASSISTANT);
        List<ChatMessage> history=historyStore.loadAll();
        List<ChatMessage> turns=memoryManager.buildTurns(history);
        GenerationResult r=engineForRole(ROLE_LANGUAGE).generate(turns,MAX_NEW_TOKENS*2,TEMPERATURE,TOP_K,0,live::append);
        live.end();
        String answer=cleanAssistantText(r.text);
        return new AgentLoop.Outcome(AgentLoop.State.SUCCESS,answer.isEmpty()?"…":answer,null,true);
    }

    private String languageSummary(String request,String outcome){
        try{
            ProjectWorkspace ws=currentWorkspace();
            List<String> files=new java.util.ArrayList<>(ws.completeShas().keySet());
            List<ChatMessage> turns=new java.util.ArrayList<>();
            turns.add(new ChatMessage(-1,ChatMessage.ROLE_USER,Orchestrator.summaryPrompt(request,outcome,files),0));
            live.start("",ChatMessage.ROLE_ASSISTANT);
            GenerationResult r=engineForRole(ROLE_LANGUAGE).generate(turns,200,0.3f,TOP_K,0,live::append);
            live.end();
            return cleanAssistantText(r.text);
        }catch(Exception e){return "";}
    }

    /** Runs one agent action (new task, resume or rerun) in the background with live UI and notifications. */
    private void runAgentTask(String userText,java.util.function.Function<AgentLoop,AgentLoop.Outcome> action){
        setGenerating(true);
        if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)!=android.content.pm.PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{android.Manifest.permission.POST_NOTIFICATIONS},REQ_NOTIFY);
        AgentService.start(this,userText==null?"متابعة المهمة":AgentLoop.tail(userText,200));
        boolean[] shownInline={false};
        executor.execute(()->{
            if(userText!=null){
                long userId=historyStore.append(ChatMessage.ROLE_USER,userText);long t=System.currentTimeMillis();
                runOnUiThread(()->{adapter.add(new ChatMessage(userId,ChatMessage.ROLE_USER,userText,t));scrollToEnd();});
            }
            String text;
            try{
                ProjectWorkspace ws=currentWorkspace();
                TermuxBridge b=termuxBridge();
                loadUserSkills(b);
                AgentLoop loop=newLoop(ws,b);
                agentLoop=loop;
                live.persist=true;
                AgentLoop.Outcome o=action.apply(loop);
                live.end();
                shownInline[0]=o.shownInline;
                text=describe(o,loop,ws);
                if(o.state==AgentLoop.State.SUCCESS&&o.interactiveCommand==null&&prefs.getBoolean(PREF_AUTO_TEST,false)
                        &&(loop.agent()==AgentProfile.CODER||loop.agent()==AgentProfile.ARCHITECT)&&!hasTests(ws)){
                    // Verification pass: a tester agent writes and runs tests for what was just built.
                    runOnUiThread(()->setWorking(true,"🧪 كتابة اختبارات للتحقق…"));
                    AgentLoop tester=newLoop(ws,b);
                    agentLoop=tester;
                    AgentLoop.Outcome t=tester.run("Write unit tests for this project and run them.",AgentProfile.TESTER,Skills.byId("tests"));
                    live.end();
                    text+="\n\n"+describe(t,tester,ws);
                }
                if(o.interactiveCommand!=null)offerInteractive(ws.id,o.interactiveCommand);
            }catch(Exception e){
                live.end();
                text="❌ "+safeMessage(e);
            }finally{agentLoop=null;live.persist=false;}
            final String summary=text;
            if(shownInline[0]){
                AgentService.finish(getApplicationContext(),"✅ تم",AgentLoop.tail(summary,300));
                runOnUiThread(()->{scrollToEnd();setGenerating(false);setWorking(false,"جاهز");});
                return;
            }
            AgentService.finish(getApplicationContext(),summary.startsWith("✅")?"✅ اكتملت المهمة":summary.startsWith("⚠️")?"⚠️ المهمة تحتاج تدخلك":"❌ فشلت المهمة",summary);
            long id=historyStore.append(ChatMessage.ROLE_RESULT,summary);long t=System.currentTimeMillis();
            runOnUiThread(()->{adapter.add(new ChatMessage(id,ChatMessage.ROLE_RESULT,summary,t));scrollToEnd();setGenerating(false);});
        });
    }

    private AgentLoop newLoop(ProjectWorkspace ws,TermuxBridge b){
        AgentLoop[] self=new AgentLoop[1];
        AgentLoop loop=new AgentLoop(roleModel(()->self[0]==null?ROLE_CODER:roleFor(self[0].agent()),true),b,ws,new UiAgentListener());
        self[0]=loop;
        loop.planner=roleModel(()->ROLE_MANAGER,true);
        loop.localTools=device;
        loop.experience=experience;
        loop.maxAttempts=prefs.getInt(PREF_MAX_ATTEMPTS,6);
        loop.execTimeoutMs=prefs.getInt(PREF_TIMEOUT_S,60)*1000L;
        return loop;
    }

    private String describe(AgentLoop.Outcome o,AgentLoop loop,ProjectWorkspace ws){
        String icon=o.state==AgentLoop.State.SUCCESS?"✅ ":o.state==AgentLoop.State.WAITING_FOR_USER?"⚠️ ":"❌ ";
        Skills.Skill sk=loop.skill();
        return icon+o.message+"\n"+loop.agent().icon+" "+loop.agent().title+(sk==null?"":" • "+sk.icon+" "+sk.title)
                +" • 📁 ~/newal/projects/"+ws.id;
    }

    private static boolean hasTests(ProjectWorkspace ws){
        for(ProjectWorkspace.Entry e:ws.entries())if(e.path.startsWith("tests/")||e.path.matches(".*(^|/)test_[^/]*\\.py"))return true;
        return false;
    }

    private void offerInteractive(String projectId,String command){
        runOnUiThread(()->new AlertDialog.Builder(this).setTitle("برنامج تفاعلي")
                .setMessage("البرنامج يعمل وينتظر إدخالك. تشغيله الآن في Termux لتكتب له؟\n\n"+command)
                .setPositiveButton("شغّل في Termux",(d,w)->openInTermux(projectId,command))
                .setNegativeButton("لاحقاً",null).show());
    }

    private void openInTermux(String projectId,String command){
        try{new TermuxLauncher(this).openInteractive(projectId,command);}
        catch(Exception e){setWorking(false,"تعذر فتح Termux: "+safeMessage(e));}
    }

    private void loadUserSkills(TermuxBridge b){
        if(!b.isConnected())return; // never wait for Termux just to read skills
        try{
            org.json.JSONArray a=b.userSkills();
            List<Skills.Skill> list=new java.util.ArrayList<>();
            for(int i=0;i<a.length();i++){org.json.JSONObject o=a.getJSONObject(i);list.add(Skills.fromMarkdown(o.getString("id"),o.getString("markdown")));}
            Skills.setUserSkills(list);
        }catch(Exception ignored){}
    }

    /**
     * The model of a role, loaded on demand and kept loaded while RAM allows ({@link ModelPool});
     * roles run one after another, never in parallel. Called on the worker thread.
     */
    private LlamaEngine engineForRole(String role){
        String path=prefs.getString(PREF_ROLE+role,"");
        if((path.isEmpty()||!new File(path).isFile())&&ROLE_MANAGER.equals(role)){
            // Screen control works best with Qwen3.5 (measured); use one if it is on the phone.
            path="";
            for(File f:modelFiles()){
                String n=f.getName().toLowerCase(java.util.Locale.ROOT);
                // 2B: fast enough on mid-range phones; a 4B only when there is no 2B.
                if(n.startsWith("qwen3.5")&&!n.contains("0.8b")&&(path.isEmpty()||n.contains("-2b")))path=f.getAbsolutePath();
            }
        }
        if(path.isEmpty()||!new File(path).isFile())path=prefs.getString(PREF_MODEL_LOCAL_PATH,null);
        if(path==null)return engine;
        String draftPath=ROLE_LANGUAGE.equals(role)?null:draftFor(path);
        java.util.List<String> keep=draftPath==null?java.util.Collections.singletonList(path):java.util.Arrays.asList(path,draftPath);
        try{
            boolean cold=!pool.isLoaded(path);
            String name=new File(path).getName();
            long t=System.nanoTime();
            if(cold)runOnUiThread(()->setWorking(true,"🔄 تحميل ("+roleTitle(role)+"): "+name));
            LlamaEngine target=pool.get(path,keep);
            // Speculative decoding: a small same-family model drafts, the big one verifies.
            LlamaEngine d=draftPath==null?null:pool.get(draftPath,keep);
            target.setDraft(d,DRAFT_TOKENS);
            engine=target;
            loadedPath=path;
            if(cold){
                long ms=(System.nanoTime()-t)/1_000_000;
                runOnUiThread(()->setWorking(true,"🧠 "+roleTitle(role)+": "+name+(d==null?"":" + ⚡ مساعد")+" • "+ms+"ms • محمّل: "+pool.loadedPaths().size()));
            }
            return target;
        }catch(Exception e){
            throw new IllegalStateException("تعذر تحميل النموذج: "+safeMessage(e),e);
        }
    }

    private LlamaEngine newEngine(String path){
        int cores=Runtime.getRuntime().availableProcessors();
        int threads=Math.max(2,Math.min(6,cores));
        LlamaEngine e=new LlamaEngine(getApplicationContext(),path,prefs.getInt(PREF_CONTEXT,CONTEXT_TOKENS),threads);
        // The measured best thread count for generation on this phone; prompts use every core.
        e.setThreads(prefs.getInt("gen_threads",threads),cores);
        return e;
    }

    /**
     * Measures generation speed with 2, 3, 4, 6 and all cores and keeps the fastest. Phones mix
     * fast and slow cores (the Galaxy A16: 2 fast + 6 slow), and the slow ones can hold the fast
     * ones back, so the best count differs per phone. Done once automatically, or from the menu.
     */
    private void tuneSpeed(boolean manual){
        LlamaEngine e=engine;
        if(e==null){if(manual)setWorking(false,"حمّل نموذجاً أولاً");return;}
        if(generating)return;
        setGenerating(true);
        setWorking(true,"⚡ أقيس أسرع إعداد لهاتفك…");
        executor.execute(()->{
            int cores=Runtime.getRuntime().availableProcessors();
            java.util.TreeSet<Integer> candidates=new java.util.TreeSet<>();
            for(int t:new int[]{2,3,4,6,cores})if(t>=1&&t<=cores)candidates.add(t);
            List<ChatMessage> turns=new java.util.ArrayList<>();
            turns.add(new ChatMessage(0,ChatMessage.ROLE_USER,"Count from 1 to 80, separated by commas.",0));
            int best=prefs.getInt("gen_threads",Math.max(2,Math.min(6,cores)));
            double bestSpeed=0,before=0;
            StringBuilder log=new StringBuilder();
            try{
                e.setThreads(best,cores);
                e.resetContext();
                before=e.generate(turns,24,0f,1,LlamaEngine.FLAG_RAW,null,null).tokensPerSecond;   // also warms up
                for(int t:candidates){
                    e.setThreads(t,cores);
                    e.resetContext();
                    GenerationResult r=e.generate(turns,32,0f,1,LlamaEngine.FLAG_RAW,null,null);
                    if(r.stopReason==GenerationResult.STOP_CANCELLED)throw new IllegalStateException("أُوقف القياس");
                    double speed=r.tokensPerSecond;
                    log.append(String.format(java.util.Locale.US,"%d:%.1f ",t,speed));
                    if(speed>bestSpeed){bestSpeed=speed;best=t;}
                }
            }catch(Exception ex){
                final String err=safeMessage(ex);
                runOnUiThread(()->{setGenerating(false);setWorking(false,"تعذّر القياس: "+err);});
                return;
            }
            prefs.edit().putInt("gen_threads",best).apply();
            for(LlamaEngine x:pool.engines())x.setThreads(best,cores);
            e.resetContext();
            final String msg=String.format(java.util.Locale.US,"⚡ أفضل إعداد: %d أنوية • %.1f tok/s (كان %.1f)",best,bestSpeed,before);
            final String detail=log.toString().trim();
            runOnUiThread(()->{
                setGenerating(false);
                setWorking(false,msg);
                if(manual)new AlertDialog.Builder(this).setTitle("⚡ ضبط السرعة").setMessage(msg+"\n\nالقياسات (أنوية:tok/s): "+detail).setPositiveButton("تمام",null).show();
            });
        });
    }

    private long availableRam(){
        android.app.ActivityManager am=(android.app.ActivityManager)getSystemService(ACTIVITY_SERVICE);
        android.app.ActivityManager.MemoryInfo mi=new android.app.ActivityManager.MemoryInfo();
        if(am!=null)am.getMemoryInfo(mi);
        return mi.availMem;
    }

    /**
     * Draft model for speculative decoding, or null. Only worth it when the main model is at
     * least 3x the draft's size (measured: ~1.2x faster for 3B with a 0.5B draft, slower for 1.5B).
     */
    private String draftFor(String targetPath){
        if(!prefs.getBoolean(PREF_SPECULATIVE,true))return null;
        String d=prefs.getString(PREF_DRAFT,"");
        // Measured gains only for Qwen2.5 3B+ with a 0.5B draft; hybrid models (LFM2.5, Qwen3.5) got slower.
        if(!new File(targetPath).getName().toLowerCase(java.util.Locale.ROOT).contains("qwen2.5"))return null;
        if(d.isEmpty()){
            for(File f:modelFiles())if(f.getName().toLowerCase(java.util.Locale.ROOT).contains("0.5b")&&(d.isEmpty()||f.length()<new File(d).length()))d=f.getAbsolutePath();
        }
        if(d.isEmpty()||d.equals(targetPath)||!new File(d).isFile())return null;
        return new File(targetPath).length()>=3*new File(d).length()?d:null;
    }

    private static String roleTitle(String role){
        return ROLE_MANAGER.equals(role)?"المدير والمساعد":ROLE_CODER.equals(role)?"المبرمج":"اللغة";
    }

    private static String roleFor(AgentProfile a){
        return a==AgentProfile.AUTOMATOR||a==AgentProfile.EXPLAINER?ROLE_MANAGER:ROLE_CODER;
    }

    /** A model whose role (and so its GGUF) is decided at call time. */
    private AgentLoop.Model roleModel(java.util.function.Supplier<String> role,boolean codeGrammar){
        return new AgentLoop.Model(){
            // Greedy decoding for deterministic output; generate until the answer ends or the context is full.
            @Override public GenerationResult generate(List<ChatMessage> turns,TextListener l){
                String grammar=codeGrammar&&prefs.getBoolean(PREF_GRAMMAR,true)?AgentLoop.OUTPUT_GRAMMAR:null;
                return engineForRole(role.get()).generate(turns,0,0f,1,LlamaEngine.FLAG_RAW,grammar,l);
            }
            @Override public void cancel(){for(LlamaEngine e:pool.engines())e.cancel();}
            @Override public int contextTokens(){LlamaEngine e=engine;return e==null?CONTEXT_TOKENS:e.contextTokens();}
        };
    }

    private AgentLoop.Model agentModel(){return roleModel(()->ROLE_CODER,true);}

    private final class UiAgentListener implements AgentLoop.Listener{
        @Override public void onState(AgentLoop.State state,String detail){
            if((state==AgentLoop.State.GENERATING||state==AgentLoop.State.PATCHING)&&detail!=null)live.start("",ChatMessage.ROLE_CODE);
            else if(state==AgentLoop.State.RUNNING)live.start("$ "+detail+"\n",ChatMessage.ROLE_TERMINAL);
            String label=state.name()+(detail==null||detail.isEmpty()?"":" • "+AgentLoop.tail(detail,120));
            runOnUiThread(()->status.setText(label));
            AgentService.update(getApplicationContext(),label);
        }
        @Override public void onModelText(String delta){live.append(delta);}
        @Override public void onOutput(boolean stderr,String text){live.append(text);}
        @Override public void onMetrics(String line){
            try{
                org.json.JSONObject m=new org.json.JSONObject(line);
                String s=String.format(java.util.Locale.US,"#%d • أول token %sms • %s tok/s • أول كتابة %sms • تشغيل %sms • محاولة كاملة %sms",
                        m.optInt("attempt"),m.opt("time_to_first_token_ms"),m.opt("tokens_per_s"),m.opt("time_to_first_file_write_ms"),m.opt("exec_execution_ms"),m.opt("iteration_ms"))
                        +(m.optInt("draft_tokens")>0?String.format(java.util.Locale.US," • ⚡ قبول %d%%",100*m.optInt("draft_accepted")/m.optInt("draft_tokens")):"");
                runOnUiThread(()->status.setText(s));
            }catch(org.json.JSONException ignored){}
        }
        @Override public boolean confirm(String reason){
            CountDownLatch done=new CountDownLatch(1);boolean[] ok={false};
            runOnUiThread(()->new AlertDialog.Builder(MainActivity.this).setTitle("عملية تحتاج موافقتك").setMessage(reason).setCancelable(false)
                    .setPositiveButton("نفّذ",(d,w)->{ok[0]=true;done.countDown();})
                    .setNegativeButton("إلغاء",(d,w)->done.countDown()).show());
            try{if(!done.await(10,TimeUnit.MINUTES))return false;}catch(InterruptedException e){Thread.currentThread().interrupt();return false;}
            return ok[0];
        }
    }

    /** One chat bubble whose text streams in; UI updates are coalesced to one per frame-ish interval. */
    private final class LiveBubble{
        private static final int MAX_CHARS=8000;
        private final StringBuilder text=new StringBuilder();
        private boolean active,scheduled;
        private int role=ChatMessage.ROLE_ASSISTANT;
        /** Agent mode: finished bubbles (generated code, program output) are saved to chat history. */
        volatile boolean persist;

        void start(String header){start(header,ChatMessage.ROLE_ASSISTANT);}

        void start(String header,int newRole){
            final String previous;final int previousRole;
            synchronized(this){previous=active?snapshot():null;previousRole=role;text.setLength(0);text.append(header);active=true;role=newRole;}
            save(previous,previousRole);
            ui.post(()->{
                if(previous!=null)adapter.updateLast(bubble(previous,previousRole));
                adapter.add(bubble(header,newRole));scrollToEnd();
            });
        }

        void append(String s){
            synchronized(this){
                if(!active||s.isEmpty())return;
                text.append(s);
                if(text.length()>MAX_CHARS*2)text.delete(0,text.length()-MAX_CHARS);
                if(scheduled)return;
                scheduled=true;
            }
            ui.postDelayed(this::flush,40);
        }

        private void flush(){
            String snap;
            int r;
            synchronized(this){scheduled=false;if(!active)return;snap=snapshot();r=role;}
            adapter.updateLast(bubble(snap,r));scrollToEnd();
        }

        void end(){
            String snap;
            int r;
            synchronized(this){if(!active)return;snap=snapshot();active=false;r=role;}
            save(snap,r);
            ui.post(()->adapter.updateLast(bubble(snap,r)));
        }

        private void save(String finished,int r){
            if(persist&&finished!=null&&!finished.trim().isEmpty())historyStore.append(r,finished);
        }

        private String snapshot(){return text.length()>MAX_CHARS?"…"+text.substring(text.length()-MAX_CHARS):text.toString();}
        private ChatMessage bubble(String s,int r){return new ChatMessage(-1,r,s,System.currentTimeMillis());}
    }

    private String cleanAssistantText(String answer){
        if(answer==null)return "";
        String s=answer.trim();String[] markers={"\nUser:","\nAssistant:","\n### Instruction:","\n### Response:"};int cut=s.length();
        for(String marker:markers){int i=s.indexOf(marker);if(i>0&&i<cut)cut=i;}
        if(cut<s.length())s=s.substring(0,cut).trim();
        if(s.startsWith("Assistant:"))s=s.substring("Assistant:".length()).trim();
        return s;
    }

    private String formatMetrics(GenerationResult r){
        if(r.generatedTokens<=0)return "جاهز";
        String first=r.firstTokenMs>=0?formatMs(r.firstTokenMs):"—";String total=r.totalMs>0?formatMs(r.totalMs):"—";
        return String.format(java.util.Locale.US,"جاهز • %d tok • %s أول token • %s إجمالي • %.1f tok/s",r.generatedTokens,first,total,r.tokensPerSecond);
    }

    private String formatMs(long ms){if(ms<1000)return ms+"ms";return String.format(java.util.Locale.US,"%.2fs",ms/1000.0);}
    private void scrollToEnd(){int count=adapter.getCount();if(count>0)chatList.scrollToPosition(count-1);}

    private void clearChat(){
        AgentLoop loop=agentLoop;if(loop!=null)loop.cancel();
        if(generating)for(LlamaEngine e:pool.engines())e.cancel();
        setGenerating(true);
        executor.execute(()->{
            historyStore.clear();for(LlamaEngine e:pool.engines())e.resetContext();
            // In agent mode a cleared chat also starts a fresh project; old projects stay on disk.
            if(agentMode){try{workspace=ProjectWorkspace.create(new File(getFilesDir(),"projects"));}catch(Exception ignored){}}
            runOnUiThread(()->{adapter.setAll(java.util.Collections.emptyList());setGenerating(false);refreshConversations();setWorking(false,agentMode?"مشروع جديد — المحادثات والذاكرة مُسحت":"تم حذف كل المحادثات");});
        });
    }

    private void setGenerating(boolean value){
        generating=value;
        sendButton.setText(value?"■":"↑");
        // Keep the button enabled while generating so it remains a Stop button.
        sendButton.setEnabled(engine!=null||assistantMode);
        assistantButton.setEnabled(!value);
        clearButton.setEnabled(!value);
        agentButton.setEnabled(!value);
        loadModelButton.setEnabled(!value&&!isWorkingStatus());
    }

    private boolean isWorkingStatus(){String s=status.getText()==null?"":status.getText().toString();return s.startsWith("جاري")||s.startsWith("تم تجهيز النموذج");}

    private void setWorking(boolean working,String message){
        status.setText(message);
        if(!generating)sendButton.setEnabled(!working&&(engine!=null||assistantMode));
    }

    private static String safeMessage(Exception ex){String m=ex.getMessage();return TextUtils.isEmpty(m)?ex.getClass().getSimpleName():m;}

    @Override protected void onResume(){
        super.onResume();resumed=true;
        if(assistantMode)buildAssistantChips();
        // Coming back from a settings page during "grant everything": go on with the next one.
        if(grantFlow)ui.postDelayed(this::nextGrant,500);
    }

    // ------------------------------------------------------------------ full permissions

    private boolean grantFlow;
    private final java.util.Set<String> grantTried=new java.util.HashSet<>();

    private String[] runtimePermissions(){
        List<String> p=new java.util.ArrayList<>(java.util.Arrays.asList(android.Manifest.permission.READ_CONTACTS,
                android.Manifest.permission.CALL_PHONE,android.Manifest.permission.SEND_SMS,android.Manifest.permission.RECORD_AUDIO));
        if(Build.VERSION.SDK_INT>=33)p.add(android.Manifest.permission.POST_NOTIFICATIONS);
        if(Build.VERSION.SDK_INT<30)p.add(android.Manifest.permission.WRITE_EXTERNAL_STORAGE);
        if(TermuxLauncher.isInstalled(this))p.add(TermuxLauncher.PERMISSION);
        return p.toArray(new String[0]);
    }

    private List<String> missingRuntime(){
        List<String> out=new java.util.ArrayList<>();
        for(String p:runtimePermissions())if(checkSelfPermission(p)!=android.content.pm.PackageManager.PERMISSION_GRANTED)out.add(p);
        return out;
    }

    private boolean allFilesGranted(){return Build.VERSION.SDK_INT<30||android.os.Environment.isExternalStorageManager();}
    private boolean batteryUnrestricted(){
        android.os.PowerManager pm=(android.os.PowerManager)getSystemService(POWER_SERVICE);
        return pm!=null&&pm.isIgnoringBatteryOptimizations(getPackageName());
    }
    private boolean isAssistantApp(){
        if(Build.VERSION.SDK_INT<29)return false;
        android.app.role.RoleManager rm=getSystemService(android.app.role.RoleManager.class);
        return rm!=null&&rm.isRoleAvailable(android.app.role.RoleManager.ROLE_ASSISTANT)&&rm.isRoleHeld(android.app.role.RoleManager.ROLE_ASSISTANT);
    }

    private void openAllFilesAccess(){
        if(Build.VERSION.SDK_INT<30)return;
        try{startActivity(new Intent(android.provider.Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,Uri.parse("package:"+getPackageName())));}
        catch(Exception e){startActivity(new Intent(android.provider.Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));}
    }

    /**
     * Android 13+ blocks Accessibility for apps installed from an APK file ("إعداد مقيّد" /
     * "تم منع التطبيق من الوصول"). The user unlocks it once from App info → ⋮, then enables it.
     */
    private void guideScreenControl(){
        new AlertDialog.Builder(this).setTitle("🖐 تفعيل التحكم بالشاشة")
                .setMessage("أندرويد يمنع التطبيقات المثبتة من ملف من هذا الإذن حتى تسمح أنت مرة واحدة:\n\n"
                        +"1️⃣ اضغط «معلومات التطبيق» ← ثم ⋮ (النقاط الثلاث أعلى الشاشة) ← «السماح بالإعدادات المقيدة» وأكّد ببصمتك/رمزك.\n"
                        +"   (إذا لم تظهر ⋮: حاول تفعيله مرة من الخطوة 2 أولاً ثم ارجع هنا.)\n\n"
                        +"2️⃣ اضغط «إمكانية الوصول» ← التطبيقات المثبتة ← NewAl screen control ← تشغيل.\n\n"
                        +"في سامسونج: إذا بقي ممنوعاً، أوقف «أداة الحظر التلقائي» مؤقتاً من الإعدادات ← الأمان والخصوصية.")
                .setPositiveButton("2️⃣ إمكانية الوصول",(d,w)->openAccessibility())
                .setNeutralButton("1️⃣ معلومات التطبيق",(d,w)->startActivity(new Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,Uri.parse("package:"+getPackageName()))))
                .setNegativeButton("لاحقاً",null).show();
    }

    private void openAccessibility(){
        Intent i=new Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS);
        // Highlights this app's entry on phones that support it.
        String component=new android.content.ComponentName(this,ScreenControlService.class).flattenToString();
        i.putExtra(":settings:fragment_args_key",component);
        Bundle b=new Bundle();b.putString(":settings:fragment_args_key",component);i.putExtra(":settings:show_fragment_args",b);
        startActivity(i);
    }

    @android.annotation.SuppressLint("BatteryLife")
    private void openBattery(){
        try{startActivity(new Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,Uri.parse("package:"+getPackageName())));}
        catch(Exception e){startActivity(new Intent(android.provider.Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));}
    }

    private void openAssistantSetting(){
        try{startActivity(new Intent(android.provider.Settings.ACTION_VOICE_INPUT_SETTINGS));}
        catch(Exception e){startActivity(new Intent(android.provider.Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS));}
    }

    /** What NewAl may do, with one button that walks through every permission once. */
    private void showPermissions(){
        boolean runtime=missingRuntime().isEmpty(),screen=ScreenControlService.instance!=null,files=allFilesGranted(),
                battery=batteryUnrestricted(),assist=isAssistantApp(),autonomous=!prefs.getBoolean(PREF_CONFIRM_SENDS,true);
        String[] items={
                (runtime?"✅":"⬜")+" جهات الاتصال، المكالمات، الرسائل، الميكروفون، الإشعارات",
                (screen?"✅":"⬜")+" التحكم بالشاشة (فتح التطبيقات والضغط والكتابة)",
                (files?"✅":"⬜")+" الوصول لكل الملفات (النماذج في التنزيلات)",
                (battery?"✅":"⬜")+" العمل بالخلفية بلا قيود البطارية",
                (assist?"✅":"⬜")+" مساعد الهاتف الافتراضي (ضغطة مطوّلة على الرئيسية)",
                (autonomous?"✅":"⬜")+" التنفيذ بدون سؤال (إرسال واتصال بلا تأكيد)"};
        new AlertDialog.Builder(this).setTitle("🔓 الصلاحيات الكاملة")
                .setItems(items,(d,w)->{
                    switch(w){
                        case 0:if(!runtime)requestPermissions(missingRuntime().toArray(new String[0]),REQ_ASSIST);break;
                        case 1:guideScreenControl();break;
                        case 2:openAllFilesAccess();break;
                        case 3:openBattery();break;
                        case 4:openAssistantSetting();break;
                        default:
                            prefs.edit().putBoolean(PREF_CONFIRM_SENDS,autonomous).apply();
                            assistant.confirmSends=autonomous;
                            setWorking(false,autonomous?"سيسأل قبل الإرسال والاتصال":"⚡ ينفّذ بدون سؤال");
                            showPermissions();
                    }
                })
                .setPositiveButton("🔓 امنح الكل",(d,w)->startGrantAll())
                .setNegativeButton("إغلاق",null).show();
    }

    /** Android shows each permission once; after that NewAl never asks again. */
    private void startGrantAll(){
        grantFlow=true;
        grantTried.clear();
        prefs.edit().putBoolean(PREF_CONFIRM_SENDS,false).apply();
        assistant.confirmSends=false;
        List<String> missing=missingRuntime();
        if(!missing.isEmpty())requestPermissions(missing.toArray(new String[0]),REQ_ALL);
        else nextGrant();
    }

    private void nextGrant(){
        if(!grantFlow||!resumed)return;
        if(ScreenControlService.instance==null&&grantTried.add("screen")){
            setWorking(false,"فعّل «NewAl screen control» ثم ارجع");
            guideScreenControl();return;
        }
        if(!allFilesGranted()&&grantTried.add("files")){setWorking(false,"فعّل «السماح بإدارة كل الملفات» ثم ارجع");openAllFilesAccess();return;}
        if(!batteryUnrestricted()&&grantTried.add("battery")){openBattery();return;}
        if(!isAssistantApp()&&grantTried.add("assist")){setWorking(false,"اختر NewAl كتطبيق المساعد ثم ارجع");openAssistantSetting();return;}
        grantFlow=false;
        setWorking(false,"🔓 تم — NewAl لن يطلب منك أذونات بعد الآن");
        showPermissions();
    }
    @Override protected void onPause(){resumed=false;super.onPause();}

    @Override protected void onNewIntent(Intent intent){
        super.onNewIntent(intent);
        handleAssistIntent(intent);
    }

    @Override protected void onDestroy(){
        if(tts!=null)tts.shutdown();
        if(recognizer!=null)recognizer.destroy();
        AgentLoop loop=agentLoop;if(loop!=null)loop.cancel();
        TermuxBridge b=bridge;if(b!=null)b.close();
        engine=null;
        for(LlamaEngine e:pool.engines())e.cancel();
        executor.execute(pool::closeAll);
        executor.shutdown();super.onDestroy();
    }
}
