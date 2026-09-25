package com.musab.aragpt2;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.FileOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Closed generate → write → validate → run → analyze → patch loop. Termux's exit code is the
 * only thing that decides success; the model's own opinion of its code is never trusted.
 * Runs entirely on the caller's (background) thread.
 */
public final class AgentLoop {
    public enum State { IDLE, GENERATING, WRITING, READY, VALIDATING, RUNNING, CAPTURING_OUTPUT, ANALYZING,
        PATCHING, RETRYING, SUCCESS, FAILED, WAITING_FOR_USER }

    public interface Model {
        GenerationResult generate(List<ChatMessage> turns, TextListener listener) throws Exception;
        void cancel();
        /** Context window in tokens; bounds how much project text goes into a prompt. */
        int contextTokens();
    }

    public interface Listener {
        void onState(State state, String detail);
        void onModelText(String delta);
        void onOutput(boolean stderr, String text);
        void onMetrics(String line);
        /** Blocking: asks the user to approve a risky operation. Called on the loop thread. */
        boolean confirm(String reason);
    }

    public static final class Outcome {
        public final State state;
        public final String message;
        /** Set when the program works but needs a person at the keyboard: run it in a Termux session. */
        public final String interactiveCommand;
        Outcome(State state, String message) { this(state, message, null); }
        Outcome(State state, String message, String interactiveCommand) {
            this.state = state; this.message = message; this.interactiveCommand = interactiveCommand;
        }
    }

    static final String SYSTEM_PROMPT =
            "You are a coding agent. Every file you output is saved and executed automatically in Termux "
            + "(Linux on Android, aarch64), and you are shown the real result.\n"
            + "Output ONLY file blocks, no explanations:\n"
            + "FILE: relative/path.ext\n```lang\ncomplete file content\n```\n"
            + "To change an existing file use a minimal edit instead of rewriting it:\n"
            + "EDIT: relative/path.ext\n```\n<<<<<<< SEARCH\nexact lines copied from the current file\n=======\n"
            + "replacement lines\n>>>>>>> REPLACE\n```\n"
            + "Rules:\n"
            + "- Complete, runnable code. Never placeholders such as \"...\" or \"your code here\".\n"
            + "- Interactive input() is fine when the task needs it. Then also give sample keyboard input so the "
            + "program can be tested automatically, one answer per line, ending with the choice that exits:\n"
            + "STDIN:\n```\n1\n2\n3\n```\n"
            + "- Prefer the standard library; list third-party Python packages in requirements.txt.\n"
            + "- The project runs with the usual command for its language (python main.py, node index.js, "
            + "bash main.sh). For another command add one line: RUN: <command>\n"
            + "- Output only the files you create or change.\n"
            + "- If the request is a question that needs no program, answer in one line: SAY: <answer>";

    /**
     * GBNF grammar for the agent's output: only FILE / EDIT / STDIN / RUN blocks, so the model
     * cannot drift into prose, forget the FILE header, or break the fence structure.
     */
    static final String OUTPUT_GRAMMAR =
            "root ::= [\\n]* item (sep item)* [\\n]?\n"
            + "sep ::= \"\\n\" [\\n]*\n"
            + "item ::= file | edit | stdin | run | tool | say\n"
            + "file ::= \"FILE: \" path \"\\n```\" lang \"\\n\" body \"```\"\n"
            + "edit ::= \"EDIT: \" path \"\\n```\" lang \"\\n\" hunk+ \"```\"\n"
            + "hunk ::= \"<<<<<<< SEARCH\\n\" body \"=======\\n\" body \">>>>>>> REPLACE\\n\"\n"
            + "stdin ::= \"STDIN:\\n```\\n\" body \"```\"\n"
            + "run ::= \"RUN: \" [^\\n`]+\n"
            + "tool ::= \"TOOL: \" [A-Za-z0-9_.-]+ \" {\" [^\\n]* \"}\"\n"
            + "say ::= \"SAY: \" [^\\n]+\n"
            + "path ::= [A-Za-z0-9_] [A-Za-z0-9_./-]*\n"
            + "lang ::= [A-Za-z0-9+#-]*\n"
            + "body ::= line*\n"
            + "line ::= ([^`\\n] [^\\n]* | \"`\" [^`\\n] [^\\n]* | \"``\" [^`\\n] [^\\n]*)? \"\\n\"\n";

    private static final Pattern TRACE_FILE = Pattern.compile("(?:File \"|at |^|[\\s(])([\\w./-]+\\.[A-Za-z]{1,5})(?:\", line |:)(\\d+)", Pattern.MULTILINE);
    private static final Set<String> SHELL_LANGS = new java.util.HashSet<>(java.util.Arrays.asList(
            "", "bash", "sh", "shell", "console", "zsh", "terminal", "text", "txt", "plaintext", "output", "cmd"));

    public int maxAttempts = 6;
    public long execTimeoutMs = 60_000;
    public long installTimeoutMs = 600_000;
    public int repeatLimit = 3;
    public int maxToolRounds = 4;

    private final Model model;
    private final TermuxBridge bridge;
    private final ProjectWorkspace ws;
    private final Listener listener;
    private volatile boolean cancelled;
    private volatile int runningExec = -1;
    private final List<String> signatures = new ArrayList<>();
    private String lastFilesFingerprint = "";
    /** Tool list for the system prompt; fetched while connecting, empty when Termux is unavailable. */
    private volatile String toolsSection = "";
    private int noProgress;

    public AgentLoop(Model model, TermuxBridge bridge, ProjectWorkspace ws, Listener listener) {
        this.model = model; this.bridge = bridge; this.ws = ws; this.listener = listener;
        ws.attach(bridge);
    }

    public void cancel() {
        cancelled = true;
        model.cancel();
        int id = runningExec;
        if (id >= 0) bridge.kill(id);
    }

    /** Starts a new task on the workspace (existing files are kept and can be edited). */
    public Outcome run(String request) {
        ws.request = request;
        ws.stdinInput = null;
        ws.attempt = 0;
        ws.attemptNotes.clear();
        return loop(initialPrompt(), false);
    }

    /** Continues an interrupted task from the last safe point recorded in the journal. */
    public Outcome resume() {
        boolean haveFiles = false;
        for (ProjectWorkspace.Entry e : ws.entries()) {
            if (e.status != ProjectWorkspace.FileStatus.COMPLETE) restoreOrDrop(e);
        }
        for (ProjectWorkspace.Entry e : ws.entries()) haveFiles |= e.status == ProjectWorkspace.FileStatus.COMPLETE;
        // Files that were fully written are trusted and executed again, not regenerated.
        return haveFiles ? loop(null, true) : loop(initialPrompt(), false);
    }

    private void restoreOrDrop(ProjectWorkspace.Entry e) {
        java.io.File backup = new java.io.File(ws.dir, ProjectWorkspace.META + "/history/a" + ws.attempt + "/" + e.path);
        try {
            if (backup.isFile()) {
                ProjectWorkspace.FileWriter w = ws.beginFile(e.path, ws.attempt);
                w.write(new String(ProjectWorkspace.readBytes(backup), java.nio.charset.StandardCharsets.UTF_8));
                w.close(true);
                return;
            }
        } catch (IOException ignored) {
        }
        ws.dropIncomplete();
    }

    private Outcome loop(String prompt, boolean executeFirst) {
        // Connect to Termux while the model is generating, so files stream into Termux live
        // and execution starts without a connection delay.
        Thread warm = new Thread(() -> {
            try {
                bridge.ensureConnected(15_000);
                toolsSection = describeTools(bridge.listTools());
            } catch (IOException ignored) {
            }
        }, "termux-prewarm");
        warm.setDaemon(true);
        warm.start();
        // Tools must be known before the first prompt; don't hold generation up for long.
        try { warm.join(4000); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        String failure = null;
        Set<String> carried = new LinkedHashSet<>();
        int toolRounds = 0;
        while (true) {
            if (cancelled) return finish(State.FAILED, "أُلغي بطلب المستخدم");
            long iterationStart = System.nanoTime();
            JSONObject metrics = new JSONObject();
            Set<String> changed = new LinkedHashSet<>();

            if (!executeFirst) {
                if (ws.attempt >= maxAttempts) {
                    return finish(State.FAILED, "توقف بعد " + maxAttempts + " محاولات. آخر خطأ:\n" + tail(failure, 1200));
                }
                ws.attempt++;
                Generation g = generate(prompt, failure != null, metrics);
                if (cancelled) return finish(State.FAILED, "أُلغي بطلب المستخدم");
                if (g.error != null) return finish(State.FAILED, "خطأ في النموذج: " + g.error);
                changed.addAll(g.changed);
                if (!g.toolCalls.isEmpty() && toolRounds < maxToolRounds) {
                    // Tool rounds gather information; they are not repair attempts.
                    toolRounds++;
                    carried.addAll(g.changed);
                    prompt = prompt + "\n\nYour previous output:\n" + tail(g.result == null ? "" : g.result.text, 1500)
                            + "\n\nTool results:\n" + runTools(g.toolCalls, metrics)
                            + "\nContinue: write files, call another tool, or answer with SAY:.";
                    ws.attempt--;
                    report(metrics);
                    continue;
                }
                changed.addAll(carried);
                carried.clear();
                failure = g.problem();
                if (failure == null && changed.isEmpty() && g.says.length() > 0) {
                    put(metrics, "iteration_ms", ms(iterationStart));
                    report(metrics);
                    return finish(State.SUCCESS, g.says.toString().trim());
                }
            }
            executeFirst = false;

            Exec result = null;
            if (failure == null) {
                try {
                    result = execute(changed, metrics);
                } catch (IOException e) {
                    return finish(State.WAITING_FOR_USER, "تعذر الوصول إلى Termux: " + e.getMessage());
                } catch (NeedsUser e) {
                    return finish(State.WAITING_FOR_USER, e.getMessage());
                }
                if (cancelled) return finish(State.FAILED, "أُلغي بطلب المستخدم");
                if (result.success()) {
                    put(metrics, "iteration_ms", ms(iterationStart));
                    report(metrics);
                    note(result.signature(), changed);
                    return finish(State.SUCCESS, "نجح التنفيذ" + (result.isTest ? " واجتازت الاختبارات" : "")
                            + " (exit 0) في المحاولة " + ws.attempt + ": " + result.command);
                }
                if (result.waitsForInput()) {
                    // Not a bug: the program ran correctly up to the point where it asks the user
                    // for input, which a background run cannot provide. Asking the model to
                    // "fix" that would only remove the interactivity the user asked for.
                    put(metrics, "iteration_ms", ms(iterationStart));
                    report(metrics);
                    note("interactive", changed);
                    Outcome o = finish(State.SUCCESS, "البرنامج يعمل حتى لحظة طلب الإدخال (برنامج تفاعلي): "
                            + result.command + "\nشغّله في Termux لتتفاعل معه.");
                    return new Outcome(o.state, o.message, result.command);
                }
                failure = result.report();
            }

            setState(State.ANALYZING, null);
            long analysisStart = System.nanoTime();
            String signature = result != null ? result.signature() : signatureOf(failure);
            note(signature, changed);
            String stuck = detectStuck(signature, changed);
            prompt = fixPrompt(failure, result);
            put(metrics, "analysis_ms", ms(analysisStart));
            put(metrics, "iteration_ms", ms(iterationStart));
            report(metrics);
            if (stuck != null) return finish(State.WAITING_FOR_USER, stuck + "\n" + tail(failure, 1200));
            setState(State.RETRYING, "المحاولة " + (ws.attempt + 1));
        }
    }

    // ---------------------------------------------------------------- generation

    private final class Generation implements CodeStreamParser.Sink, CodeStreamParser.UnnamedBlockResolver {
        final Set<String> changed = new LinkedHashSet<>();
        final List<String> editErrors = new ArrayList<>();
        final List<String> incomplete = new ArrayList<>();
        final List<String[]> toolCalls = new ArrayList<>();
        final StringBuilder says = new StringBuilder();
        final int attemptNo;
        ProjectWorkspace.FileWriter writer;
        long startNanos, firstWriteNanos = -1, writeNanos, editNanos;
        GenerationResult result;
        String error;
        boolean namedSeen;

        Generation(int attemptNo) { this.attemptNo = attemptNo; }

        @Override public void onFileStart(String path) throws IOException {
            namedSeen = true;
            try {
                writer = ws.beginFile(path, attemptNo);
                setState(State.WRITING, writer.entry.path);
            } catch (IOException e) {
                writer = null;
                editErrors.add(path + ": " + e.getMessage());
            }
        }

        @Override public void onFileData(String text) throws IOException {
            if (writer == null) return;
            if (firstWriteNanos < 0) firstWriteNanos = System.nanoTime();
            writer.write(text);
        }

        @Override public void onFileEnd(boolean complete) throws IOException {
            if (writer == null) return;
            String before = writer.entry.sha;
            writer.close(complete);
            writeNanos += writer.writeNanos;
            if (!complete) incomplete.add(writer.entry.path);
            if (!writer.entry.sha.equals(before)) changed.add(writer.entry.path);
            writer = null;
            setState(State.GENERATING, null);
        }

        @Override public void onEdit(String path, String body, boolean complete) throws IOException {
            namedSeen = true;
            if (!complete) { incomplete.add(path); return; }
            long t = System.nanoTime();
            String before = ws.entry(path) == null ? "" : ws.entry(path).sha;
            String err;
            try { err = ws.applyEdit(path, body, attemptNo); }
            catch (IOException e) { err = path + ": " + e.getMessage(); }
            editNanos += System.nanoTime() - t;
            if (err != null) editErrors.add(err);
            else if (ws.entry(path) != null && !ws.entry(path).sha.equals(before)) changed.add(path);
        }

        @Override public void onRunCommand(String command) {
            ws.runCommandOverride = command;
        }

        @Override public void onStdin(String input) {
            ws.stdinInput = input;
        }

        @Override public void onToolCall(String name, String argsJson) {
            namedSeen = true;
            toolCalls.add(new String[]{name, argsJson});
        }

        @Override public void onSay(String text) {
            if (says.length() > 0) says.append('\n');
            says.append(text);
        }

        @Override public String pathFor(String language) {
            if (namedSeen || SHELL_LANGS.contains(language)) return null;
            String ext = extensionFor(language);
            if (ext == null) return null;
            String only = null;
            int count = 0;
            for (ProjectWorkspace.Entry e : ws.entries()) {
                if (e.path.endsWith("." + ext)) { only = e.path; count++; }
            }
            if (count == 1) return only;
            if (count == 0 && ws.isEmpty()) return ext.equals("java") ? "Main.java" : "main." + ext;
            return null;
        }

        /** Why this generation cannot be executed, or null when it can. */
        String problem() {
            StringBuilder b = new StringBuilder();
            if (!editErrors.isEmpty()) {
                b.append("Your EDIT could not be applied (nothing was changed by it):\n");
                for (String e : editErrors) b.append(e).append('\n');
                b.append("Copy the SEARCH lines exactly from the current file, or output the whole file with FILE.\n");
            }
            if (!incomplete.isEmpty()) {
                b.append("Your output was cut off before these blocks were finished: ").append(incomplete)
                        .append(". Keep files shorter or change them with small EDIT blocks.\n");
            }
            if (b.length() == 0 && changed.isEmpty() && says.length() == 0 && toolCalls.isEmpty()) {
                if (ws.isEmpty()) b.append("No FILE block was produced. Output the program as FILE blocks.\n");
                else b.append("No file was changed, so the same result would repeat. Change the code that causes the problem.\n");
            }
            return b.length() == 0 ? null : b.toString();
        }
    }

    private Generation generate(String prompt, boolean patching, JSONObject metrics) {
        setState(patching ? State.PATCHING : State.GENERATING, "المحاولة " + ws.attempt);
        saveJournal();
        Generation g = new Generation(ws.attempt);
        CodeStreamParser parser = new CodeStreamParser(g, g);
        List<ChatMessage> turns = new ArrayList<>(2);
        turns.add(new ChatMessage(-1, ChatMessage.ROLE_SYSTEM, SYSTEM_PROMPT + toolsSection, 0));
        turns.add(new ChatMessage(-1, ChatMessage.ROLE_USER, prompt, 0));
        g.startNanos = System.nanoTime();
        try {
            g.result = model.generate(turns, delta -> {
                listener.onModelText(delta);
                parser.feed(delta);
            });
            parser.finish(g.result.finishedNaturally());
            if (g.result.stopReason == GenerationResult.STOP_CANCELLED) cancelled = true;
        } catch (Exception e) {
            try { parser.finish(); } catch (IOException ignored) {}
            g.error = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
        }
        GenerationResult r = g.result;
        if (r != null) {
            put(metrics, "time_to_first_token_ms", r.firstTokenMs);
            put(metrics, "generation_ms", r.totalMs);
            put(metrics, "prompt_tokens", r.promptTokens);
            put(metrics, "reused_prompt_tokens", r.reusedPromptTokens);
            put(metrics, "generated_tokens", r.generatedTokens);
            put(metrics, "tokens_per_s", Math.round(r.tokensPerSecond * 10) / 10.0);
            put(metrics, "stop_reason", r.stopReason);
        }
        if (g.firstWriteNanos > 0) put(metrics, "time_to_first_file_write_ms", (g.firstWriteNanos - g.startNanos) / 1_000_000);
        put(metrics, "file_write_ms", g.writeNanos / 1_000_000.0);
        put(metrics, patching ? "patch_apply_ms" : "edit_apply_ms", g.editNanos / 1_000_000.0);
        put(metrics, "files_changed", String.join(",", g.changed));
        log("generation", metrics);
        return g;
    }

    // ---------------------------------------------------------------- execution

    private static final class NeedsUser extends Exception {
        private static final long serialVersionUID = 1L;
        NeedsUser(String message) { super(message); }
    }

    private final class Exec implements TermuxBridge.ExecListener {
        final String command;
        final boolean isTest;
        String stdin;
        final CountDownLatch done = new CountDownLatch(1);
        final Utf8StreamDecoder outDecoder = new Utf8StreamDecoder(), errDecoder = new Utf8StreamDecoder();
        final TailBuffer out = new TailBuffer(4000), err = new TailBuffer(8000);
        FileOutputStream outLog, errLog;
        volatile TermuxBridge.ExecResult result;
        volatile long startLatencyMs = -1, pid;
        String validationErrors;

        Exec(String command, boolean isTest) { this.command = command; this.isTest = isTest; }

        @Override public void onStarted(long pid, long latencyMs) {
            this.pid = pid;
            startLatencyMs = latencyMs;
            setState(State.CAPTURING_OUTPUT, command);
        }

        @Override public void onOutput(boolean stderr, byte[] data) {
            try { (stderr ? errLog : outLog).write(data); } catch (IOException | NullPointerException ignored) {}
            String text = (stderr ? errDecoder : outDecoder).decode(data, 0, data.length);
            (stderr ? err : out).append(text);
            listener.onOutput(stderr, text);
        }

        @Override public void onExit(TermuxBridge.ExecResult r) {
            result = r;
            done.countDown();
        }

        /**
         * The run ended only because nobody typed anything: Python's input() hit end of input,
         * or the program sat at a prompt until the timeout.
         */
        boolean waitsForInput() {
            if (isTest || validationErrors != null || result == null || result.disconnected) return false;
            String e = err.toString();
            if (e.contains("EOFError") && (e.contains("input(") || e.contains("EOF when reading a line"))) {
                // Only when EOFError is the whole failure, not a later traceback.
                String last = e.trim().substring(e.trim().lastIndexOf('\n') + 1);
                return last.startsWith("EOFError");
            }
            if (result.timedOut && e.trim().isEmpty()) {
                String o = out.toString();
                String t = o.trim();
                return !o.endsWith("\n") && !t.isEmpty() && ":?>".indexOf(t.charAt(t.length() - 1)) >= 0;
            }
            return false;
        }

        boolean success() {
            return validationErrors == null && result != null && result.exitCode == 0 && !result.timedOut
                    && !result.disconnected && result.error == null;
        }

        String signature() {
            if (validationErrors != null) return signatureOf(validationErrors);
            if (result != null && result.timedOut) return "timeout";
            String e = err.toString().trim();
            return signatureOf(e.isEmpty() ? "exit " + (result == null ? -1 : result.exitCode) + " " + out : e);
        }

        String report() {
            StringBuilder b = new StringBuilder();
            if (validationErrors != null) {
                b.append("Syntax check failed before running:\n").append(validationErrors);
                return b.toString();
            }
            b.append("Command: ").append(command).append('\n');
            if (stdin != null) b.append("Keyboard input given (STDIN):\n").append(tail(stdin, 400)).append('\n');
            if (result.timedOut) b.append("It did not finish within ").append(execTimeoutMs / 1000)
                    .append("s and was stopped (it may wait for input or loop forever).\n");
            else if (result.error != null) b.append("Execution error: ").append(result.error).append('\n');
            else b.append("Exit code: ").append(result.exitCode).append('\n');
            String e = err.toString().trim(), o = out.toString().trim();
            if (!e.isEmpty()) b.append("stderr:\n").append(tail(e, 2500)).append('\n');
            if (!o.isEmpty()) b.append("stdout:\n").append(tail(o, 900)).append('\n');
            return b.toString();
        }
    }

    private Exec execute(Set<String> changed, JSONObject metrics) throws IOException, NeedsUser {
        setState(State.READY, null);
        bridge.ensureConnected(15_000);
        ws.syncRemote();

        Map<String, String> files = ws.completeShas();
        if (files.isEmpty()) throw new NeedsUser("لا توجد ملفات مكتملة لتشغيلها");
        CommandPlanner.Plan plan = CommandPlanner.plan(files.keySet(), this::readQuietly, ws.runCommandOverride);
        if (plan.command == null) throw new NeedsUser("تعذر تحديد أمر التشغيل للمشروع؛ اطلب من النموذج سطر RUN: أو ملف main");

        // Ordinary project execution runs without asking; destructive/system operations do not.
        List<String> risky = new ArrayList<>();
        addRisk(risky, SafetyPolicy.check(plan.command));
        for (String f : files.keySet()) {
            if (f.endsWith(".sh") || changed.contains(f)) addRisk(risky, SafetyPolicy.check(readQuietly(f)));
        }
        if (!risky.isEmpty()) {
            setState(State.WAITING_FOR_USER, risky.get(0));
            if (!listener.confirm(String.join("\n", risky))) throw new NeedsUser("رفض المستخدم تنفيذ عملية خطرة:\n" + risky.get(0));
        }

        setState(State.VALIDATING, null);
        long vt = System.nanoTime();
        List<String> toCheck = new ArrayList<>();
        for (String f : files.keySet()) if (f.endsWith(".py") || f.endsWith(".json")) toCheck.add(f);
        JSONObject v = bridge.validate(ws.id, toCheck, plan.requiredCommands);
        put(metrics, "validation_ms", ms(vt));
        if (v.optJSONArray("missing_commands") != null && v.optJSONArray("missing_commands").length() > 0) {
            throw new NeedsUser("أوامر غير مثبتة في Termux: " + v.optJSONArray("missing_commands").toString()
                    + "\nثبّتها مرة واحدة في Termux (مثل pkg install clang nodejs) ثم أعد المحاولة");
        }
        Exec exec = new Exec(plan.command, plan.isTest);
        if (v.optJSONArray("errors") != null && v.optJSONArray("errors").length() > 0) {
            StringBuilder b = new StringBuilder();
            org.json.JSONArray errs = v.optJSONArray("errors");
            for (int i = 0; i < errs.length(); i++) {
                JSONObject e = errs.optJSONObject(i);
                b.append(e.optString("path")).append(':').append(e.optInt("line")).append(": ")
                        .append(e.optString("msg")).append('\n');
                if (!e.optString("text").isEmpty()) b.append("    ").append(e.optString("text")).append('\n');
            }
            exec.validationErrors = b.toString();
            listener.onOutput(true, exec.validationErrors);
            return exec;
        }

        if (plan.install != null) {
            String key = plan.installKey + ":" + files.get(plan.installKey);
            if (!key.equals(ws.installedRequirementsSha)) {
                Exec install = runCommand(plan.install, false, installTimeoutMs, metrics, "install");
                if (!install.success()) return install;
                ws.installedRequirementsSha = key;
                saveJournal();
            }
        }
        String stdin = plan.isTest ? null : ws.stdinInput;
        Exec run = runCommand(plan.command, plan.isTest, stdin, execTimeoutMs, metrics, "exec");
        // A missing third-party module is an environment problem, not a code bug: install it
        // and run again without spending a model attempt on it.
        Set<String> tried = new java.util.HashSet<>();
        while (!run.success() && !cancelled) {
            String install = MissingModule.installCommand(run.err.toString(), files.keySet());
            if (install == null || !tried.add(install) || tried.size() > 4) break;
            Exec inst = runCommand(install, false, installTimeoutMs, metrics, "autoinstall");
            if (!inst.success()) return inst;
            run = runCommand(plan.command, plan.isTest, stdin, execTimeoutMs, metrics, "exec");
        }
        return run;
    }

    private Exec runCommand(String command, boolean isTest, long timeoutMs, JSONObject metrics, String label) throws IOException {
        return runCommand(command, isTest, null, timeoutMs, metrics, label);
    }

    private Exec runCommand(String command, boolean isTest, String stdin, long timeoutMs, JSONObject metrics,
                            String label) throws IOException {
        setState(State.RUNNING, command);
        Exec exec = new Exec(command, isTest);
        exec.stdin = stdin;
        exec.outLog = new FileOutputStream(ws.runLog(ws.attempt, label + ".stdout"));
        exec.errLog = new FileOutputStream(ws.runLog(ws.attempt, label + ".stderr"));
        try {
            for (int tries = 0; ; tries++) {
                runningExec = bridge.exec(ws.id, command, null, stdin, timeoutMs, exec);
                try {
                    exec.done.await(timeoutMs + 30_000, TimeUnit.MILLISECONDS);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    throw new IOException("interrupted");
                }
                runningExec = -1;
                TermuxBridge.ExecResult r = exec.result;
                if (r == null) throw new IOException("لم يرد Termux على انتهاء التنفيذ");
                if (!r.disconnected || tries > 0 || cancelled) break;
                // Termux dropped the connection mid-run: reconnect, resync and rerun once.
                bridge.ensureConnected(15_000);
                ws.syncRemote();
                exec = new Exec(command, isTest);
                exec.stdin = stdin;
                exec.outLog = new FileOutputStream(ws.runLog(ws.attempt, label + ".stdout"));
                exec.errLog = new FileOutputStream(ws.runLog(ws.attempt, label + ".stderr"));
            }
        } finally {
            closeQuietly(exec.outLog);
            closeQuietly(exec.errLog);
        }
        listener.onOutput(false, exec.outDecoder.flush());
        listener.onOutput(true, exec.errDecoder.flush());
        TermuxBridge.ExecResult r = exec.result;
        put(metrics, label + "_termux_start_latency_ms", exec.startLatencyMs);
        put(metrics, label + "_execution_ms", r.durationMs);
        put(metrics, label + "_exit_code", r.exitCode);
        JSONObject rec = new JSONObject();
        put(rec, "command", command);
        put(rec, "cwd", "~/newal/projects/" + ws.id);
        put(rec, "pid", exec.pid);
        put(rec, "exit_code", r.exitCode);
        put(rec, "signal", r.signal);
        put(rec, "timed_out", r.timedOut);
        put(rec, "duration_ms", r.durationMs);
        put(rec, "stderr_tail", tail(exec.err.toString(), 2000));
        put(rec, "stdout_tail", tail(exec.out.toString(), 1000));
        log(label, rec);
        return exec;
    }

    // ---------------------------------------------------------------- prompts

    private String initialPrompt() {
        StringBuilder b = new StringBuilder("Task: ").append(ws.request).append('\n');
        if (!ws.isEmpty()) {
            b.append("\nThe project already contains these files; change only what the task needs:\n");
            appendFiles(b, allPaths(), promptBudgetChars() - b.length());
        }
        return b.toString();
    }

    private String fixPrompt(String failure, Exec result) {
        StringBuilder b = new StringBuilder("Task: ").append(ws.request).append("\n\n");
        // Files named in the error first, then recently changed files, then the rest if room remains.
        LinkedHashSet<String> order = new LinkedHashSet<>();
        Matcher m = TRACE_FILE.matcher(failure);
        Set<String> known = ws.completeShas().keySet();
        while (m.find()) {
            String p = m.group(1);
            for (String k : known) if (k.equals(p) || p.endsWith("/" + k)) order.add(k);
        }
        for (ProjectWorkspace.Entry e : ws.entries()) if (e.changedInAttempt == ws.attempt) order.add(e.path);
        order.addAll(known);
        StringBuilder tailPart = new StringBuilder();
        if (ws.attemptNotes.size() > 1) {
            tailPart.append("Previous attempts:\n");
            for (String n : ws.attemptNotes.subList(0, ws.attemptNotes.size() - 1)) tailPart.append("- ").append(n).append('\n');
            tailPart.append('\n');
        }
        tailPart.append("Attempt ").append(ws.attempt).append(" failed.\n").append(failure.trim()).append("\n\n")
                .append("Fix the cause with the smallest change. Output only EDIT or FILE blocks.");
        b.append("Current files:\n");
        appendFiles(b, new ArrayList<>(order), promptBudgetChars() - b.length() - tailPart.length());
        b.append('\n').append(tailPart);
        return b.toString();
    }

    private void appendFiles(StringBuilder b, List<String> paths, int budget) {
        List<String> omitted = new ArrayList<>();
        for (String p : paths) {
            String content = readQuietly(p);
            int cost = content.length() + p.length() + 24;
            if (cost > budget) { omitted.add(p); continue; }
            b.append("FILE: ").append(p).append("\n```").append(languageFor(p)).append('\n').append(content);
            if (!content.endsWith("\n")) b.append('\n');
            b.append("```\n");
            budget -= cost;
        }
        if (!omitted.isEmpty()) b.append("(Not shown, unchanged: ").append(String.join(", ", omitted)).append(")\n");
    }

    /** Characters of project text that fit in half the context (the rest is left for the answer). */
    private int promptBudgetChars() {
        return Math.max(1500, model.contextTokens() * 3 / 2) - SYSTEM_PROMPT.length();
    }

    // ---------------------------------------------------------------- tools

    static String describeTools(org.json.JSONArray tools) {
        if (tools == null || tools.length() == 0) return "";
        StringBuilder b = new StringBuilder("\nTools on this phone. Call one per line and you get the result back:\n"
                + "TOOL: name {\"arg\": value}\n");
        for (int i = 0; i < tools.length() && i < 40; i++) {
            JSONObject t = tools.optJSONObject(i);
            if (t == null) continue;
            JSONObject params = t.optJSONObject("parameters");
            b.append("- ").append(t.optString("name")).append(' ').append(params == null ? "{}" : params.toString())
                    .append(": ").append(tail(t.optString("description").replace('\n', ' '), 160)).append('\n');
        }
        return b.toString();
    }

    private String runTools(List<String[]> calls, JSONObject metrics) {
        StringBuilder b = new StringBuilder();
        long t = System.nanoTime();
        for (String[] c : calls) {
            if (cancelled) break;
            setState(State.RUNNING, "TOOL " + c[0]);
            String output;
            boolean ok;
            try {
                bridge.ensureConnected(15_000);
                JSONObject args;
                try { args = new JSONObject(c[1]); } catch (JSONException e) { args = new JSONObject(); }
                JSONObject r = bridge.callTool(c[0], args);
                ok = r.optBoolean("ok");
                output = r.optString("output");
            } catch (IOException e) {
                ok = false;
                output = e.getMessage();
            }
            String line = c[0] + (ok ? " -> " : " FAILED -> ") + tail(output, 1500);
            listener.onOutput(!ok, "🔧 " + line + "\n");
            b.append(line).append('\n');
            JSONObject rec = new JSONObject();
            put(rec, "tool", c[0]);
            put(rec, "args", c[1]);
            put(rec, "ok", ok);
            put(rec, "output", tail(output, 1000));
            log("tool", rec);
        }
        put(metrics, "tools_ms", ms(t));
        return b.toString();
    }

    // ---------------------------------------------------------------- analysis

    /** Normalised error identity: the last meaningful error line without numbers or addresses. */
    static String signatureOf(String text) {
        if (text == null) return "";
        String[] lines = text.trim().split("\n");
        String pick = lines.length == 0 ? "" : lines[lines.length - 1];
        for (int i = lines.length - 1; i >= 0; i--) {
            String l = lines[i].trim();
            if (l.matches("^[\\w.]*(Error|Exception|error|FAILED|failed)\\b.*") || l.contains("Error:")) { pick = l; break; }
        }
        return pick.replaceAll("0x[0-9a-fA-F]+", "0x").replaceAll("\\d+", "N").trim().toLowerCase(Locale.ROOT);
    }

    private void note(String signature, Set<String> changed) {
        ws.attemptNotes.add("attempt " + ws.attempt + ": " + (signature.isEmpty() ? "ok" : signature)
                + (changed.isEmpty() ? " (no file changed)" : " after changing " + String.join(", ", changed)));
        saveJournal();
    }

    /** Stops the loop when it keeps failing the same way without making progress. */
    private String detectStuck(String signature, Set<String> changed) {
        signatures.add(signature);
        int n = signatures.size();
        if (n >= repeatLimit) {
            boolean same = true;
            for (int i = n - repeatLimit; i < n; i++) same &= signatures.get(i).equals(signature);
            if (same) return "نفس الخطأ تكرر " + repeatLimit + " مرات دون تقدم؛ يلزم تدخلك:";
        }
        String fingerprint = ws.completeShas().toString();
        if (changed.isEmpty() || fingerprint.equals(lastFilesFingerprint)) noProgress++;
        else noProgress = 0;
        lastFilesFingerprint = fingerprint;
        if (noProgress >= 2) return "النموذج لم يغيّر الكود في محاولتين متتاليتين؛ يلزم تدخلك:";
        return null;
    }

    // ---------------------------------------------------------------- helpers

    private Outcome finish(State state, String message) {
        setState(state, message);
        saveJournal();
        JSONObject rec = new JSONObject();
        put(rec, "final_state", state.name());
        put(rec, "attempts", ws.attempt);
        put(rec, "message", message);
        log("final", rec);
        return new Outcome(state, message);
    }

    private void setState(State s, String detail) {
        ws.state = s.name();
        listener.onState(s, detail);
    }

    private void saveJournal() {
        try { ws.saveJournal(); } catch (IOException ignored) {}
    }

    private void report(JSONObject metrics) {
        put(metrics, "attempt", ws.attempt);
        log("metrics", metrics);
        listener.onMetrics(metrics.toString());
    }

    private void log(String event, JSONObject record) {
        JSONObject r = new JSONObject();
        try {
            r.put("event", event).put("attempt", ws.attempt);
            for (java.util.Iterator<String> it = record.keys(); it.hasNext(); ) {
                String k = it.next();
                r.put(k, record.get(k));
            }
        } catch (JSONException ignored) {
        }
        ws.log(r);
    }

    private List<String> allPaths() { return new ArrayList<>(ws.completeShas().keySet()); }

    private String readQuietly(String path) {
        try { return ws.read(path); } catch (IOException e) { return ""; }
    }

    private static void addRisk(List<String> risky, String reason) { if (reason != null) risky.add(reason); }

    private static void put(JSONObject o, String k, Object v) {
        try { o.put(k, v); } catch (JSONException ignored) {}
    }

    private static long ms(long startNanos) { return (System.nanoTime() - startNanos) / 1_000_000; }

    private static void closeQuietly(java.io.Closeable c) {
        if (c != null) try { c.close(); } catch (IOException ignored) {}
    }

    static String tail(String s, int max) {
        if (s == null) return "";
        if (s.length() <= max) return s;
        int cut = s.indexOf('\n', s.length() - max);
        return "…\n" + s.substring(cut < 0 ? s.length() - max : cut + 1);
    }

    static String extensionFor(String language) {
        switch (language) {
            case "python": case "py": case "python3": return "py";
            case "javascript": case "js": case "node": case "nodejs": return "js";
            case "c": return "c";
            case "cpp": case "c++": case "cxx": return "cpp";
            case "go": case "golang": return "go";
            case "rust": case "rs": return "rs";
            case "java": return "java";
            default: return null;
        }
    }

    static String languageFor(String path) {
        String p = path.toLowerCase(Locale.ROOT);
        if (p.endsWith(".py")) return "python";
        if (p.endsWith(".js")) return "javascript";
        if (p.endsWith(".sh")) return "bash";
        if (p.endsWith(".json")) return "json";
        if (p.endsWith(".c")) return "c";
        if (p.endsWith(".cpp")) return "cpp";
        if (p.endsWith(".go")) return "go";
        if (p.endsWith(".rs")) return "rust";
        if (p.endsWith(".java")) return "java";
        return "";
    }

    /** Keeps only the last {@code max} characters of a stream, so memory stays bounded. */
    static final class TailBuffer {
        private final int max;
        private final StringBuilder b = new StringBuilder();
        TailBuffer(int max) { this.max = max; }
        synchronized void append(String s) {
            b.append(s);
            if (b.length() > max * 2) b.delete(0, b.length() - max);
        }
        @Override public synchronized String toString() {
            return b.length() > max ? b.substring(b.length() - max) : b.toString();
        }
    }
}
