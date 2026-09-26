package com.musab.aragpt2;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Screen agent: look at the screen, let the model pick one action, do it, look again, until the
 * goal is done. The same observe-act loop that phone agents such as AppAgent and AndroidWorld's
 * M3A use, with the screen given as text (the accessibility tree) so a small local model can run it.
 */
final class PhoneAgent {
    /** The phone: reads the screen and performs actions. */
    interface Device {
        ScreenState observe();
        /** Performs one action and returns what happened ("ok", "no such element" ...). */
        String perform(PhoneAction action, ScreenState on) throws Exception;
    }

    interface Model {
        /** Returns the model's reply for this prompt, constrained by the GBNF grammar. */
        String next(List<ChatMessage> turns, String grammar) throws Exception;
    }

    interface Listener {
        void onStep(int step, PhoneAction action, String reason, String result);
        /** Asks the user before an action that sends, pays, calls or deletes. */
        boolean confirm(String what);
    }

    static final class Result {
        final boolean done;
        final String message;
        final int steps;
        Result(boolean done, String message, int steps) { this.done = done; this.message = message; this.steps = steps; }
    }

    /**
     * Small models follow a worked example much better than a list of rules (measured on Qwen3.5
     * 0.8B / 2B: without the example they confused tap with long_press and echoed element lines).
     */
    static final String SYSTEM_PROMPT =
            "You control an Android phone to reach the user's goal, one action per turn.\n"
            + "Actions:\n"
            + "open_app(\"WhatsApp\") - start an app by its name\n"
            + "tap(5) - tap element [5]\n"
            + "type(7, \"hello\") - write text into field [7]\n"
            + "enter() - press enter / search on the keyboard\n"
            + "scroll(down) or scroll(up) - see more of a list\n"
            + "back() / home() / wait()\n"
            + "done(\"...\") - the goal is reached, or the answer to the user's question\n"
            + "ask(\"...\") - you need something only the user knows\n"
            + "\n"
            + "Example. Goal: send \"hi\" to Sara on WhatsApp\n"
            + "Screen: [1] item \"Sara, see you\" [2] button \"New chat\" -> tap(1)\n"
            + "Screen: [4] field \"Message\" (empty) [5] button \"Voice message\" -> type(4, \"hi\")\n"
            + "Screen: [4] field containing \"hi\" [5] button \"Send\" -> tap(5)\n"
            + "Screen: [3] text \"hi ✓\" [4] field \"Message\" (empty) -> done(\"Sent to Sara\")\n"
            + "\n"
            + "Rules:\n"
            + "- First check the current screen: if the goal is already reached, reply done.\n"
            + "- Use only numbers of elements on the current screen.\n"
            + "- A switch shows on or off; tap it to change it.\n"
            + "- If the goal asks for information that is on the screen, reply done(\"the answer\").\n"
            + "- Never repeat a step that already worked. If the screen did not change, try another element.\n"
            + "- Write done / ask text in the user's language.";

    static final int MAX_STEPS = 20;
    private static final int HISTORY = 8;
    private static final Pattern RISKY = Pattern.compile(
            "send|إرسال|ارسال|call|اتصال|pay|دفع|buy|شراء|purchase|delete|حذف|remove|uninstall|إلغاء التثبيت|transfer|تحويل|post|نشر",
            Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);

    private final Device device;
    private final Model model;
    private final Listener listener;
    private volatile boolean cancelled;
    /** Ask before taps that send, pay, call or delete. */
    boolean confirmRisky = true;

    PhoneAgent(Device device, Model model, Listener listener) {
        this.device = device; this.model = model; this.listener = listener;
    }

    void cancel() { cancelled = true; }

    Result run(String goal) throws Exception {
        List<String> history = new ArrayList<>();
        String lastSignature = null, lastAction = null;
        int repeats = 0, noEffect = 0;
        for (int step = 1; step <= MAX_STEPS; step++) {
            if (cancelled) return new Result(false, "أوقفت المهمة", step - 1);
            ScreenState screen = device.observe();
            List<ChatMessage> turns = new ArrayList<>();
            turns.add(new ChatMessage(0, ChatMessage.ROLE_SYSTEM, SYSTEM_PROMPT, 0));
            turns.add(new ChatMessage(1, ChatMessage.ROLE_USER, prompt(goal, history, screen), 0));
            String reply = model.next(turns, PhoneAction.GRAMMAR);
            if (cancelled) return new Result(false, "أوقفت المهمة", step - 1);
            PhoneAction action = PhoneAction.parse(reply);
            String reason = PhoneAction.reason(reply);
            if (action == null) {
                history.add(step + ". (invalid reply) → reply with exactly one action");
                listener.onStep(step, null, reason, "invalid reply");
                continue;
            }
            if (action.finishes()) {
                listener.onStep(step, action, reason, "");
                return new Result(action.kind == PhoneAction.Kind.DONE, action.text, step);
            }

            // The same action on an unchanged screen again and again: the model is stuck.
            String sig = screen.signature();
            if (action.toString().equals(lastAction) && sig.equals(lastSignature)) {
                if (++repeats >= 2) return new Result(false, "توقفت: الخطوة نفسها تتكرر دون تقدم (" + action + ")", step);
            } else repeats = 0;
            lastAction = action.toString();
            lastSignature = sig;

            String result;
            String risky = riskyTarget(action, screen);
            if (risky != null && confirmRisky && !listener.confirm(risky)) {
                result = "the user declined this action";
                history.add(step + ". " + action + " → " + result);
                listener.onStep(step, action, reason, result);
                return new Result(false, "لم يُنفَّذ: رفضت «" + risky + "»", step);
            }
            try {
                result = device.perform(action, screen);
            } catch (Exception e) {
                result = "failed: " + e.getMessage();
            }
            if (action.kind != PhoneAction.Kind.WAIT && !result.startsWith("failed")) {
                ScreenState after = device.observe();
                boolean changed = !after.signature().equals(sig);
                if (!changed) noEffect++; else noEffect = 0;
                result += changed ? " (screen changed)" : " (screen did not change)";
                if (noEffect >= 4) {
                    listener.onStep(step, action, reason, result);
                    return new Result(false, "توقفت: لا شيء يتغير على الشاشة", step);
                }
            }
            history.add(step + ". " + action + " → " + result);
            listener.onStep(step, action, reason, result);
        }
        return new Result(false, "توقفت بعد " + MAX_STEPS + " خطوة دون إكمال المهمة", MAX_STEPS);
    }

    static String prompt(String goal, List<String> history, ScreenState screen) {
        StringBuilder b = new StringBuilder("Goal: ").append(goal).append('\n');
        if (!history.isEmpty()) {
            b.append("Steps so far:\n");
            for (String h : history.subList(Math.max(0, history.size() - HISTORY), history.size())) b.append(h).append('\n');
        }
        b.append("Current screen:\n").append(screen.render()).append("Next action:");
        return b.toString();
    }

    /** What the action would do when it sends, pays, calls or deletes; otherwise null. */
    static String riskyTarget(PhoneAction action, ScreenState screen) {
        if (action.kind != PhoneAction.Kind.TAP && action.kind != PhoneAction.Kind.ENTER) return null;
        if (action.kind == PhoneAction.Kind.ENTER) return null;
        ScreenState.Element e = screen.byId(action.target);
        if (e == null || e.editable()) return null;
        String text = (e.label + " " + e.viewId).toLowerCase(Locale.ROOT);
        return RISKY.matcher(text).find() ? "الضغط على «" + (e.label.isEmpty() ? e.viewId : e.label) + "» في " + screen.appName : null;
    }
}
