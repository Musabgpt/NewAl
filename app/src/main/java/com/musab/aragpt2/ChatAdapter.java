package com.musab.aragpt2;

import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;
import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;
import java.util.ArrayList;
import java.util.List;

public final class ChatAdapter extends RecyclerView.Adapter<ChatAdapter.VH> {
    private final List<ChatMessage> items = new ArrayList<>();
    public void setAll(List<ChatMessage> messages){items.clear();items.addAll(messages);notifyDataSetChanged();}
    public void add(ChatMessage message){items.add(message);notifyItemInserted(items.size()-1);}
    public int getCount(){return items.size();}
    /** Replaces the last message in place (used while a reply is streaming). */
    public void updateLast(ChatMessage message){if(items.isEmpty()){add(message);return;}items.set(items.size()-1,message);notifyItemChanged(items.size()-1);}
    @Override public int getItemViewType(int position){return items.get(position).role;}
    @Override public int getItemCount(){return items.size();}
    @NonNull @Override public VH onCreateViewHolder(@NonNull ViewGroup parent,int viewType){int layout=viewType==ChatMessage.ROLE_USER?R.layout.item_message_user:R.layout.item_message_assistant;return new VH(LayoutInflater.from(parent.getContext()).inflate(layout,parent,false));}
    @Override public void onBindViewHolder(@NonNull VH holder,int position){holder.text.setText(items.get(position).text);}
    static final class VH extends RecyclerView.ViewHolder{final TextView text;VH(@NonNull View itemView){super(itemView);text=itemView.findViewById(R.id.messageText);}}
}
