package com.musab.aragpt2;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * What is on the phone's screen, as a short numbered list the model can act on
 * ("[3] button \"Send\""). Built from the accessibility tree by {@link ScreenControlService}.
 */
final class ScreenState {
    static final class Element {
        final int id;
        /** button, item, field, switch, checkbox, tab or text. */
        final String role;
        final String label;
        final boolean focused;
        /** null when the element cannot be checked. */
        final Boolean checked;
        /** Short resource id (e.g. "send"), or "". */
        final String viewId;
        /** A text field showing only its hint (label is the hint). */
        final boolean empty;

        Element(int id, String role, String label, boolean focused, Boolean checked, String viewId) {
            this(id, role, label, focused, checked, viewId, false);
        }

        Element(int id, String role, String label, boolean focused, Boolean checked, String viewId, boolean empty) {
            this.id = id; this.role = role; this.label = label == null ? "" : label;
            this.focused = focused; this.checked = checked; this.viewId = viewId == null ? "" : viewId;
            this.empty = empty;
        }

        boolean editable() { return "field".equals(role); }

        String render() {
            StringBuilder b = new StringBuilder().append('[').append(id).append("] ").append(role);
            // Small models cannot tell a hint from typed text, so say which one it is.
            if (editable() && empty) b.append(label.isEmpty() ? " (empty)" : " \"" + label + "\" (empty)");
            else if (editable() && !label.isEmpty()) b.append(" containing \"").append(label).append('"');
            else if (!label.isEmpty()) b.append(" \"").append(label).append('"');
            else if (!viewId.isEmpty()) b.append(" (").append(viewId).append(')');
            if (checked != null) b.append(checked ? " on" : " off");
            if (focused) b.append(" focused");
            return b.toString();
        }
    }

    final String packageName, appName;
    final List<Element> elements;
    final boolean scrollable;

    ScreenState(String packageName, String appName, List<Element> elements, boolean scrollable) {
        this.packageName = packageName == null ? "" : packageName;
        this.appName = appName == null ? "" : appName;
        this.elements = Collections.unmodifiableList(new ArrayList<>(elements));
        this.scrollable = scrollable;
    }

    static ScreenState unavailable(String why) {
        return new ScreenState("", why, Collections.emptyList(), false);
    }

    Element byId(int id) {
        for (Element e : elements) if (e.id == id) return e;
        return null;
    }

    /** The prompt text for this screen. */
    String render() {
        StringBuilder b = new StringBuilder("App: ").append(appName.isEmpty() ? packageName : appName);
        if (!packageName.isEmpty() && !packageName.equals(appName)) b.append(" (").append(packageName).append(')');
        b.append('\n');
        if (elements.isEmpty()) b.append("(nothing readable on screen)\n");
        for (Element e : elements) b.append(e.render()).append('\n');
        if (scrollable) b.append("(more content: scroll to see it)\n");
        return b.toString();
    }

    /** Changes whenever the visible content changes; used to tell whether an action did anything. */
    String signature() {
        StringBuilder b = new StringBuilder(packageName);
        for (Element e : elements) b.append('|').append(e.role).append(':').append(e.label).append(e.checked).append(e.focused);
        return Integer.toHexString(b.toString().hashCode()) + ":" + elements.size();
    }
}
