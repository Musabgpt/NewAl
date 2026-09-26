package com.musab.aragpt2;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** One step of the screen agent, e.g. {@code tap(4)} or {@code type(2, "hello")}. */
final class PhoneAction {
    enum Kind { OPEN_APP, TAP, LONG_PRESS, TYPE, ENTER, SCROLL, BACK, HOME, WAIT, DONE, ASK }

    final Kind kind;
    /** Element number for tap / long_press / type, else 0. */
    final int target;
    /** App name, typed text, scroll direction, or the message for done / ask. */
    final String text;

    PhoneAction(Kind kind, int target, String text) {
        this.kind = kind; this.target = target; this.text = text == null ? "" : text;
    }

    boolean finishes() { return kind == Kind.DONE || kind == Kind.ASK; }

    /**
     * GBNF for the model's reply: an optional short reason, then exactly one action. The reason
     * is capped so the model spends its tokens on acting.
     */
    static final String GRAMMAR =
            "root ::= (\"Reason: \" [^\\n]{1,90} \"\\n\")? action\n"
            + "action ::= \"open_app(\" str \")\" | \"tap(\" num \")\""
            + " | \"type(\" num \", \" str \")\" | \"enter()\" | \"scroll(\" dir \")\" | \"back()\" | \"home()\""
            + " | \"wait()\" | \"done(\" str \")\" | \"ask(\" str \")\"\n"
            + "str ::= \"\\\"\" ([^\"\\\\\\n] | \"\\\\\" [\"\\\\n])* \"\\\"\"\n"
            + "num ::= [1-9] [0-9]? [0-9]?\n"
            + "dir ::= \"up\" | \"down\" | \"left\" | \"right\"\n";

    private static final Pattern ACTION = Pattern.compile(
            "(open_app|tap|long_press|type|enter|scroll|back|home|wait|done|ask)\\(\\s*(\\d+)?\\s*,?\\s*(\"((?:[^\"\\\\]|\\\\.)*)\"|up|down|left|right)?\\s*\\)");

    /** Parses the last action in a reply, or returns null. */
    static PhoneAction parse(String reply) {
        if (reply == null) return null;
        Matcher m = ACTION.matcher(reply);
        PhoneAction last = null;
        while (m.find()) {
            int target = m.group(2) == null ? 0 : Integer.parseInt(m.group(2));
            String arg = m.group(4) != null ? unescape(m.group(4)) : m.group(3) == null ? "" : m.group(3);
            Kind k = Kind.valueOf(m.group(1).toUpperCase(java.util.Locale.ROOT));
            boolean needsTarget = k == Kind.TAP || k == Kind.LONG_PRESS || k == Kind.TYPE;
            if (needsTarget && target == 0) continue;
            if (k == Kind.SCROLL && arg.isEmpty()) arg = "down";
            last = new PhoneAction(k, target, arg);
        }
        return last;
    }

    /** The reason line the model gave, or "". */
    static String reason(String reply) {
        if (reply == null) return "";
        Matcher m = Pattern.compile("Reason: ([^\\n]*)").matcher(reply);
        return m.find() ? m.group(1).trim() : "";
    }

    private static String unescape(String s) {
        return s.replace("\\n", "\n").replace("\\\"", "\"").replace("\\\\", "\\");
    }

    private static String quote(String s) {
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n") + "\"";
    }

    @Override public String toString() {
        String name = kind.name().toLowerCase(java.util.Locale.ROOT);
        switch (kind) {
            case TAP: case LONG_PRESS: return name + "(" + target + ")";
            case TYPE: return name + "(" + target + ", " + quote(text) + ")";
            case SCROLL: return name + "(" + text + ")";
            case OPEN_APP: case DONE: case ASK: return name + "(" + quote(text) + ")";
            default: return name + "()";
        }
    }
}
