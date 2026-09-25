package com.musab.aragpt2;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;

/** Decides how a project is installed, validated, tested and run, from the files it contains. */
public final class CommandPlanner {
    /** Reads a project file; returns "" when unavailable. */
    public interface Reader { String read(String path); }

    public static final class Plan {
        /** Dependency install step and the file whose hash decides whether it must rerun. */
        public String install, installKey;
        /** The command whose exit code decides success (tests when present, else the program). */
        public String command;
        public boolean isTest;
        public final Set<String> requiredCommands = new LinkedHashSet<>();
    }

    private static final Pattern PY_TEST = Pattern.compile("(^|/)(test_[^/]*|[^/]*_test)\\.py$");
    private static final Pattern MAIN_GUARD = Pattern.compile("if\\s+__name__\\s*==\\s*['\"]__main__['\"]");

    private CommandPlanner() {}

    public static Plan plan(Collection<String> paths, Reader reader, String override) {
        Plan p = new Plan();
        List<String> py = byExt(paths, ".py"), js = byExt(paths, ".js"), sh = byExt(paths, ".sh"),
                c = byExt(paths, ".c"), cpp = byExt(paths, ".cpp"), go = byExt(paths, ".go"),
                rs = byExt(paths, ".rs"), java = byExt(paths, ".java");

        if (paths.contains("requirements.txt") && !reader.read("requirements.txt").trim().isEmpty()) {
            p.install = "python -m pip install -q --disable-pip-version-check -r requirements.txt";
            p.installKey = "requirements.txt";
        }
        JSONObject pkg = null;
        if (paths.contains("package.json")) {
            try { pkg = new JSONObject(reader.read("package.json")); } catch (Exception ignored) {}
            if (pkg != null && (pkg.has("dependencies") || pkg.has("devDependencies"))) {
                p.install = "npm install --silent --no-audit --no-fund";
                p.installKey = "package.json";
            }
        }

        if (override != null && !override.trim().isEmpty()) {
            p.command = override.trim();
        } else if (pkg != null) {
            JSONObject scripts = pkg.optJSONObject("scripts");
            String test = scripts == null ? "" : scripts.optString("test");
            if (!test.isEmpty() && !test.contains("no test specified")) { p.command = "npm test --silent"; p.isTest = true; }
            else if (scripts != null && scripts.has("start")) p.command = "npm start --silent";
            else p.command = "node " + quote(first(js, pkg.optString("main"), "index.js", "app.js", "main.js", "server.js"));
        } else if (!py.isEmpty()) {
            List<String> tests = new ArrayList<>();
            for (String f : py) if (PY_TEST.matcher(f).find()) tests.add(f);
            if (!tests.isEmpty()) {
                p.isTest = true;
                boolean pytest = reader.read("requirements.txt").toLowerCase(Locale.ROOT).contains("pytest");
                for (String t : tests) if (reader.read(t).contains("import pytest")) pytest = true;
                String dir = commonTopDir(tests);
                p.command = pytest ? "python -m pytest -q -p no:cacheprovider"
                        : dir == null ? "python -m unittest discover -q -s . -p '*test*.py'"
                        // PYTHONPATH keeps project modules importable from a plain tests/ folder.
                        : "PYTHONPATH=. python -m unittest discover -q -s " + quote(dir) + " -p '*test*.py'";
            } else {
                p.command = "python " + quote(pythonEntry(py, reader));
            }
        } else if (!sh.isEmpty()) {
            p.command = "bash " + quote(first(sh, null, "main.sh", "run.sh"));
        } else if (!c.isEmpty()) {
            p.command = "clang -O1 -o .newal_bin " + join(c) + " -lm && ./.newal_bin";
        } else if (!cpp.isEmpty()) {
            p.command = "clang++ -O1 -std=c++17 -o .newal_bin " + join(cpp) + " && ./.newal_bin";
        } else if (!go.isEmpty()) {
            p.command = paths.contains("go.mod") ? "go run ." : "go run " + join(go);
        } else if (paths.contains("Cargo.toml")) {
            p.command = "cargo run -q";
        } else if (!rs.isEmpty()) {
            p.command = "rustc -O -o .newal_bin " + quote(first(rs, null, "main.rs")) + " && ./.newal_bin";
        } else if (!java.isEmpty()) {
            p.command = "java " + quote(first(java, null, "Main.java"));
        } else if (!js.isEmpty()) {
            p.command = "node " + quote(first(js, null, "index.js", "main.js", "app.js"));
        }

        if (p.install != null) p.requiredCommands.add(p.install.split(" ")[0]);
        if (p.command != null) {
            for (String part : p.command.split("&&|\\|\\||;|\\|")) {
                for (String word : part.trim().split("\\s+")) {
                    if (word.contains("=")) continue; // VAR=value prefix
                    if (!word.isEmpty() && !word.startsWith("./")) p.requiredCommands.add(word);
                    break;
                }
            }
        }
        return p;
    }

    /** main.py / app.py first, then the file with a __main__ guard, then the only file. */
    private static String pythonEntry(List<String> py, Reader reader) {
        for (String name : new String[]{"main.py", "app.py", "run.py", "__main__.py"}) if (py.contains(name)) return name;
        String guarded = null;
        for (String f : py) {
            if (MAIN_GUARD.matcher(reader.read(f)).find()) {
                if (guarded == null || depth(f) < depth(guarded)) guarded = f;
            }
        }
        if (guarded != null) return guarded;
        String top = py.get(0);
        for (String f : py) if (depth(f) < depth(top)) top = f;
        return top;
    }

    private static List<String> byExt(Collection<String> paths, String ext) {
        List<String> r = new ArrayList<>();
        for (String p : paths) if (p.toLowerCase(Locale.ROOT).endsWith(ext)) r.add(p);
        return r;
    }

    private static String first(List<String> files, String preferred, String... names) {
        if (preferred != null && !preferred.isEmpty() && files.contains(preferred)) return preferred;
        for (String n : names) if (files.contains(n)) return n;
        return files.isEmpty() ? (preferred != null && !preferred.isEmpty() ? preferred : names[0]) : files.get(0);
    }

    /** The first path component shared by all files, or null when some are at the top level. */
    private static String commonTopDir(List<String> files) {
        String dir = null;
        for (String f : files) {
            int slash = f.indexOf('/');
            if (slash < 0) return null;
            String d = f.substring(0, slash);
            if (dir == null) dir = d;
            else if (!dir.equals(d)) return null;
        }
        return dir;
    }

    private static int depth(String path) {
        int d = 0;
        for (int i = 0; i < path.length(); i++) if (path.charAt(i) == '/') d++;
        return d;
    }

    private static String join(List<String> files) {
        StringBuilder b = new StringBuilder();
        for (String f : files) { if (b.length() > 0) b.append(' '); b.append(quote(f)); }
        return b.toString();
    }

    static String quote(String s) {
        return s.matches("[\\w./-]+") ? s : "'" + s.replace("'", "'\\''") + "'";
    }
}
