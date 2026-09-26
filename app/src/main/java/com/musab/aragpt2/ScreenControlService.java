package com.musab.aragpt2;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.graphics.Rect;
import android.os.Build;
import android.os.Bundle;
import android.os.SystemClock;
import android.util.DisplayMetrics;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Screen control (the user enables it once in Accessibility settings): reads the screen as a
 * numbered element list for {@link PhoneAgent}, taps, types, scrolls and presses system buttons.
 * It only acts when the assistant is carrying out a request.
 */
public final class ScreenControlService extends AccessibilityService {
    static volatile ScreenControlService instance;

    private volatile long lastEventAt;
    private volatile String lastPackage = "";
    /** Nodes of the last snapshot, index = element id - 1. */
    private final List<AccessibilityNodeInfo> nodes = new ArrayList<>();

    @Override protected void onServiceConnected() { instance = this; }

    @Override public void onAccessibilityEvent(AccessibilityEvent event) {
        lastEventAt = SystemClock.uptimeMillis();
        if (event.getEventType() == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED && event.getPackageName() != null)
            lastPackage = event.getPackageName().toString();
    }

    @Override public void onInterrupt() {}

    @Override public boolean onUnbind(android.content.Intent intent) {
        instance = null;
        return super.onUnbind(intent);
    }

    // ------------------------------------------------------------------ reading the screen

    private static final int MAX_ELEMENTS = 80, MAX_LABEL = 70;

    /** The current screen as numbered elements; element ids refer to this snapshot. */
    synchronized ScreenState snapshot() {
        nodes.clear();
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return ScreenState.unavailable("(screen not readable right now)");
        String pkg = root.getPackageName() == null ? "" : root.getPackageName().toString();
        List<ScreenState.Element> out = new ArrayList<>();
        boolean[] scrollable = {false};
        walk(root, out, scrollable, 0);
        return new ScreenState(pkg, appLabel(pkg), out, scrollable[0]);
    }

    private void walk(AccessibilityNodeInfo n, List<ScreenState.Element> out, boolean[] scrollable, int depth) {
        if (n == null || out.size() >= MAX_ELEMENTS || depth > 40) return;
        if (!n.isVisibleToUser()) return;
        if (n.isScrollable()) scrollable[0] = true;
        String cls = n.getClassName() == null ? "" : n.getClassName().toString();
        String own = label(n);
        if (n.isEditable()) {
            boolean hint = Build.VERSION.SDK_INT >= 26 && n.isShowingHintText();
            CharSequence h = Build.VERSION.SDK_INT >= 26 ? n.getHintText() : null;
            String text = n.getText() == null ? "" : clean(n.getText());
            boolean empty = hint || text.isEmpty();
            String shown = empty ? (h != null ? clean(h) : text.isEmpty() && n.getContentDescription() != null ? clean(n.getContentDescription()) : text) : text;
            add(out, n, "field", shown, n.isFocused(), null, empty);
            return;
        }
        if (n.isCheckable()) {
            String l = own.isEmpty() ? childText(n) : own;
            add(out, n, cls.contains("Switch") || cls.contains("Toggle") ? "switch" : "checkbox", l, false, n.isChecked(), false);
            return;
        }
        if (n.isClickable() || n.isLongClickable()) {
            String l = own.isEmpty() ? childText(n) : own;
            String role = cls.contains("Button") || cls.contains("ImageView") ? "button" : cls.contains("Tab") ? "tab" : "item";
            if (!l.isEmpty() || !shortId(n).isEmpty()) add(out, n, role, l, n.isFocused(), null, false);
            // Controls inside a tappable row (a checkbox in a list item, a button in a card) are listed too.
            for (int i = 0; i < n.getChildCount(); i++) walkInteractive(n.getChild(i), out, scrollable, depth + 1);
            return;
        }
        if (!own.isEmpty() && (out.isEmpty() || !out.get(out.size() - 1).label.equals(own)))
            add(out, n, "text", own, false, null, false);
        for (int i = 0; i < n.getChildCount(); i++) walk(n.getChild(i), out, scrollable, depth + 1);
    }

    /** Inside a tappable parent: only the interactive descendants (their text is already the parent's label). */
    private void walkInteractive(AccessibilityNodeInfo n, List<ScreenState.Element> out, boolean[] scrollable, int depth) {
        if (n == null || !n.isVisibleToUser() || depth > 40) return;
        if (n.isEditable() || n.isCheckable() || n.isClickable()) { walk(n, out, scrollable, depth); return; }
        for (int i = 0; i < n.getChildCount(); i++) walkInteractive(n.getChild(i), out, scrollable, depth + 1);
    }

    private void add(List<ScreenState.Element> out, AccessibilityNodeInfo n, String role, String label, boolean focused, Boolean checked, boolean empty) {
        nodes.add(n);
        out.add(new ScreenState.Element(nodes.size(), role, label, focused, checked, shortId(n), empty));
    }

    private static String label(AccessibilityNodeInfo n) {
        CharSequence t = n.getText();
        if (t != null && t.toString().trim().length() > 0) return clean(t);
        CharSequence d = n.getContentDescription();
        return d != null && d.toString().trim().length() > 0 ? clean(d) : "";
    }

    /** Text of the non-interactive descendants, joined: the label of a tappable row. */
    private static String childText(AccessibilityNodeInfo n) {
        StringBuilder b = new StringBuilder();
        ArrayDeque<AccessibilityNodeInfo> q = new ArrayDeque<>();
        for (int i = 0; i < n.getChildCount(); i++) q.add(n.getChild(i));
        while (!q.isEmpty() && b.length() < MAX_LABEL) {
            AccessibilityNodeInfo c = q.poll();
            if (c == null || !c.isVisibleToUser() || c.isClickable() || c.isEditable() || c.isCheckable()) continue;
            String l = label(c);
            if (!l.isEmpty()) b.append(b.length() == 0 ? "" : ", ").append(l);
            for (int i = 0; i < c.getChildCount(); i++) q.add(c.getChild(i));
        }
        return b.length() > MAX_LABEL ? b.substring(0, MAX_LABEL) : b.toString();
    }

    private static String clean(CharSequence s) {
        String t = s.toString().replace('\n', ' ').replace('"', '\'').replaceAll("\\s+", " ").trim();
        return t.length() > MAX_LABEL ? t.substring(0, MAX_LABEL) + "…" : t;
    }

    private static String shortId(AccessibilityNodeInfo n) {
        String id = n.getViewIdResourceName();
        if (id == null) return "";
        int slash = id.indexOf('/');
        return slash >= 0 ? id.substring(slash + 1) : id;
    }

    private String appLabel(String pkg) {
        try {
            android.content.pm.PackageManager pm = getPackageManager();
            return pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString();
        } catch (Exception e) {
            return pkg;
        }
    }

    String currentPackage() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        return root != null && root.getPackageName() != null ? root.getPackageName().toString() : lastPackage;
    }

    /** Waits until the screen has been quiet for a moment (animations, loading), at most maxMs. */
    void waitForIdle(long quietMs, long maxMs) {
        long start = SystemClock.uptimeMillis();
        SystemClock.sleep(Math.min(quietMs, 150));
        while (SystemClock.uptimeMillis() - start < maxMs) {
            if (SystemClock.uptimeMillis() - lastEventAt >= quietMs) return;
            SystemClock.sleep(50);
        }
    }

    /** Waits until an app from {@code packagePrefix} is in front. */
    boolean waitForPackage(String packagePrefix, long maxMs) {
        long end = SystemClock.uptimeMillis() + maxMs;
        while (SystemClock.uptimeMillis() < end) {
            if (currentPackage().startsWith(packagePrefix)) return true;
            SystemClock.sleep(100);
        }
        return false;
    }

    // ------------------------------------------------------------------ acting on elements

    private synchronized AccessibilityNodeInfo node(int id) {
        return id >= 1 && id <= nodes.size() ? nodes.get(id - 1) : null;
    }

    boolean tap(int id) {
        AccessibilityNodeInfo n = node(id);
        if (n == null) return false;
        for (AccessibilityNodeInfo c = n; c != null; c = c.getParent()) {
            if (c.isClickable() && c.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return true;
        }
        return tapAt(n);
    }

    boolean longPress(int id) {
        AccessibilityNodeInfo n = node(id);
        if (n == null) return false;
        for (AccessibilityNodeInfo c = n; c != null; c = c.getParent()) {
            if (c.isLongClickable() && c.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)) return true;
        }
        return gesture(center(n), center(n), 700);
    }

    /** Types into element {@code id}; a tap on a non-field (a search icon) first opens its field. */
    boolean type(int id, String text) {
        AccessibilityNodeInfo n = node(id);
        if (n == null) return false;
        if (!n.isEditable()) {
            tap(id);
            waitForIdle(300, 1500);
            AccessibilityNodeInfo root = getRootInActiveWindow();
            n = root == null ? null : root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
            if (n == null || !n.isEditable()) n = firstEditable(root);
            if (n == null) return false;
        }
        n.performAction(AccessibilityNodeInfo.ACTION_FOCUS);
        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
        return n.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
    }

    /** Types into whichever field has the focus (or the first field on screen). */
    boolean typeFocused(String text) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return false;
        AccessibilityNodeInfo field = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (field == null || !field.isEditable()) field = firstEditable(root);
        if (field == null) return false;
        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
        return field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
    }

    /** The keyboard's enter / search / send key on the focused field (Android 11+). */
    boolean enter() {
        if (Build.VERSION.SDK_INT < 30) return false;
        AccessibilityNodeInfo root = getRootInActiveWindow();
        AccessibilityNodeInfo field = root == null ? null : root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (field == null) field = firstEditable(root);
        return field != null && field.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.getId());
    }

    boolean scroll(String direction) {
        String d = direction.toLowerCase(Locale.ROOT);
        AccessibilityNodeInfo best = null;
        int bestArea = 0;
        ArrayDeque<AccessibilityNodeInfo> q = new ArrayDeque<>();
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root != null) q.add(root);
        Rect r = new Rect();
        while (!q.isEmpty()) {
            AccessibilityNodeInfo n = q.poll();
            if (n == null) continue;
            if (n.isScrollable() && n.isVisibleToUser()) {
                n.getBoundsInScreen(r);
                int area = r.width() * r.height();
                if (area > bestArea) { bestArea = area; best = n; }
            }
            for (int i = 0; i < n.getChildCount(); i++) q.add(n.getChild(i));
        }
        boolean vertical = d.equals("down") || d.equals("up");
        if (best != null && vertical && best.performAction(d.equals("down")
                ? AccessibilityNodeInfo.ACTION_SCROLL_FORWARD : AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD)) return true;
        // A finger swipe works where the view does not expose scroll actions.
        DisplayMetrics dm = getResources().getDisplayMetrics();
        float cx = dm.widthPixels / 2f, cy = dm.heightPixels / 2f, dy = dm.heightPixels * 0.3f, dx = dm.widthPixels * 0.35f;
        switch (d) {
            case "up": return gesture(new float[]{cx, cy - dy}, new float[]{cx, cy + dy}, 350);
            case "left": return gesture(new float[]{cx - dx, cy}, new float[]{cx + dx, cy}, 300);
            case "right": return gesture(new float[]{cx + dx, cy}, new float[]{cx - dx, cy}, 300);
            default: return gesture(new float[]{cx, cy + dy}, new float[]{cx, cy - dy}, 350);
        }
    }

    boolean press(String button) {
        switch (button.toLowerCase(Locale.ROOT)) {
            case "back": return performGlobalAction(GLOBAL_ACTION_BACK);
            case "home": return performGlobalAction(GLOBAL_ACTION_HOME);
            case "recents": return performGlobalAction(GLOBAL_ACTION_RECENTS);
            case "notifications": return performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS);
            case "quick_settings": return performGlobalAction(GLOBAL_ACTION_QUICK_SETTINGS);
            case "lock": return performGlobalAction(GLOBAL_ACTION_LOCK_SCREEN);
            case "screenshot": return performGlobalAction(GLOBAL_ACTION_TAKE_SCREENSHOT);
            default: return false;
        }
    }

    // ------------------------------------------------------------------ overlay over other apps

    private final android.os.Handler main = new android.os.Handler(android.os.Looper.getMainLooper());
    private android.view.View statusBar;
    private android.widget.TextView statusText;

    private android.view.WindowManager.LayoutParams overlayParams(boolean focusable) {
        android.view.WindowManager.LayoutParams lp = new android.view.WindowManager.LayoutParams(
                android.view.WindowManager.LayoutParams.MATCH_PARENT, android.view.WindowManager.LayoutParams.WRAP_CONTENT,
                android.view.WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                (focusable ? 0 : android.view.WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE)
                        | android.view.WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                android.graphics.PixelFormat.TRANSLUCENT);
        lp.gravity = android.view.Gravity.TOP;
        return lp;
    }

    private android.widget.LinearLayout panel() {
        android.widget.LinearLayout box = new android.widget.LinearLayout(this);
        box.setOrientation(android.widget.LinearLayout.HORIZONTAL);
        box.setGravity(android.view.Gravity.CENTER_VERTICAL);
        box.setBackgroundColor(0xEE1E1B4B);
        int pad = (int) (8 * getResources().getDisplayMetrics().density);
        box.setPadding(pad * 2, pad * 4, pad * 2, pad);
        box.setLayoutDirection(android.view.View.LAYOUT_DIRECTION_RTL);
        return box;
    }

    private android.widget.Button button(String text, android.view.View.OnClickListener l) {
        android.widget.Button b = new android.widget.Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setOnClickListener(l);
        return b;
    }

    /** A small bar at the top of the screen while the assistant works, with a stop button. */
    void showStatus(String text, Runnable onStop) {
        main.post(() -> {
            if (statusBar == null) {
                android.widget.LinearLayout box = panel();
                statusText = new android.widget.TextView(this);
                statusText.setTextColor(0xFFFFFFFF);
                statusText.setMaxLines(2);
                box.addView(statusText, new android.widget.LinearLayout.LayoutParams(0, -2, 1f));
                box.addView(button("⏹", v -> { if (onStop != null) onStop.run(); hideStatus(); }));
                try {
                    ((android.view.WindowManager) getSystemService(WINDOW_SERVICE)).addView(box, overlayParams(false));
                    statusBar = box;
                } catch (RuntimeException ignored) {
                    return;
                }
            }
            statusText.setText("🤖 " + text);
        });
    }

    void hideStatus() {
        main.post(() -> {
            if (statusBar == null) return;
            try { ((android.view.WindowManager) getSystemService(WINDOW_SERVICE)).removeView(statusBar); } catch (RuntimeException ignored) {}
            statusBar = null;
            statusText = null;
        });
    }

    /**
     * Asks the user over whatever app is showing ("send this message?"). Blocks the calling
     * (worker) thread; false after {@code timeoutMs} without an answer.
     */
    boolean confirmOverlay(String message, String yes, long timeoutMs) {
        java.util.concurrent.CountDownLatch done = new java.util.concurrent.CountDownLatch(1);
        boolean[] ok = {false};
        android.view.View[] shown = {null};
        main.post(() -> {
            android.widget.LinearLayout box = panel();
            box.setOrientation(android.widget.LinearLayout.VERTICAL);
            android.widget.TextView t = new android.widget.TextView(this);
            t.setTextColor(0xFFFFFFFF);
            t.setTextSize(16);
            t.setText(message);
            box.addView(t);
            android.widget.LinearLayout row = new android.widget.LinearLayout(this);
            row.addView(button(yes, v -> { ok[0] = true; done.countDown(); }));
            row.addView(button("إلغاء", v -> done.countDown()));
            box.addView(row);
            try {
                ((android.view.WindowManager) getSystemService(WINDOW_SERVICE)).addView(box, overlayParams(true));
                shown[0] = box;
            } catch (RuntimeException e) {
                done.countDown();
            }
        });
        try {
            done.await(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        main.post(() -> {
            if (shown[0] != null) try { ((android.view.WindowManager) getSystemService(WINDOW_SERVICE)).removeView(shown[0]); } catch (RuntimeException ignored) {}
        });
        return ok[0];
    }

    // ------------------------------------------------------------------ recipes

    /**
     * Clicks the first visible control whose resource id or text / description matches, trying
     * until {@code maxMs}; e.g. WhatsApp's send button after a wa.me link filled in the message.
     */
    boolean clickFirst(String[] viewIds, Pattern text, long maxMs) {
        long end = SystemClock.uptimeMillis() + maxMs;
        while (SystemClock.uptimeMillis() < end) {
            AccessibilityNodeInfo root = getRootInActiveWindow();
            if (root != null) {
                for (String id : viewIds) {
                    List<AccessibilityNodeInfo> found = root.findAccessibilityNodeInfosByViewId(id);
                    for (AccessibilityNodeInfo n : found) if (n.isVisibleToUser() && clickNode(n)) return true;
                }
                AccessibilityNodeInfo n = find(root, text, false);
                if (n != null && clickNode(n)) return true;
            }
            SystemClock.sleep(200);
        }
        return false;
    }

    /**
     * Sets the switch matching {@code label} (or the page's main switch) on this screen.
     * Returns "on", "off", "already on", "already off", or null when there is no such switch.
     */
    String setSwitch(Pattern label, boolean on, long maxMs) {
        long end = SystemClock.uptimeMillis() + maxMs;
        while (SystemClock.uptimeMillis() < end) {
            AccessibilityNodeInfo root = getRootInActiveWindow();
            AccessibilityNodeInfo sw = root == null ? null : find(root, label, true);
            if (sw == null && root != null) sw = firstCheckable(root);
            if (sw != null) {
                if (sw.isChecked() == on) return on ? "already on" : "already off";
                if (clickNode(sw)) return on ? "on" : "off";
            }
            SystemClock.sleep(200);
        }
        return null;
    }

    private static boolean clickNode(AccessibilityNodeInfo n) {
        for (AccessibilityNodeInfo c = n; c != null; c = c.getParent()) {
            if (c.isClickable() && c.isEnabled() && c.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return true;
        }
        return false;
    }

    /** A visible node whose text / description matches; with {@code checkable}, the switch next to it. */
    private static AccessibilityNodeInfo find(AccessibilityNodeInfo root, Pattern text, boolean checkable) {
        ArrayDeque<AccessibilityNodeInfo> q = new ArrayDeque<>();
        q.add(root);
        while (!q.isEmpty()) {
            AccessibilityNodeInfo n = q.poll();
            if (n == null) continue;
            if (n.isVisibleToUser()) {
                String l = label(n);
                if (!l.isEmpty() && text.matcher(l).find()) {
                    if (!checkable) return n;
                    if (n.isCheckable()) return n;
                    // The switch sits in the same row as the label.
                    AccessibilityNodeInfo row = n.getParent();
                    for (int level = 0; row != null && level < 3; level++, row = row.getParent()) {
                        AccessibilityNodeInfo c = firstCheckable(row);
                        if (c != null) return c;
                        if (row.isClickable()) break;
                    }
                }
            }
            for (int i = 0; i < n.getChildCount(); i++) q.add(n.getChild(i));
        }
        return null;
    }

    private static AccessibilityNodeInfo firstCheckable(AccessibilityNodeInfo root) {
        ArrayDeque<AccessibilityNodeInfo> q = new ArrayDeque<>();
        q.add(root);
        while (!q.isEmpty()) {
            AccessibilityNodeInfo n = q.poll();
            if (n == null) continue;
            if (n.isCheckable() && n.isVisibleToUser()) return n;
            for (int i = 0; i < n.getChildCount(); i++) q.add(n.getChild(i));
        }
        return null;
    }

    private static AccessibilityNodeInfo firstEditable(AccessibilityNodeInfo root) {
        if (root == null) return null;
        ArrayDeque<AccessibilityNodeInfo> q = new ArrayDeque<>();
        q.add(root);
        while (!q.isEmpty()) {
            AccessibilityNodeInfo n = q.poll();
            if (n == null) continue;
            if (n.isEditable() && n.isVisibleToUser()) return n;
            for (int i = 0; i < n.getChildCount(); i++) q.add(n.getChild(i));
        }
        return null;
    }

    private static float[] center(AccessibilityNodeInfo n) {
        Rect r = new Rect();
        n.getBoundsInScreen(r);
        return new float[]{r.exactCenterX(), r.exactCenterY()};
    }

    private boolean tapAt(AccessibilityNodeInfo n) {
        float[] c = center(n);
        return gesture(c, c, 60);
    }

    private boolean gesture(float[] from, float[] to, long ms) {
        Path p = new Path();
        p.moveTo(from[0], from[1]);
        if (from[0] != to[0] || from[1] != to[1]) p.lineTo(to[0], to[1]);
        return dispatchGesture(new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(p, 0, ms)).build(), null, null);
    }
}
