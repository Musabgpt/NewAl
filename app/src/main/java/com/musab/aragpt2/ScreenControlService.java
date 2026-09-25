package com.musab.aragpt2;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.graphics.Rect;
import android.os.Bundle;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import java.util.ArrayDeque;
import java.util.List;
import java.util.Locale;

/**
 * Screen control for the personal assistant (the user enables it once in Accessibility
 * settings): read what is on screen, tap items by their text, type into the focused field,
 * scroll, and press system buttons. It only acts when the assistant calls a tool.
 */
public final class ScreenControlService extends AccessibilityService {
    static volatile ScreenControlService instance;

    @Override protected void onServiceConnected() { instance = this; }
    @Override public void onAccessibilityEvent(AccessibilityEvent event) {}
    @Override public void onInterrupt() {}

    @Override public boolean onUnbind(android.content.Intent intent) {
        instance = null;
        return super.onUnbind(intent);
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

    /** Visible text and buttons, top to bottom, one per line; [button] marks tappable items. */
    String readScreen() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return "(screen not readable right now)";
        StringBuilder b = new StringBuilder();
        if (root.getPackageName() != null) b.append("app: ").append(root.getPackageName()).append('\n');
        ArrayDeque<AccessibilityNodeInfo> queue = new ArrayDeque<>();
        queue.add(root);
        int lines = 0;
        while (!queue.isEmpty() && lines < 150) {
            AccessibilityNodeInfo n = queue.poll();
            if (n == null || !n.isVisibleToUser()) continue;
            CharSequence text = n.getText() != null ? n.getText() : n.getContentDescription();
            if (text != null && text.toString().trim().length() > 0) {
                b.append(n.isClickable() ? "[button] " : n.isEditable() ? "[field] " : "")
                        .append(text.toString().trim().replace('\n', ' ')).append('\n');
                lines++;
            }
            for (int i = 0; i < n.getChildCount(); i++) queue.add(n.getChild(i));
        }
        return b.toString().trim();
    }

    boolean tap(String text) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null || text.isEmpty()) return false;
        List<AccessibilityNodeInfo> found = root.findAccessibilityNodeInfosByText(text);
        for (AccessibilityNodeInfo n : found) {
            if (!n.isVisibleToUser()) continue;
            for (AccessibilityNodeInfo c = n; c != null; c = c.getParent()) {
                if (c.isClickable()) return c.performAction(AccessibilityNodeInfo.ACTION_CLICK);
            }
            // Not clickable itself: tap its centre like a finger would.
            Rect r = new Rect();
            n.getBoundsInScreen(r);
            Path p = new Path();
            p.moveTo(r.exactCenterX(), r.exactCenterY());
            return dispatchGesture(new GestureDescription.Builder()
                    .addStroke(new GestureDescription.StrokeDescription(p, 0, 60)).build(), null, null);
        }
        return false;
    }

    boolean type(String text) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return false;
        AccessibilityNodeInfo field = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (field == null || !field.isEditable()) field = firstEditable(root);
        if (field == null) return false;
        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
        return field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
    }

    boolean scroll(boolean forward) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return false;
        ArrayDeque<AccessibilityNodeInfo> queue = new ArrayDeque<>();
        queue.add(root);
        while (!queue.isEmpty()) {
            AccessibilityNodeInfo n = queue.poll();
            if (n == null) continue;
            if (n.isScrollable() && n.isVisibleToUser()) {
                return n.performAction(forward ? AccessibilityNodeInfo.ACTION_SCROLL_FORWARD : AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD);
            }
            for (int i = 0; i < n.getChildCount(); i++) queue.add(n.getChild(i));
        }
        return false;
    }

    private static AccessibilityNodeInfo firstEditable(AccessibilityNodeInfo root) {
        ArrayDeque<AccessibilityNodeInfo> queue = new ArrayDeque<>();
        queue.add(root);
        while (!queue.isEmpty()) {
            AccessibilityNodeInfo n = queue.poll();
            if (n == null) continue;
            if (n.isEditable() && n.isVisibleToUser()) return n;
            for (int i = 0; i < n.getChildCount(); i++) queue.add(n.getChild(i));
        }
        return null;
    }
}
