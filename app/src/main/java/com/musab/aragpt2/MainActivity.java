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
    private static final String PREF_AGENT_MODE="agent_mode", PREF_AGENT_TOKEN="agent_token";
    private static final int CONTEXT_TOKENS=4096, MAX_NEW_TOKENS=256, TOP_K=40, AGENT_PORT=47811, REQ_TERMUX=41, REQ_NOTIFY=42;
    private static final float TEMPERATURE=0.70f;

    private final ExecutorService executor=Executors.newSingleThreadExecutor();
    private final Handler ui=new Handler(Looper.getMainLooper());
    private final LiveBubble live=new LiveBubble();
    private volatile TermuxBridge bridge;
    private volatile AgentLoop agentLoop;
    private ProjectWorkspace workspace;
    private boolean agentMode;
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

        adapter=new ChatAdapter();
        chatList.setLayoutManager(new LinearLayoutManager(this));
        chatList.setAdapter(adapter);
        adapter.setAll(historyStore.loadAll());
        scrollToEnd();
        installKeyboardInsetsFix();

        pickModelLauncher=registerForActivityResult(new ActivityResultContracts.OpenDocument(), this::onModelPicked);
        loadModelButton.setOnClickListener(v->pickModelLauncher.launch(new String[]{"*/*"}));
        sendButton.setOnClickListener(v->onSendOrStopClicked());
        clearButton.setOnClickListener(v->clearChat());
        agentButton.setOnClickListener(v->setAgentMode(!agentMode));
        agentMode=prefs.getBoolean(PREF_AGENT_MODE,false);
        renderAgentButton();
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
            if(engine!=null){try{engine.close();}catch(Exception ignored){}engine=null;}
            int threads=Math.max(2,Math.min(6,Runtime.getRuntime().availableProcessors()));
            LlamaEngine loaded=new LlamaEngine(getApplicationContext(),file.getAbsolutePath(),CONTEXT_TOKENS,threads);
            engine=loaded;
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
        if(generating){AgentLoop loop=agentLoop;if(loop!=null)loop.cancel();else if(engine!=null)engine.cancel();return;}
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
                GenerationResult result=engine.generate(turns,MAX_NEW_TOKENS,TEMPERATURE,TOP_K,GenerationMode.DEFAULT_CODE_MODE?LlamaEngine.FLAG_CODE_MODE:0,live::append);
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

    private void renderAgentButton(){agentButton.setText(agentMode?"🛠 Termux ✓":"🛠 Termux");inputBox.setHint(agentMode?"اطلب برنامجاً ليُكتب ويُشغَّل في Termux…":"اكتب رسالتك…");}

    /** One-time setup: Termux installed, permission granted, agent reachable. Afterwards it is automatic. */
    private void ensureTermuxReady(){
        if(!TermuxLauncher.isInstalled(this)){
            new AlertDialog.Builder(this).setTitle("Termux غير مثبت").setMessage("ثبّت Termux من F-Droid أو GitHub (وليس Google Play) ثم فعّل الوضع مرة أخرى.").setPositiveButton("حسناً",null).show();
            setAgentMode(false);return;
        }
        if(!TermuxLauncher.hasPermission(this)){requestPermissions(new String[]{TermuxLauncher.PERMISSION},REQ_TERMUX);return;}
        setWorking(true,"جاري الاتصال بـTermux…");
        new Thread(()->{
            try{
                TermuxBridge b=termuxBridge();
                b.ensureConnected(12000);
                long rtt=b.ping(3000);
                String py=b.agentInfo()==null?"":b.agentInfo().optString("python");
                runOnUiThread(()->setWorking(false,"Termux جاهز • Python "+py+" • "+rtt+"ms"));
            }catch(Exception e){runOnUiThread(this::showTermuxSetup);}
        },"termux-connect").start();
    }

    @Override public void onRequestPermissionsResult(int requestCode,String[] permissions,int[] grantResults){
        super.onRequestPermissionsResult(requestCode,permissions,grantResults);
        if(requestCode!=REQ_TERMUX)return;
        if(grantResults.length>0&&grantResults[0]==android.content.pm.PackageManager.PERMISSION_GRANTED)ensureTermuxReady();
        else{setAgentMode(false);setWorking(false,"لم يُمنح إذن تشغيل الأوامر في Termux");}
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
                .setNegativeButton("إلغاء",(d,w)->setAgentMode(false))
                .show();
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
        setGenerating(true);
        if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)!=android.content.pm.PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{android.Manifest.permission.POST_NOTIFICATIONS},REQ_NOTIFY);
        AgentService.start(this,AgentLoop.tail(request,200));
        executor.execute(()->{
            if(!resume){
                long userId=historyStore.append(ChatMessage.ROLE_USER,request);long t=System.currentTimeMillis();
                runOnUiThread(()->{adapter.add(new ChatMessage(userId,ChatMessage.ROLE_USER,request,t));scrollToEnd();});
            }
            String text;
            try{
                ProjectWorkspace ws=currentWorkspace();
                AgentLoop loop=new AgentLoop(agentModel(),termuxBridge(),ws,new UiAgentListener());
                agentLoop=loop;
                live.persist=true;
                if(resume)runOnUiThread(()->setWorking(true,"استئناف المهمة السابقة من آخر حالة آمنة…"));
                AgentLoop.Outcome o=resume?loop.resume():loop.run(request);
                live.end();
                String icon=o.state==AgentLoop.State.SUCCESS?"✅ ":o.state==AgentLoop.State.WAITING_FOR_USER?"⚠️ ":"❌ ";
                text=icon+o.message+"\n📁 ~/newal/projects/"+ws.id;
                if(o.interactiveCommand!=null){
                    String projectId=ws.id,command=o.interactiveCommand;
                    runOnUiThread(()->new AlertDialog.Builder(this).setTitle("برنامج تفاعلي")
                            .setMessage("البرنامج يعمل وينتظر إدخالك. تشغيله الآن في Termux لتكتب له؟\n\n"+command)
                            .setPositiveButton("شغّل في Termux",(d,w)->{
                                try{new TermuxLauncher(this).openInteractive(projectId,command);}
                                catch(Exception e){setWorking(false,"تعذر فتح Termux: "+safeMessage(e));}
                            })
                            .setNegativeButton("لاحقاً",null).show());
                }
            }catch(Exception e){
                live.end();
                text="❌ "+safeMessage(e);
            }finally{agentLoop=null;live.persist=false;}
            final String summary=text;
            AgentService.finish(getApplicationContext(),summary.startsWith("✅")?"✅ اكتملت المهمة":summary.startsWith("⚠️")?"⚠️ المهمة تحتاج تدخلك":"❌ فشلت المهمة",summary);
            long id=historyStore.append(ChatMessage.ROLE_ASSISTANT,summary);long t=System.currentTimeMillis();
            runOnUiThread(()->{adapter.add(new ChatMessage(id,ChatMessage.ROLE_ASSISTANT,summary,t));scrollToEnd();setGenerating(false);});
        });
    }

    private AgentLoop.Model agentModel(){
        final LlamaEngine e=engine;
        return new AgentLoop.Model(){
            // Greedy decoding for deterministic code; generate until the answer ends or the context is full.
            // The grammar only allows FILE/EDIT/STDIN/RUN blocks, so the output always parses.
            @Override public GenerationResult generate(List<ChatMessage> turns,TextListener l){return e.generate(turns,0,0f,1,LlamaEngine.FLAG_RAW,AgentLoop.OUTPUT_GRAMMAR,l);}
            @Override public void cancel(){e.cancel();}
            @Override public int contextTokens(){return e.contextTokens();}
        };
    }

    private final class UiAgentListener implements AgentLoop.Listener{
        @Override public void onState(AgentLoop.State state,String detail){
            if((state==AgentLoop.State.GENERATING||state==AgentLoop.State.PATCHING)&&detail!=null)live.start("");
            else if(state==AgentLoop.State.RUNNING)live.start("▶ "+detail+"\n");
            String label=state.name()+(detail==null||detail.isEmpty()?"":" • "+AgentLoop.tail(detail,120));
            runOnUiThread(()->status.setText(label));
            AgentService.update(getApplicationContext(),label);
        }
        @Override public void onModelText(String delta){live.append(delta);}
        @Override public void onOutput(boolean stderr,String text){live.append(text);}
        @Override public void onMetrics(String line){
            try{
                org.json.JSONObject m=new org.json.JSONObject(line);
                String s=String.format(java.util.Locale.US,"#%d • أول token %sms • أول كتابة %sms • تشغيل %sms • محاولة كاملة %sms",
                        m.optInt("attempt"),m.opt("time_to_first_token_ms"),m.opt("time_to_first_file_write_ms"),m.opt("exec_execution_ms"),m.opt("iteration_ms"));
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
        /** Agent mode: finished bubbles (generated code, program output) are saved to chat history. */
        volatile boolean persist;

        void start(String header){
            final String previous;
            synchronized(this){previous=active?snapshot():null;text.setLength(0);text.append(header);active=true;}
            save(previous);
            ui.post(()->{
                if(previous!=null)adapter.updateLast(bubble(previous));
                adapter.add(bubble(header));scrollToEnd();
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
            synchronized(this){scheduled=false;if(!active)return;snap=snapshot();}
            adapter.updateLast(bubble(snap));scrollToEnd();
        }

        void end(){
            String snap;
            synchronized(this){if(!active)return;snap=snapshot();active=false;}
            save(snap);
            ui.post(()->adapter.updateLast(bubble(snap)));
        }

        private void save(String finished){
            if(persist&&finished!=null&&!finished.trim().isEmpty())historyStore.append(ChatMessage.ROLE_ASSISTANT,finished);
        }

        private String snapshot(){return text.length()>MAX_CHARS?"…"+text.substring(text.length()-MAX_CHARS):text.toString();}
        private ChatMessage bubble(String s){return new ChatMessage(-1,ChatMessage.ROLE_ASSISTANT,s,System.currentTimeMillis());}
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
        if(generating&&engine!=null)engine.cancel();
        setGenerating(true);
        executor.execute(()->{
            historyStore.clear();if(engine!=null)engine.resetContext();
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
        LlamaEngine toClose=engine;engine=null;if(toClose!=null)toClose.cancel();
        if(toClose!=null)executor.execute(()->{try{toClose.close();}catch(Exception ignored){}});
        executor.shutdown();super.onDestroy();
    }
}
