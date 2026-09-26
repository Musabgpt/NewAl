package com.musab.aragpt2;

import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.text.SpannableStringBuilder;
import android.text.Spanned;
import android.text.TextPaint;
import android.text.method.LinkMovementMethod;
import android.text.style.BackgroundColorSpan;
import android.text.style.ClickableSpan;
import android.text.style.ForegroundColorSpan;
import android.text.style.LeadingMarginSpan;
import android.text.style.QuoteSpan;
import android.text.style.RelativeSizeSpan;
import android.text.style.StrikethroughSpan;
import android.text.style.StyleSpan;
import android.text.style.TypefaceSpan;
import android.util.TypedValue;
import android.view.View;
import android.view.ViewGroup;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.TableLayout;
import android.widget.TableRow;
import android.widget.TextView;

import java.util.List;

/** Renders {@link Markdown} into views: text blocks as styled text, code blocks and tables as their own cards. */
final class MarkdownView {
    interface Callbacks {
        void copy(String text);
        void openUrl(String url);
        /** A [n] citation was tapped. */
        void cite(int n);
    }

    static final int TEXT = Color.parseColor("#E5E7EB"), MUTED = Color.parseColor("#94A3B8"), ACCENT = Color.parseColor("#A78BFA"),
            CODE_BG = Color.parseColor("#0D1117"), INLINE_CODE_BG = Color.parseColor("#1F2937"), BORDER = Color.parseColor("#263350");

    private MarkdownView() {}

    static void render(LinearLayout into, String md, boolean streaming, Callbacks cb) {
        Context c = into.getContext();
        into.removeAllViews();
        List<Markdown.Block> blocks = Markdown.parse(md);
        SpannableStringBuilder text = new SpannableStringBuilder();
        for (int i = 0; i < blocks.size(); i++) {
            Markdown.Block b = blocks.get(i);
            boolean lastBlock = i == blocks.size() - 1;
            switch (b.type) {
                case CODE:
                    flush(into, text, cb);
                    into.addView(code(c, b.text + (streaming && lastBlock && b.open ? " ▍" : ""), b.lang, cb));
                    continue;
                case TABLE:
                    flush(into, text, cb);
                    into.addView(table(c, b.rows, cb));
                    continue;
                case RULE: {
                    flush(into, text, cb);
                    View line = new View(c);
                    line.setBackgroundColor(BORDER);
                    LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(c, 1));
                    lp.setMargins(0, dp(c, 8), 0, dp(c, 8));
                    into.addView(line, lp);
                    continue;
                }
                default:
                    if (text.length() > 0) text.append(b.type == Markdown.Type.BULLET || b.type == Markdown.Type.NUMBERED ? "\n" : "\n\n");
                    appendBlock(c, text, b, cb);
            }
        }
        if (streaming && (blocks.isEmpty() || blocks.get(blocks.size() - 1).type != Markdown.Type.CODE)) {
            int s = text.length();
            text.append(" ▍");
            text.setSpan(new ForegroundColorSpan(ACCENT), s, text.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        }
        flush(into, text, cb);
    }

    private static void appendBlock(Context c, SpannableStringBuilder out, Markdown.Block b, Callbacks cb) {
        int start = out.length();
        switch (b.type) {
            case HEADING: {
                appendInline(out, b.text, cb);
                float size = b.level == 1 ? 1.4f : b.level == 2 ? 1.25f : 1.1f;
                out.setSpan(new RelativeSizeSpan(size), start, out.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                out.setSpan(new StyleSpan(Typeface.BOLD), start, out.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                break;
            }
            case BULLET:
            case NUMBERED: {
                String marker = b.type == Markdown.Type.BULLET ? (b.level == 0 ? "•  " : "◦  ") : b.number + ".  ";
                out.append(marker);
                out.setSpan(new ForegroundColorSpan(ACCENT), start, out.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                appendInline(out, b.text, cb);
                int indent = dp(c, 6 + 16 * b.level);
                out.setSpan(new LeadingMarginSpan.Standard(indent, indent + dp(c, 16)), start, out.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                break;
            }
            case QUOTE:
                appendInline(out, b.text, cb);
                out.setSpan(new QuoteSpan(ACCENT), start, out.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                out.setSpan(new ForegroundColorSpan(MUTED), start, out.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                break;
            default:
                appendInline(out, b.text, cb);
        }
    }

    static void appendInline(SpannableStringBuilder out, String md, Callbacks cb) {
        Markdown.Inline in = Markdown.inline(md);
        int base = out.length();
        out.append(in.text);
        for (Markdown.Span s : in.spans) {
            int a = base + s.start, z = base + s.end;
            switch (s.style) {
                case BOLD: out.setSpan(new StyleSpan(Typeface.BOLD), a, z, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE); break;
                case ITALIC: out.setSpan(new StyleSpan(Typeface.ITALIC), a, z, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE); break;
                case STRIKE: out.setSpan(new StrikethroughSpan(), a, z, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE); break;
                case CODE:
                    out.setSpan(new TypefaceSpan("monospace"), a, z, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                    out.setSpan(new BackgroundColorSpan(INLINE_CODE_BG), a, z, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                    out.setSpan(new ForegroundColorSpan(Color.parseColor("#F9A8D4")), a, z, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                    break;
                case LINK:
                    out.setSpan(new Click(ACCENT, () -> cb.openUrl(s.url)), a, z, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                    break;
                case CITE: {
                    int n = Integer.parseInt(s.url);
                    out.setSpan(new Click(ACCENT, () -> cb.cite(n)), a, z, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                    out.setSpan(new RelativeSizeSpan(0.8f), a, z, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                    break;
                }
            }
        }
    }

    private static final class Click extends ClickableSpan {
        private final int color;
        private final Runnable action;
        Click(int color, Runnable action) { this.color = color; this.action = action; }
        @Override public void onClick(View widget) { action.run(); }
        @Override public void updateDrawState(TextPaint ds) { ds.setColor(color); ds.setUnderlineText(false); }
    }

    private static void flush(LinearLayout into, SpannableStringBuilder text, Callbacks cb) {
        if (text.length() == 0) return;
        TextView t = textView(into.getContext(), 16);
        t.setText(new SpannableStringBuilder(text));
        t.setMovementMethod(LinkMovementMethod.getInstance());
        t.setLineSpacing(0, 1.2f);
        into.addView(t);
        text.clear();
        text.clearSpans();
    }

    static TextView textView(Context c, float sp) {
        TextView t = new TextView(c);
        t.setTextColor(TEXT);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        // Arabic answers read right to left and English ones left to right, whatever the app's direction.
        t.setTextDirection(View.TEXT_DIRECTION_FIRST_STRONG);
        t.setTextAlignment(View.TEXT_ALIGNMENT_TEXT_START);
        return t;
    }

    private static View code(Context c, String code, String lang, Callbacks cb) {
        LinearLayout card = new LinearLayout(c);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackgroundResource(R.drawable.bubble_code);
        card.setLayoutDirection(View.LAYOUT_DIRECTION_LTR);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, dp(c, 8), 0, dp(c, 8));
        card.setLayoutParams(lp);

        LinearLayout head = new LinearLayout(c);
        head.setPadding(dp(c, 12), dp(c, 6), dp(c, 8), dp(c, 2));
        TextView name = textView(c, 12);
        name.setText(lang.isEmpty() ? "code" : lang);
        name.setTextColor(MUTED);
        head.addView(name, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView copy = textView(c, 12);
        copy.setText("⧉ نسخ");
        copy.setTextColor(MUTED);
        copy.setPadding(dp(c, 8), dp(c, 4), dp(c, 8), dp(c, 4));
        copy.setOnClickListener(v -> cb.copy(code.replace(" ▍", "")));
        head.addView(copy);
        card.addView(head);

        HorizontalScrollView scroll = new HorizontalScrollView(c);
        scroll.setHorizontalScrollBarEnabled(false);
        TextView body = new TextView(c);
        body.setTypeface(Typeface.MONOSPACE);
        body.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        body.setTextColor(TEXT);
        body.setTextDirection(View.TEXT_DIRECTION_LTR);
        body.setPadding(dp(c, 12), dp(c, 4), dp(c, 12), dp(c, 12));
        body.setText(lang.equals("math") ? code : ChatAdapter.highlight(code));
        body.setTextIsSelectable(true);
        scroll.addView(body);
        card.addView(scroll);
        return card;
    }

    private static View table(Context c, List<List<String>> rows, Callbacks cb) {
        HorizontalScrollView scroll = new HorizontalScrollView(c);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, dp(c, 8), 0, dp(c, 8));
        scroll.setLayoutParams(lp);
        TableLayout t = new TableLayout(c);
        t.setBackgroundColor(BORDER);
        t.setPadding(1, 1, 1, 1);
        for (int r = 0; r < rows.size(); r++) {
            TableRow row = new TableRow(c);
            for (String cell : rows.get(r)) {
                TextView tv = textView(c, 14);
                SpannableStringBuilder s = new SpannableStringBuilder();
                appendInline(s, cell, cb);
                if (r == 0) s.setSpan(new StyleSpan(Typeface.BOLD), 0, s.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
                tv.setText(s);
                tv.setPadding(dp(c, 10), dp(c, 6), dp(c, 10), dp(c, 6));
                tv.setBackgroundColor(r == 0 ? Color.parseColor("#1E293B") : Color.parseColor("#0F172A"));
                TableRow.LayoutParams p = new TableRow.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.MATCH_PARENT);
                p.setMargins(1, 1, 1, 1);
                row.addView(tv, p);
            }
            t.addView(row);
        }
        scroll.addView(t);
        return scroll;
    }

    static int dp(Context c, int v) { return Math.round(v * c.getResources().getDisplayMetrics().density); }
}
