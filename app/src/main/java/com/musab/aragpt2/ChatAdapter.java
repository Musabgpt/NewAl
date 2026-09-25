package com.musab.aragpt2;

import android.graphics.Color;
import android.text.SpannableString;
import android.text.Spanned;
import android.text.style.ForegroundColorSpan;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;
import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;
import java.util.ArrayList;
import java.util.List;

public final class ChatAdapter extends RecyclerView.Adapter<ChatAdapter.VH> {
    /** Buttons on a task result card, plus long-press copy on any bubble. */
    public interface Actions {
        void onRerun();
        void onFiles();
        void onOpenTermux();
        void onUndo();
        void onCopy(String text);
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
    private Actions actions;

    public void setActions(Actions a){actions=a;}
    public void setAll(List<ChatMessage> messages){items.clear();items.addAll(messages);notifyDataSetChanged();}
    public void add(ChatMessage message){items.add(message);notifyItemInserted(items.size()-1);}
    public int getCount(){return items.size();}
    /** Replaces the last message in place (used while a reply is streaming). */
    public void updateLast(ChatMessage message){if(items.isEmpty()){add(message);return;}items.set(items.size()-1,message);notifyItemChanged(items.size()-1);}
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
        vh.text.setOnLongClickListener(v->{if(actions!=null)actions.onCopy(vh.text.getText().toString());return true;});
        return vh;
    }

    private static void bind(View root,int id,Runnable r){View b=root.findViewById(id);if(b!=null)b.setOnClickListener(v->r.run());}

    @Override public void onBindViewHolder(@NonNull VH holder,int position){
        ChatMessage m=items.get(position);
        if(m.role==ChatMessage.ROLE_CODE)holder.text.setText(highlight(m.text));
        else holder.text.setText(m.text);
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
