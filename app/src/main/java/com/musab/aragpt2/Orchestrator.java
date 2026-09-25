package com.musab.aragpt2;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * The manager model's routing step. Models run one after another (only one is in memory at a
 * time): the manager reads the request and decides who handles it, with a short plan; the coder
 * or assistant does the work; the language model answers the user.
 */
public final class Orchestrator {
    public enum Kind { CODE, PHONE, EXPLAIN, CHAT }

    public static final class Route {
        public final Kind kind;
        public final List<String> plan;
        Route(Kind kind, List<String> plan) { this.kind = kind; this.plan = plan; }

        public AgentProfile agent() {
            switch (kind) {
                case PHONE: return AgentProfile.AUTOMATOR;
                case EXPLAIN: return AgentProfile.EXPLAINER;
                case CHAT: return null;
                default: return plan.size() >= 3 ? AgentProfile.ARCHITECT : AgentProfile.CODER;
            }
        }

        public String planText() {
            StringBuilder b = new StringBuilder();
            for (String p : plan) b.append("- ").append(p).append('\n');
            return b.toString();
        }
    }

    /** The manager may only answer with a route and optional plan lines. */
    public static final String ROUTE_GRAMMAR =
            "root ::= \"ROUTE: \" (\"code\" | \"phone\" | \"explain\" | \"chat\") (\"\\n\" \"PLAN: \" [^\\n]+){0,8}\n";

    public static final String MANAGER_PROMPT =
            "You are the manager of a team on the user's phone: a coder (writes and runs programs in Termux), "
            + "a phone assistant (opens apps, alarms, settings, screen actions), an explainer (answers questions "
            + "about the current project) and a chat model (conversation, knowledge, writing). Decide who handles "
            + "the request. Answer with one line ROUTE: code|phone|explain|chat, then for code up to 8 lines "
            + "PLAN: <file or step>.";

    private Orchestrator() {}

    public static Route parse(String text) {
        Kind kind = Kind.CHAT;
        List<String> plan = new ArrayList<>();
        for (String line : text.split("\n")) {
            String t = line.trim();
            if (t.regionMatches(true, 0, "ROUTE:", 0, 6)) {
                String v = t.substring(6).trim().toLowerCase(Locale.ROOT);
                if (v.startsWith("code")) kind = Kind.CODE;
                else if (v.startsWith("phone")) kind = Kind.PHONE;
                else if (v.startsWith("explain")) kind = Kind.EXPLAIN;
                else kind = Kind.CHAT;
            } else if (t.regionMatches(true, 0, "PLAN:", 0, 5) && t.length() > 5) {
                plan.add(t.substring(5).trim());
            }
        }
        return new Route(kind, plan);
    }

    /** Prompt for the language model's short answer to the user about a finished task. */
    public static String summaryPrompt(String request, String outcome, List<String> files) {
        return "The user asked: " + request + "\nResult: " + outcome
                + (files.isEmpty() ? "" : "\nFiles: " + String.join(", ", files))
                + "\nTell the user in their language, in two or three short sentences, what was done and how to use it.";
    }
}
