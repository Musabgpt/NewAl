package dev.newal.code.lite;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.graphics.Rect;
import android.os.Build;
import android.os.Bundle;
import android.util.DisplayMetrics;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * NewAl Code's accessibility service: once the user turns it on (Settings > Accessibility > NewAl Code), the agent
 * can see what is on the screen (as numbered items: text, what they are, whether they can be tapped) and tap, type,
 * swipe and press back or home. It reads the screen only when the agent asks for it, and does nothing on its own.
 */
public class PhoneControlService extends AccessibilityService {
    static volatile PhoneControlService on;
    private static final int MAX_ITEMS = 150;

    /** What the last look at the screen numbered: [n] -> the node (and where it was, if it has gone stale since). */
    private final List<AccessibilityNodeInfo> items = new ArrayList<>();
    private final List<Rect> places = new ArrayList<>();

    @Override
    protected void onServiceConnected() {
        on = this;
    }

    @Override
    public boolean onUnbind(android.content.Intent intent) {
        on = null;
        return super.onUnbind(intent);
    }

    @Override
    public void onDestroy() {
        on = null;
        super.onDestroy();
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
    }

    @Override
    public void onInterrupt() {
    }

    static PhoneControlService need() {
        PhoneControlService s = on;
        if (s == null) {
            throw new IllegalStateException("the screen, taps and typing need NewAl Code's accessibility service: the "
                    + "user turns it on in Android's Settings > Accessibility > NewAl Code (the app's Phone settings "
                    + "open that page)");
        }
        return s;
    }

    // ------------------------------------------------------------------------------------------------ looking

    private AccessibilityNodeInfo root() throws InterruptedException {
        for (int i = 0; i < 15; i++) {
            AccessibilityNodeInfo r = getRootInActiveWindow();
            if (r != null) {
                return r;
            }
            Thread.sleep(200);             // between two screens there is none
        }
        throw new IllegalStateException("no window on the screen right now");
    }

    synchronized JSONObject screen() throws Exception {
        AccessibilityNodeInfo root = root();
        items.clear();
        places.clear();
        StringBuilder sb = new StringBuilder();
        CharSequence pkg = root.getPackageName();
        sb.append("App: ").append(label(pkg)).append(pkg != null ? " (" + pkg + ")" : "").append('\n');
        walk(root, sb, 0);
        if (items.isEmpty()) {
            sb.append("(nothing readable on this screen)");
        } else if (items.size() >= MAX_ITEMS) {
            sb.append("(more below: scroll to see it)");
        }
        JSONObject o = Phone.ok(sb.toString().trim());
        o.put("items", items.size());
        return o;
    }

    private String label(CharSequence pkg) {
        if (pkg == null) {
            return "?";
        }
        try {
            android.content.pm.PackageManager pm = getPackageManager();
            return String.valueOf(pm.getApplicationLabel(pm.getApplicationInfo(pkg.toString(), 0)));
        } catch (Exception e) {
            return pkg.toString();
        }
    }

    private void walk(AccessibilityNodeInfo n, StringBuilder sb, int depth) {
        if (n == null || items.size() >= MAX_ITEMS || depth > 60) {
            return;
        }
        if (n.isVisibleToUser()) {
            String text = words(n);
            boolean tap = n.isClickable() || n.isLongClickable();
            boolean field = n.isEditable();
            boolean scroll = n.isScrollable();
            if (!text.isEmpty() || field || (tap && n.getChildCount() == 0)) {
                Rect r = new Rect();
                n.getBoundsInScreen(r);
                if (r.width() > 0 && r.height() > 0) {
                    items.add(n);
                    places.add(r);
                    sb.append('[').append(items.size()).append("] ");
                    sb.append(text.isEmpty() ? "(" + kind(n) + ")" : text);
                    List<String> tags = new ArrayList<>();
                    String k = kind(n);
                    if (!k.isEmpty() && !text.isEmpty()) {
                        tags.add(k);
                    }
                    if (field) {
                        tags.add("field");
                    }
                    if (n.isCheckable()) {
                        tags.add(n.isChecked() ? "on" : "off");
                    }
                    if (tap || clickableParent(n) != null) {
                        tags.add("tap");
                    }
                    if (scroll) {
                        tags.add("scrolls");
                    }
                    if (n.isFocused()) {
                        tags.add("focused");
                    }
                    if (n.isSelected()) {
                        tags.add("selected");
                    }
                    if (!n.isEnabled()) {
                        tags.add("disabled");
                    }
                    if (!tags.isEmpty()) {
                        sb.append(" · ").append(String.join(", ", tags));
                    }
                    sb.append('\n');
                }
            }
        }
        for (int i = 0; i < n.getChildCount(); i++) {
            walk(n.getChild(i), sb, depth + 1);
        }
    }

    private static String words(AccessibilityNodeInfo n) {
        CharSequence t = n.getText();
        CharSequence d = n.getContentDescription();
        String s = t != null && t.length() > 0 ? t.toString() : d != null ? d.toString() : "";
        if (s.isEmpty() && Build.VERSION.SDK_INT >= 26 && n.getHintText() != null) {
            s = "hint: " + n.getHintText();
        }
        s = s.replace('\n', ' ').trim();
        return s.length() > 90 ? s.substring(0, 87) + "…" : s;
    }

    private static String kind(AccessibilityNodeInfo n) {
        CharSequence c = n.getClassName();
        String k = c == null ? "" : c.toString().substring(c.toString().lastIndexOf('.') + 1);
        switch (k) {
            case "Button":
            case "ImageButton":
            case "MaterialButton":
                return "button";
            case "EditText":
            case "AutoCompleteTextView":
                return "text field";
            case "CheckBox":
                return "checkbox";
            case "Switch":
            case "SwitchCompat":
            case "SwitchMaterial":
            case "ToggleButton":
                return "switch";
            case "RadioButton":
                return "option";
            case "ImageView":
                return "image";
            case "SeekBar":
                return "slider";
            case "TextView":
            case "View":
            case "ViewGroup":
            case "FrameLayout":
            case "LinearLayout":
            case "RelativeLayout":
                return "";
            default:
                return k.toLowerCase(Locale.ROOT);
        }
    }

    private static AccessibilityNodeInfo clickableParent(AccessibilityNodeInfo n) {
        AccessibilityNodeInfo p = n;
        for (int i = 0; p != null && i < 8; i++) {
            if (p.isClickable()) {
                return p;
            }
            p = p.getParent();
        }
        return null;
    }

    // ------------------------------------------------------------------------------------------------ acting

    synchronized JSONObject tap(JSONObject a) throws Exception {
        if (a.has("x") && a.has("y")) {
            gesture(a.optInt("x"), a.optInt("y"), a.optInt("x"), a.optInt("y"), 60);
            return settle("tapped at " + a.optInt("x") + "," + a.optInt("y"));
        }
        int idx;
        if (a.has("item") && a.optInt("item", 0) > 0) {
            idx = a.optInt("item") - 1;
            if (idx >= items.size()) {
                throw new IllegalArgumentException("no item " + (idx + 1) + " on the last screen (it has "
                        + items.size() + "): look again with screen");
            }
        } else {
            String want = a.optString("text").trim().toLowerCase(Locale.ROOT);
            if (want.isEmpty()) {
                throw new IllegalArgumentException("tap needs item (a number from screen), text, or x and y");
            }
            screen();                          // the screen as it is now, then the best match on it
            idx = -1;
            int score = 0;
            for (int i = 0; i < items.size(); i++) {
                String w = words(items.get(i)).toLowerCase(Locale.ROOT);
                int s = w.equals(want) ? 3 : w.startsWith(want) ? 2 : w.contains(want) ? 1 : 0;
                if (s > score) {
                    score = s;
                    idx = i;
                }
            }
            if (idx < 0) {
                throw new IllegalArgumentException("nothing on the screen says \"" + a.optString("text") + "\"");
            }
        }
        AccessibilityNodeInfo n = items.get(idx);
        String what = "[" + (idx + 1) + "] " + words(n);
        boolean fresh = n.refresh();
        AccessibilityNodeInfo target = fresh ? clickableParent(n) : null;
        if (target != null && target.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
            return settle("tapped " + what);
        }
        Rect r = places.get(idx);
        gesture(r.centerX(), r.centerY(), r.centerX(), r.centerY(), 60);
        return settle("tapped " + what + " (at " + r.centerX() + "," + r.centerY() + ")");
    }

    synchronized JSONObject type(JSONObject a) throws Exception {
        String text = a.optString("text");
        AccessibilityNodeInfo target = null;
        if (a.optInt("item", 0) > 0 && a.optInt("item") <= items.size()) {
            target = items.get(a.optInt("item") - 1);
            target.refresh();
        }
        if (target == null) {
            target = root().findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        }
        if (target == null) {
            target = firstField(root());
        }
        if (target == null || !target.isEditable()) {
            throw new IllegalArgumentException("no text field to type into: tap one first");
        }
        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
        if (!target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) {
            throw new IllegalStateException("the field did not take the text");
        }
        return settle("typed " + text.length() + " characters into " + (words(target).isEmpty() ? "the field"
                : words(target)));
    }

    private static AccessibilityNodeInfo firstField(AccessibilityNodeInfo n) {
        if (n == null) {
            return null;
        }
        if (n.isEditable() && n.isVisibleToUser()) {
            return n;
        }
        for (int i = 0; i < n.getChildCount(); i++) {
            AccessibilityNodeInfo f = firstField(n.getChild(i));
            if (f != null) {
                return f;
            }
        }
        return null;
    }

    synchronized JSONObject swipe(String direction, boolean scroll) throws Exception {
        String d = direction.trim().toLowerCase(Locale.ROOT);
        // scroll names where the content goes (down: what is further down); swipe names where the finger goes.
        if (scroll) {
            d = d.equals("down") ? "up" : d.equals("up") ? "down" : d.equals("left") ? "right" : "left";
        }
        DisplayMetrics dm = getResources().getDisplayMetrics();
        int w = dm.widthPixels, h = dm.heightPixels, cx = w / 2, cy = h / 2;
        switch (d) {
            case "up":
                gesture(cx, (int) (h * 0.72), cx, (int) (h * 0.28), 350);
                break;
            case "down":
                gesture(cx, (int) (h * 0.28), cx, (int) (h * 0.72), 350);
                break;
            case "left":
                gesture((int) (w * 0.85), cy, (int) (w * 0.15), cy, 300);
                break;
            case "right":
                gesture((int) (w * 0.15), cy, (int) (w * 0.85), cy, 300);
                break;
            default:
                throw new IllegalArgumentException("direction: up, down, left or right");
        }
        return settle((scroll ? "scrolled " + direction : "swiped " + direction).trim());
    }

    synchronized JSONObject key(String name) throws Exception {
        int action;
        switch (name.trim().toLowerCase(Locale.ROOT)) {
            case "back":
                action = GLOBAL_ACTION_BACK;
                break;
            case "home":
                action = GLOBAL_ACTION_HOME;
                break;
            case "recents":
            case "apps":
                action = GLOBAL_ACTION_RECENTS;
                break;
            case "notifications":
                action = GLOBAL_ACTION_NOTIFICATIONS;
                break;
            case "quick_settings":
                action = GLOBAL_ACTION_QUICK_SETTINGS;
                break;
            case "power":
                action = GLOBAL_ACTION_POWER_DIALOG;
                break;
            case "lock":
                if (Build.VERSION.SDK_INT < 28) {
                    throw new IllegalStateException("lock needs Android 9");
                }
                action = GLOBAL_ACTION_LOCK_SCREEN;
                break;
            case "screenshot":
                if (Build.VERSION.SDK_INT < 28) {
                    throw new IllegalStateException("screenshot needs Android 9");
                }
                action = GLOBAL_ACTION_TAKE_SCREENSHOT;
                break;
            case "enter":
                AccessibilityNodeInfo f = root().findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
                if (f != null && Build.VERSION.SDK_INT >= 30 && f.performAction(
                        AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.getId())) {
                    return settle("pressed enter");
                }
                throw new IllegalStateException("no field to press enter in (tap the button that sends instead)");
            default:
                throw new IllegalArgumentException("key: back, home, recents, notifications, quick_settings, lock, "
                        + "screenshot, power or enter");
        }
        if (!performGlobalAction(action)) {
            throw new IllegalStateException("Android did not take " + name);
        }
        return settle("pressed " + name);
    }

    private void gesture(int x1, int y1, int x2, int y2, long ms) throws Exception {
        Path p = new Path();
        p.moveTo(Math.max(0, x1), Math.max(0, y1));
        p.lineTo(Math.max(0, x2), Math.max(0, y2));
        GestureDescription g = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(p, 0, ms)).build();
        CountDownLatch done = new CountDownLatch(1);
        boolean[] ok = {false};
        boolean sent = Phone.onMain(() -> dispatchGesture(g, new GestureResultCallback() {
            @Override
            public void onCompleted(GestureDescription d) {
                ok[0] = true;
                done.countDown();
            }

            @Override
            public void onCancelled(GestureDescription d) {
                done.countDown();
            }
        }, null));
        if (!sent || !done.await(5, TimeUnit.SECONDS) || !ok[0]) {
            throw new IllegalStateException("Android did not take the gesture");
        }
    }

    private static JSONObject settle(String text) throws Exception {
        Thread.sleep(600);                    // let the screen change before the model looks again
        return Phone.ok(text);
    }
}
