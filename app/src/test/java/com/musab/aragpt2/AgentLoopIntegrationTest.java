package com.musab.aragpt2;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assume.assumeTrue;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import org.junit.After;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

/**
 * Runs the real newal_agent.py (the code that runs inside Termux) with python3 and drives the
 * whole loop with a scripted model: stream → write → validate → execute → analyze → patch.
 */
public class AgentLoopIntegrationTest {
    @Rule public TemporaryFolder tmp = new TemporaryFolder();

    private final List<Process> agents = Collections.synchronizedList(new ArrayList<>());
    private File agentHome;
    private TermuxBridge bridge;
    private int launches;

    @Before public void setUp() throws Exception {
        assumeTrue("python3 is required", new ProcessBuilder("python3", "--version").start().waitFor() == 0);
        agentHome = tmp.newFolder("termux-home");
        String source = new String(Files.readAllBytes(new File("src/main/assets/newal_agent.py").toPath()), StandardCharsets.UTF_8);
        int port = 40000 + (int) (Math.random() * 20000);
        bridge = new TermuxBridge("127.0.0.1", port, "secret-token", source, script -> {
            launches++;
            ProcessBuilder pb = new ProcessBuilder("python3", "-c", script, "newal-agent");
            pb.environment().put("NEWAL_HOME", agentHome.getAbsolutePath());
            pb.redirectErrorStream(true).redirectOutput(new File(agentHome, "stdout.log"));
            agents.add(pb.start());
        });
    }

    @After public void tearDown() {
        if (bridge != null) bridge.close();
        for (Process p : agents) p.destroyForcibly();
    }

    /** Streams scripted replies in small chunks, like a real model. */
    static final class ScriptedModel implements AgentLoop.Model {
        final List<String> replies;
        final List<String> prompts = new ArrayList<>();
        int calls;
        ScriptedModel(String... replies) { this.replies = List.of(replies); }

        @Override public GenerationResult generate(List<ChatMessage> turns, TextListener listener) throws Exception {
            prompts.add(turns.get(turns.size() - 1).text);
            String reply = replies.get(Math.min(calls++, replies.size() - 1));
            for (int i = 0; i < reply.length(); i += 3) listener.onText(reply.substring(i, Math.min(reply.length(), i + 3)));
            return new GenerationResult(reply, 100, reply.length() / 3, 5, 30.0, 50, GenerationResult.STOP_EOG, 0);
        }
        @Override public void cancel() {}
        @Override public int contextTokens() { return 4096; }
    }

    static final class Recorder implements AgentLoop.Listener {
        final List<AgentLoop.State> states = new ArrayList<>();
        final StringBuilder stdout = new StringBuilder(), stderr = new StringBuilder();
        boolean approve = true;
        @Override public void onState(AgentLoop.State s, String d) { states.add(s); }
        @Override public void onModelText(String delta) {}
        @Override public synchronized void onOutput(boolean err, String t) { (err ? stderr : stdout).append(t); }
        @Override public void onMetrics(String line) {}
        @Override public boolean confirm(String reason) { return approve; }
    }

    private ProjectWorkspace workspace() throws Exception { return ProjectWorkspace.create(tmp.newFolder("projects")); }

    @Test public void fixesRuntimeErrorWithMinimalEdit() throws Exception {
        ScriptedModel model = new ScriptedModel(
                "FILE: main.py\n```python\nfrom calc import add\nprint('sum', add(2, 3))\n```\n"
                        + "FILE: calc.py\n```python\ndef add(a, b):\n    return a + c\n```\n",
                "EDIT: calc.py\n```\n<<<<<<< SEARCH\n    return a + c\n=======\n    return a + b\n>>>>>>> REPLACE\n```\n");
        ProjectWorkspace ws = workspace();
        Recorder ui = new Recorder();
        AgentLoop.Outcome out = new AgentLoop(model, bridge, ws, ui).run("add two numbers");

        assertEquals(out.message, AgentLoop.State.SUCCESS, out.state);
        assertEquals(2, ws.attempt);
        assertTrue(ui.stdout.toString().contains("sum 5"));
        // The fix prompt carried the real Termux error and the failing file.
        assertTrue(model.prompts.get(1).contains("NameError: name 'c' is not defined"));
        assertTrue(model.prompts.get(1).contains("FILE: calc.py"));
        // The executed copy inside "Termux" is byte-identical to the app's mirror.
        File remote = new File(agentHome, "projects/" + ws.id + "/calc.py");
        assertEquals(ws.read("calc.py"), new String(Files.readAllBytes(remote.toPath()), StandardCharsets.UTF_8));
        assertEquals("def add(a, b):\n    return a + b\n", ws.read("calc.py"));
        assertTrue(ui.states.contains(AgentLoop.State.VALIDATING));
        assertTrue(ui.states.contains(AgentLoop.State.PATCHING));
        assertTrue(new File(ws.dir, ".newal/log.jsonl").length() > 0);
    }

    @Test public void syntaxErrorsAreCaughtBeforeExecution() throws Exception {
        ScriptedModel model = new ScriptedModel(
                "FILE: main.py\n```python\ndef f(:\n    pass\n```\n",
                "FILE: main.py\n```python\nprint('fixed')\n```\n");
        ProjectWorkspace ws = workspace();
        Recorder ui = new Recorder();
        AgentLoop.Outcome out = new AgentLoop(model, bridge, ws, ui).run("print fixed");
        assertEquals(AgentLoop.State.SUCCESS, out.state);
        assertTrue(model.prompts.get(1).contains("Syntax check failed before running"));
        assertTrue(model.prompts.get(1).contains("main.py:1"));
    }

    @Test public void failingTestsAreNeverReportedAsSuccess() throws Exception {
        String tests = "FILE: tests/test_calc.py\n```python\nimport unittest\nfrom calc import sq\n\n"
                + "class T(unittest.TestCase):\n    def test_sq(self):\n        self.assertEqual(sq(3), 9)\n```\n";
        ScriptedModel model = new ScriptedModel(
                "FILE: calc.py\n```python\ndef sq(x):\n    return x * 2\n```\n" + tests,
                "EDIT: calc.py\n```\n<<<<<<< SEARCH\n    return x * 2\n=======\n    return x * x\n>>>>>>> REPLACE\n```\n");
        ProjectWorkspace ws = workspace();
        AgentLoop.Outcome out = new AgentLoop(model, bridge, ws, new Recorder()).run("square");
        assertEquals(out.message, AgentLoop.State.SUCCESS, out.state);
        assertEquals(2, ws.attempt);
        assertTrue(out.message.contains("الاختبارات"));
    }

    /** Regression: an interactive calculator is not a bug to "fix" by rewriting it. */
    @Test public void interactiveProgramIsRecognisedInsteadOfRewritten() throws Exception {
        ScriptedModel model = new ScriptedModel(
                "FILE: main.py\n```python\nprint('Calculator')\nchoice = input('Enter your choice: ')\nprint(choice)\n```");
        AgentLoop.Outcome out = new AgentLoop(model, bridge, workspace(), new Recorder()).run("calculator");
        assertEquals(out.message, AgentLoop.State.SUCCESS, out.state);
        assertEquals("python main.py", out.interactiveCommand);
        assertEquals(1, model.calls);
    }

    @Test public void interactiveProgramIsTestedWithSampleInput() throws Exception {
        ScriptedModel model = new ScriptedModel(
                "FILE: main.py\n```python\nwhile True:\n    c = input('choice: ')\n    if c == '5':\n        break\n"
                        + "    a = float(input('a: '))\n    b = float(input('b: '))\n    print('sum', a + b)\n```\n"
                        + "STDIN:\n```\n1\n2\n3\n5\n```");
        Recorder ui = new Recorder();
        AgentLoop.Outcome out = new AgentLoop(model, bridge, workspace(), ui).run("calculator");
        assertEquals(out.message, AgentLoop.State.SUCCESS, out.state);
        assertEquals(null, out.interactiveCommand);
        assertTrue(ui.stdout.toString(), ui.stdout.toString().contains("sum 5.0"));
    }

    @Test public void realErrorAfterInputIsStillFixed() throws Exception {
        ScriptedModel model = new ScriptedModel(
                "FILE: main.py\n```python\nimport sys\nx = undefined_name\n```\n",
                "FILE: main.py\n```python\nprint('ok')\n```\n");
        AgentLoop.Outcome out = new AgentLoop(model, bridge, workspace(), new Recorder()).run("x");
        assertEquals(AgentLoop.State.SUCCESS, out.state);
        assertEquals(null, out.interactiveCommand);
        assertEquals(2, model.calls);
    }

    /** Minimal MCP server (stdio JSON-RPC) with one tool, as a real MCP server would expose. */
    static final String MCP_SERVER = String.join("\n",
            "import json, sys",
            "for line in sys.stdin:",
            "    m = json.loads(line)",
            "    if 'id' not in m: continue",
            "    meth = m['method']",
            "    if meth == 'initialize': r = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'demo', 'version': '1'}}",
            "    elif meth == 'tools/list': r = {'tools': [{'name': 'add', 'description': 'Add two numbers', 'inputSchema': {'type': 'object', 'properties': {'a': {'type': 'number'}, 'b': {'type': 'number'}}}}]}",
            "    elif meth == 'tools/call': a = m['params']['arguments']; r = {'content': [{'type': 'text', 'text': str(a['a'] + a['b'])}]}",
            "    else: r = {}",
            "    print(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': r}), flush=True)",
            "");

    @Test public void pluginAndMcpToolsAreListedCalledAndAnsweredWithSay() throws Exception {
        File plugin = new File(agentHome, "tools/echo");
        assertTrue(plugin.mkdirs());
        Files.write(new File(plugin, "tool.json").toPath(),
                "{\"name\":\"echo\",\"description\":\"Echo the arguments\",\"parameters\":{\"text\":\"string\"},\"command\":\"cat\"}".getBytes());
        File server = new File(agentHome, "demo_mcp.py");
        Files.write(server.toPath(), MCP_SERVER.getBytes());
        Files.write(new File(agentHome, "mcp.json").toPath(),
                ("{\"servers\":{\"demo\":{\"command\":\"python3\",\"args\":[\"" + server.getAbsolutePath() + "\"]}}}").getBytes());

        bridge.ensureConnected(15000);
        String tools = bridge.listTools().toString();
        assertTrue(tools, tools.contains("\"echo\"") && tools.contains("demo.add"));

        ScriptedModel model = new ScriptedModel(
                "TOOL: echo {\"text\": \"hi\"}\nTOOL: demo.add {\"a\": 2, \"b\": 3}",
                "SAY: the sum is 5");
        ProjectWorkspace ws = workspace();
        Recorder ui = new Recorder();
        AgentLoop.Outcome out = new AgentLoop(model, bridge, ws, ui).run("add 2 and 3 with the tool");
        assertEquals(out.message, AgentLoop.State.SUCCESS, out.state);
        assertEquals("the sum is 5", out.message);
        assertEquals(2, model.calls);
        assertEquals(1, ws.attempt);
        // The second prompt carried both real tool results back to the model.
        assertTrue(model.prompts.get(1), model.prompts.get(1).contains("echo -> {\"text\": \"hi\"}"));
        assertTrue(model.prompts.get(1), model.prompts.get(1).contains("demo.add -> 5"));
    }

    @Test public void unknownToolIsReportedToTheModel() throws Exception {
        ScriptedModel model = new ScriptedModel("TOOL: nope {}", "SAY: sorry");
        AgentLoop.Outcome out = new AgentLoop(model, bridge, workspace(), new Recorder()).run("x");
        assertEquals(AgentLoop.State.SUCCESS, out.state);
        assertTrue(model.prompts.get(1).contains("nope FAILED -> unknown tool: nope"));
    }

    @Test public void repeatedIdenticalFailureStopsForTheUser() throws Exception {
        ScriptedModel model = new ScriptedModel(
                "FILE: main.py\n```python\nraise SystemExit('boom 1')\n```\n",
                "FILE: main.py\n```python\nraise SystemExit('boom 2')\n```\n",
                "FILE: main.py\n```python\nraise SystemExit('boom 3')\n```\n",
                "FILE: main.py\n```python\nraise SystemExit('boom 4')\n```\n");
        AgentLoop loop = new AgentLoop(model, bridge, workspace(), new Recorder());
        AgentLoop.Outcome out = loop.run("fail");
        assertEquals(AgentLoop.State.WAITING_FOR_USER, out.state);
        assertEquals(3, model.calls);
    }

    @Test public void destructiveCommandNeedsApproval() throws Exception {
        ScriptedModel model = new ScriptedModel("FILE: main.sh\n```bash\nrm -rf ~/\n```\n");
        Recorder ui = new Recorder();
        ui.approve = false;
        AgentLoop.Outcome out = new AgentLoop(model, bridge, workspace(), ui).run("clean");
        assertEquals(AgentLoop.State.WAITING_FOR_USER, out.state);
        assertTrue(ui.stdout.length() == 0 && ui.stderr.length() == 0);
    }

    @Test public void reconnectsAfterTermuxAgentDies() throws Exception {
        ScriptedModel model = new ScriptedModel("FILE: main.py\n```python\nprint('again')\n```\n");
        ProjectWorkspace ws = workspace();
        assertEquals(AgentLoop.State.SUCCESS, new AgentLoop(model, bridge, ws, new Recorder()).run("x").state);
        for (Process p : agents) p.destroyForcibly().waitFor();
        Recorder ui = new Recorder();
        // Resume re-executes the finished files without regenerating them.
        AgentLoop.Outcome out = new AgentLoop(model, bridge, ws, ui).resume();
        assertEquals(out.message, AgentLoop.State.SUCCESS, out.state);
        assertEquals(1, model.calls);
        assertEquals(2, launches);
        assertTrue(ui.stdout.toString().contains("again"));
    }
}
