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
            PREF_SPECULATIVE="speculative", PREF_DRAFT="draft_path";
    /** Draft tokens per step; measured best on CPU for a 3B model with a 0.5B draft. */
    private static final int DRAFT_TOKENS=3;
    /** Model roles: each may use its own GGUF; models are loaded one at a time (sequentially). */
    private static final String ROLE_MANAGER="manager", ROLE_CODER="coder", ROLE_LANGUAGE="language";
    private static final int CONTEXT_TOKENS=4096, MAX_NEW_TOKENS=256, TOP_K=40, AGENT_PORT=47811, REQ_TERMUX=41, REQ_NOTIFY=42;
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
    private Button loadModelButton,sendButton,clearButton,agentButton;
    private int headerBasePaddingTop;
    private ActivityResultLauncher<String[]> pickModelLauncher;

    @Override protected void onCreate(Bundle savedInstanceState){
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        prefs=getSharedPreferences(PREFS,MODE_PRIVATE);
        historyStore=new ChatHistoryStore(this);
        device=new DeviceController(this);
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
        clearButton.setOnClickListener(v->clearChat());
        agentButton.setOnClickListener(v->setAgentMode(!agentMode));
        agentButton.setOnLongClickListener(v->{showTools();return true;});
        agentMode=prefs.getBoolean(PREF_AGENT_MODE,false);
        AgentProfile saved=AgentProfile.byId(prefs.getString(PREF_AGENT_PROFILE,"auto"));
        if(saved!=null)selectedAgent=saved;
        renderAgentButton();
        updateEmptyState();
        restoreSavedModel();
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
    private static final Object[][] MODEL_CATALOG={
            {"Qwen2.5-Coder 0.5B (Q8) — الأسرع",0.68,3,"https://huggingface.co/Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-0.5b-instruct-q8_0.gguf"},
            {"Qwen2.5-Coder 1.5B (Q4_K_M) — متوازن",1.12,4,"https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"},
            {"Qwen2.5-Coder 3B (Q4_K_M) — أدق بكثير",2.1,6,"https://huggingface.co/Qwen/Qwen2.5-Coder-3B-Instruct-GGUF/resolve/main/qwen2.5-coder-3b-instruct-q4_k_m.gguf"},
            {"Qwen2.5-Coder 7B (Q4_K_M) — الأقوى وبطيء",4.7,10,"https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf"},
            {"🗣 Qwen2.5 0.5B عام (مدير سريع)",0.68,3,"https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q8_0.gguf"},
            {"🗣 Qwen2.5 1.5B عام (لغة ومحادثة)",1.12,4,"https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"},
            {"🗣 Qwen2.5 3B عام (لغة أقوى)",2.1,6,"https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"},
    };

    private void chooseModelSource(){
        android.app.ActivityManager am=(android.app.ActivityManager)getSystemService(ACTIVITY_SERVICE);
        android.app.ActivityManager.MemoryInfo mi=new android.app.ActivityManager.MemoryInfo();
        if(am!=null)am.getMemoryInfo(mi);
        double ramGb=mi.totalMem/1e9;
        String[] items=new String[MODEL_CATALOG.length+1];
        items[0]="📂 اختيار ملف GGUF من الهاتف";
        for(int i=0;i<MODEL_CATALOG.length;i++){
            Object[] m=MODEL_CATALOG[i];
            boolean fits=ramGb<=0||ramGb>=((Number)m[2]).doubleValue();
            items[i+1]=(fits?"📥 ":"⚠️ ")+m[0]+String.format(java.util.Locale.US," • %.1f GB",((Number)m[1]).doubleValue())+(fits?"":" (يحتاج RAM أكبر)");
        }
        new AlertDialog.Builder(this).setTitle(String.format(java.util.Locale.US,"النموذج (ذاكرة الهاتف %.1f GB)",ramGb)).setItems(items,(d,which)->{
            if(which==0)pickModelLauncher.launch(new String[]{"*/*"});
            else downloadModel((String)MODEL_CATALOG[which-1][0],(String)MODEL_CATALOG[which-1][3]);
        }).show();
    }

    /** Downloads with Android's DownloadManager (resumes, survives app restarts), then loads it. */
    private void downloadModel(String title,String url){
        File dir=getExternalFilesDir("models");
        if(dir==null){setWorking(false,"تخزين التطبيق غير متاح");return;}
        String fileName=url.substring(url.lastIndexOf('/')+1);
        File target=new File(dir,fileName);
        if(isValidGgufFile(target)){loadModelFromLocalFile(target,fileName);return;}
        android.app.DownloadManager dm=(android.app.DownloadManager)getSystemService(DOWNLOAD_SERVICE);
        if(dm==null)return;
        target.delete();
        android.app.DownloadManager.Request r=new android.app.DownloadManager.Request(Uri.parse(url))
                .setTitle(title).setDescription("NewAl model")
                .setNotificationVisibility(android.app.DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setAllowedOverMetered(true).setAllowedOverRoaming(false)
                .setDestinationInExternalFilesDir(this,"models",fileName);
        long id=dm.enqueue(r);
        loadModelButton.setEnabled(false);
        pollDownload(dm,id,target,fileName);
    }

    private void pollDownload(android.app.DownloadManager dm,long id,File target,String name){
        try(android.database.Cursor c=dm.query(new android.app.DownloadManager.Query().setFilterById(id))){
            if(c==null||!c.moveToFirst()){loadModelButton.setEnabled(true);setWorking(false,"أُلغي التنزيل");return;}
            int status=c.getInt(c.getColumnIndexOrThrow(android.app.DownloadManager.COLUMN_STATUS));
            long done=c.getLong(c.getColumnIndexOrThrow(android.app.DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR));
            long total=c.getLong(c.getColumnIndexOrThrow(android.app.DownloadManager.COLUMN_TOTAL_SIZE_BYTES));
            if(status==android.app.DownloadManager.STATUS_SUCCESSFUL){
                if(isValidGgufFile(target))loadModelFromLocalFile(target,name);
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
        ui.postDelayed(()->pollDownload(dm,id,target,name),1000);
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
                File staged=stageModelToAppStorage(uri,expectedSize);
                runOnUiThread(()->setWorking(true,"تم تجهيز النموذج — جاري فتحه: "+displayName));
                loadModelFromLocalFileInternal(staged,displayName,uri.toString(),expectedSize);
            }catch(Exception ex){
                runOnUiThread(()->{loadModelButton.setEnabled(true);setWorking(false,"تعذر تجهيز GGUF: "+safeMessage(ex));});
            }
        });
    }

    private File stageModelToAppStorage(Uri uri,long expectedSize)throws Exception{
        File dir=getExternalFilesDir("models");
        if(dir==null)throw new IllegalStateException("تخزين التطبيق غير متاح");
        if(!dir.exists()&&!dir.mkdirs())throw new IllegalStateException("تعذر إنشاء مجلد النماذج");
        File tmp=new File(dir,"model.gguf.part"),target=new File(dir,"model.gguf");
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
            runOnUiThread(()->{loadModelButton.setEnabled(true);setWorking(false,"جاهز — "+displayName);resumeInterruptedAgentTask();});
        }catch(Exception ex){
            runOnUiThread(()->{loadModelButton.setEnabled(true);setWorking(false,"تعذر تحميل النموذج: "+safeMessage(ex));});
        }
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
        if(generating){AgentLoop loop=agentLoop;if(loop!=null)loop.cancel();for(LlamaEngine e:pool.engines())e.cancel();return;}
        String question=inputBox.getText().toString().trim();
        if(question.isEmpty()||engine==null)return;
        inputBox.setText("");
        if(agentMode){startAgent(question,false);return;}
        setGenerating(true);
        executor.execute(()->{
            long userId=historyStore.append(ChatMessage.ROLE_USER,question);long userTime=System.currentTimeMillis();
            memoryManager.rememberExplicit(question);
            List<ChatMessage> turns=memoryManager.buildTurns(historyStore.loadAll());
            runOnUiThread(()->{adapter.add(new ChatMessage(userId,ChatMessage.ROLE_USER,question,userTime));scrollToEnd();setWorking(true,"يفكر…");});
            live.start("");
            try{
                // The reply streams into the bubble token by token instead of appearing at the end.
                GenerationResult result=engineForRole(ROLE_LANGUAGE).generate(turns,MAX_NEW_TOKENS,TEMPERATURE,TOP_K,GenerationMode.DEFAULT_CODE_MODE?LlamaEngine.FLAG_CODE_MODE:0,live::append);
                live.end();
                String answerText=cleanAssistantText(result.text);if(answerText.isEmpty())answerText="…";final String displayAnswer=answerText;
                long assistantId=historyStore.append(ChatMessage.ROLE_ASSISTANT,displayAnswer);long assistantTime=System.currentTimeMillis();
                memoryManager.refreshExtractiveSummary(historyStore.loadAll());String metric=formatMetrics(result);
                runOnUiThread(()->{adapter.updateLast(new ChatMessage(assistantId,ChatMessage.ROLE_ASSISTANT,displayAnswer,assistantTime));scrollToEnd();setGenerating(false);setWorking(false,metric);});
            }catch(Exception ex){live.end();runOnUiThread(()->{setGenerating(false);setWorking(false,"خطأ أثناء التوليد: "+safeMessage(ex));});}
        });
    }

    // ------------------------------------------------------------------ Termux agent

    private void setAgentMode(boolean on){
        agentMode=on;
        prefs.edit().putBoolean(PREF_AGENT_MODE,on).apply();
        renderAgentButton();
        if(on)ensureTermuxReady();
        else setWorking(false,"وضع المحادثة العادية");
    }

    private void renderAgentButton(){
        agentButton.setText(agentMode?"🛠 Termux ✓":"🛠 Termux");
        inputBox.setHint(agentMode?"اطلب برنامجاً… أو /test /fix /explain":"اكتب رسالتك…");
        agentBar.setVisibility(agentMode?View.VISIBLE:View.GONE);
        if(agentMode)buildAgentChips();
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
            new AlertDialog.Builder(this).setTitle("🖐 التحكم بالشاشة")
                    .setMessage("ليستطيع المساعد قراءة الشاشة والضغط والكتابة في التطبيقات الأخرى، فعّل «NewAl screen control» في إعدادات إمكانية الوصول.\n\nفي Android 13+ إن ظهر «إعداد مقيّد»: معلومات التطبيق ← ⋮ ← السماح بالإعدادات المقيّدة، ثم فعّله.")
                    .setPositiveButton("فتح الإعدادات",(d,w)->startActivity(new Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS)))
                    .setNegativeButton("لاحقاً",null).show();
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
        title.setText(agentMode?"🛠 وضع البرمجة مع Termux\nاكتب طلبك وسيُكتب الكود ويُشغَّل ويُصلَح تلقائياً.":"💬 محادثة محلية بالكامل على هاتفك");
        title.setTextColor(getResources().getColor(R.color.text_primary,getTheme()));
        title.setTextSize(17);
        title.setPadding(0,0,0,24);
        examples.addView(title);
        String[] prompts=agentMode?new String[]{
                "اكتب آلة حاسبة بسيطة بلغة Python",
                "اكتب برنامجاً يطبع أول 20 عدداً أولياً مع اختبارات",
                "/data اكتب برنامجاً يحسب متوسط الدرجات من ملف CSV",
                "/sqlite اصنع دفتر ملاحظات بقاعدة بيانات",
                "كم نسبة البطارية؟",
                "/explain كيف يعمل main.py؟"}
                :new String[]{"مرحباً! عرّفني بنفسك","اشرح لي الفرق بين list و tuple في Python","اكتب قصيدة قصيرة عن البحر"};
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
        File dir=getExternalFilesDir("models");
        File[] ggufs=dir==null?new File[0]:dir.listFiles((d,n)->n.endsWith(".gguf"));
        if(ggufs==null)ggufs=new File[0];
        final File[] files=ggufs;
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
        android.widget.CheckBox autoTest=new android.widget.CheckBox(this);
        autoTest.setText("اختبارات تلقائية بعد نجاح البرنامج");autoTest.setChecked(prefs.getBoolean(PREF_AUTO_TEST,false));box.addView(autoTest);
        new AlertDialog.Builder(this).setTitle("⚙ الإعدادات").setView(box).setPositiveButton("حفظ",(d,w)->{
            prefs.edit().putInt(PREF_MAX_ATTEMPTS,clamp(attempts,1,20,6)).putInt(PREF_TIMEOUT_S,clamp(timeout,5,1800,60))
                    .putBoolean(PREF_GRAMMAR,grammar.isChecked()).putBoolean(PREF_AUTO_TEST,autoTest.isChecked()).apply();
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
     * The model of a role, loaded on demand. Only one model is in memory: switching frees the
     * current one first, so the team runs one after another (sequentially), never in parallel.
     * Called on the worker thread.
     */
    private LlamaEngine engineForRole(String role){
        String path=prefs.getString(PREF_ROLE+role,"");
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
        int threads=Math.max(2,Math.min(6,Runtime.getRuntime().availableProcessors()));
        return new LlamaEngine(getApplicationContext(),path,CONTEXT_TOKENS,threads);
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
        if(d.isEmpty()){
            File dir=getExternalFilesDir("models");
            File[] small=dir==null?null:dir.listFiles((x,n)->n.toLowerCase(java.util.Locale.ROOT).contains("0.5b")&&n.endsWith(".gguf"));
            if(small!=null)for(File f:small)if(d.isEmpty()||f.length()<new File(d).length())d=f.getAbsolutePath();
        }
        if(d.isEmpty()||d.equals(targetPath)||!new File(d).isFile())return null;
        return new File(targetPath).length()>=3*new File(d).length()?d:null;
    }

    private static String roleTitle(String role){
        return ROLE_MANAGER.equals(role)?"المدير":ROLE_CODER.equals(role)?"المبرمج":"اللغة";
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
            runOnUiThread(()->{adapter.setAll(java.util.Collections.emptyList());setGenerating(false);setWorking(false,agentMode?"مشروع جديد — المحادثة والذاكرة مُسحت":"تم مسح المحادثة والذاكرة");});
        });
    }

    private void setGenerating(boolean value){
        generating=value;
        sendButton.setText(value?"⏹":"➤");
        // Keep the button enabled while generating so it remains a Stop button.
        sendButton.setEnabled(engine!=null);
        clearButton.setEnabled(!value);
        agentButton.setEnabled(!value);
        loadModelButton.setEnabled(!value&&!isWorkingStatus());
    }

    private boolean isWorkingStatus(){String s=status.getText()==null?"":status.getText().toString();return s.startsWith("جاري")||s.startsWith("تم تجهيز النموذج");}

    private void setWorking(boolean working,String message){
        status.setText(message);
        if(!generating)sendButton.setEnabled(!working&&engine!=null);
    }

    private static String safeMessage(Exception ex){String m=ex.getMessage();return TextUtils.isEmpty(m)?ex.getClass().getSimpleName():m;}

    @Override protected void onDestroy(){
        AgentLoop loop=agentLoop;if(loop!=null)loop.cancel();
        TermuxBridge b=bridge;if(b!=null)b.close();
        engine=null;
        for(LlamaEngine e:pool.engines())e.cancel();
        executor.execute(pool::closeAll);
        executor.shutdown();super.onDestroy();
    }
}
