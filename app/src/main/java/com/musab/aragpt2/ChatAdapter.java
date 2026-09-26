package com.musab.aragpt2;

import android.graphics.Color;
import android.text.SpannableString;
import android.text.Spanned;
import android.text.style.ForegroundColorSpan;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;
import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

public final class ChatAdapter extends RecyclerView.Adapter<ChatAdapter.VH> {
    /** What the message views can ask the screen to do. */
    public interface Actions {
        void onRerun();
        void onFiles();
        void onOpenTermux();
        void onUndo();
        void onCopy(String text);
        default void onRegenerate(ChatMessage answer) {}
        default void onSpeak(String text) {}
        default void onShare(String text) {}
        default void onEdit(ChatMessage question) {}
        default void onOpenUrl(String url) {}
    }

    private static final int[] COLORS = {
            Color.parseColor("#8B949E"), // comment
            Color.parseColor("#A5D6FF"), // string
            Color.parseColor("#FF7B72"), // keyword
            Color.parseColor("#79C0FF"), // number
            Color.parseColor("#D2A8FF"), // FILE:/EDIT: headers and fences
            Color.parseColor("#FFA657"), // function name
    };

    private final List<ChatMessage> items = new ArrayList<>();
    /** Answers whose thinking is unfolded. */
    private final Set<Long> openThinking = new HashSet<>();
    private Actions actions;

    public void setActions(Actions a){actions=a;}
    public void setAll(List<ChatMessage> messages){items.clear();items.addAll(messages);notifyDataSetChanged();}
    public void add(ChatMessage message){items.add(message);notifyItemInserted(items.size()-1);}
    public int getCount(){return items.size();}
    public ChatMessage get(int i){return items.get(i);}
    /** Replaces the last message in place (used while a reply is streaming). */
    public void updateLast(ChatMessage message){if(items.isEmpty()){add(message);return;}items.set(items.size()-1,message);notifyItemChanged(items.size()-1);}
    /** Drops the messages from position {@code from} on (edit / regenerate). */
    public void truncate(int from){if(from<0||from>=items.size())return;int n=items.size()-from;items.subList(from,items.size()).clear();notifyItemRangeRemoved(from,n);}
    public int indexOf(long id){for(int i=0;i<items.size();i++)if(items.get(i).id==id)return i;return -1;}
    @Override public int getItemViewType(int position){return items.get(position).role;}
    @Override public int getItemCount(){return items.size();}

    @NonNull @Override public VH onCreateViewHolder(@NonNull ViewGroup parent,int viewType){
        int layout;
        switch(viewType){
            case ChatMessage.ROLE_USER: layout=R.layout.item_message_user; break;
            case ChatMessage.ROLE_CODE: layout=R.layout.item_message_code; break;
            case ChatMessage.ROLE_TERMINAL: layout=R.layout.item_message_terminal; break;
            case ChatMessage.ROLE_RESULT: layout=R.layout.item_message_result; break;
            default: layout=R.layout.item_message_assistant;
        }
        VH vh=new VH(LayoutInflater.from(parent.getContext()).inflate(layout,parent,false));
        if(viewType==ChatMessage.ROLE_RESULT){
            bind(vh.itemView,R.id.actionRun,()->{if(actions!=null)actions.onRerun();});
            bind(vh.itemView,R.id.actionFiles,()->{if(actions!=null)actions.onFiles();});
            bind(vh.itemView,R.id.actionTermux,()->{if(actions!=null)actions.onOpenTermux();});
            bind(vh.itemView,R.id.actionUndo,()->{if(actions!=null)actions.onUndo();});
        }
        if(vh.text!=null)vh.text.setOnLongClickListener(v->{
            if(actions==null)return true;
            int pos=vh.getBindingAdapterPosition();
            if(pos<0)return true;
            ChatMessage m=items.get(pos);
            if(m.role==ChatMessage.ROLE_USER)actions.onEdit(m);
            else actions.onCopy(m.text);
            return true;
        });
        return vh;
    }

    private static void bind(View root,int id,Runnable r){View b=root.findViewById(id);if(b!=null)b.setOnClickListener(v->r.run());}

    @Override public void onBindViewHolder(@NonNull VH holder,int position){
        ChatMessage m=items.get(position);
        if(m.role==ChatMessage.ROLE_ASSISTANT||m.role==ChatMessage.ROLE_SYSTEM){bindAnswer(holder,m,position==items.size()-1);return;}
        if(m.role==ChatMessage.ROLE_CODE)holder.text.setText(highlight(m.text));
        else holder.text.setText(m.text);
    }

    private void bindAnswer(VH h,ChatMessage m,boolean last){
        View v=h.itemView;
        JSONObject meta;
        try{meta=m.meta.isEmpty()?new JSONObject():new JSONObject(m.meta);}catch(Exception e){meta=new JSONObject();}
        final JSONObject info=meta;

        TextView tools=v.findViewById(R.id.toolsLine);
        String toolText=info.optString("tools");
        tools.setVisibility(toolText.isEmpty()?View.GONE:View.VISIBLE);
        tools.setText(toolText);

        String thinking=info.optString("thinking");
        TextView toggle=v.findViewById(R.id.thinkingToggle),think=v.findViewById(R.id.thinkingText);
        boolean open=openThinking.contains(m.id)||(m.streaming&&m.text.isEmpty());
        toggle.setVisibility(thinking.isEmpty()?View.GONE:View.VISIBLE);
        toggle.setText(m.streaming&&m.text.isEmpty()?"💭 يفكّر…":(open?"💭 التفكير ▾":"💭 فكّر قبل الإجابة ▸"));
        toggle.setOnClickListener(x->{if(!openThinking.remove(m.id))openThinking.add(m.id);notifyItemChanged(h.getBindingAdapterPosition());});
        think.setVisibility(!thinking.isEmpty()&&open?View.VISIBLE:View.GONE);
        think.setText(thinking);

        LinearLayout content=v.findViewById(R.id.content);
        JSONArray sources=info.optJSONArray("sources");
        MarkdownView.render(content,m.text.isEmpty()&&m.streaming&&thinking.isEmpty()?"":m.text,m.streaming,new MarkdownView.Callbacks(){
            @Override public void copy(String text){if(actions!=null)actions.onCopy(text);}
            @Override public void openUrl(String url){if(actions!=null)actions.onOpenUrl(url);}
            @Override public void cite(int n){
                if(sources!=null&&n>=1&&n<=sources.length()&&actions!=null)actions.onOpenUrl(sources.optJSONObject(n-1).optString("u"));
            }
        });

        TextView src=v.findViewById(R.id.sourcesLine);
        if(sources!=null&&sources.length()>0&&!m.streaming){
            android.text.SpannableStringBuilder b=new android.text.SpannableStringBuilder("المصادر: ");
            for(int i=0;i<sources.length()&&i<8;i++){
                JSONObject s=sources.optJSONObject(i);
                String host=s.optString("u").replaceFirst("^https?://(www\\.)?","").replaceFirst("/.*$","");
                int a=b.length();
                b.append("[").append(String.valueOf(i+1)).append("] ").append(host).append("   ");
                String url=s.optString("u");
                b.setSpan(new android.text.style.ClickableSpan(){
                    @Override public void onClick(@NonNull View w){if(actions!=null)actions.onOpenUrl(url);}
                    @Override public void updateDrawState(@NonNull android.text.TextPaint ds){ds.setColor(MarkdownView.ACCENT);ds.setUnderlineText(false);}
                },a,b.length()-3,Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            }
            src.setText(b);
            src.setMovementMethod(android.text.method.LinkMovementMethod.getInstance());
            src.setVisibility(View.VISIBLE);
        }else src.setVisibility(View.GONE);

        View row=v.findViewById(R.id.actions);
        row.setVisibility(m.streaming||m.role==ChatMessage.ROLE_SYSTEM?View.GONE:View.VISIBLE);
        v.findViewById(R.id.actionCopy).setOnClickListener(x->{if(actions!=null)actions.onCopy(m.text);});
        View regen=v.findViewById(R.id.actionRegenerate);
        regen.setVisibility(last?View.VISIBLE:View.GONE);
        regen.setOnClickListener(x->{if(actions!=null)actions.onRegenerate(m);});
        v.findViewById(R.id.actionSpeak).setOnClickListener(x->{if(actions!=null)actions.onSpeak(m.text);});
        v.findViewById(R.id.actionShare).setOnClickListener(x->{if(actions!=null)actions.onShare(m.text);});
        ((TextView)v.findViewById(R.id.stats)).setText(info.optString("stats"));
    }

    static CharSequence highlight(String code){
        SpannableString s=new SpannableString(code);
        for(CodeHighlighter.Span span:CodeHighlighter.highlight(code)){
            s.setSpan(new ForegroundColorSpan(COLORS[span.type]),span.start,span.end,Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        }
        return s;
    }

    static final class VH extends RecyclerView.ViewHolder{final TextView text;VH(@NonNull View itemView){super(itemView);text=itemView.findViewById(R.id.messageText);}}
}
