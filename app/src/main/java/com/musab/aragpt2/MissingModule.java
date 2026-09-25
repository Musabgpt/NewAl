package com.musab.aragpt2;

import java.util.Collection;
import java.util.HashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Turns "module not found" errors into the package install that fixes them. */
final class MissingModule {
    private static final Pattern PYTHON = Pattern.compile("ModuleNotFoundError: No module named '([\\w.]+)'");
    private static final Pattern NODE = Pattern.compile("Cannot find module '([^'./][^']*)'");

    /** Import name → PyPI name where they differ. */
    private static final Map<String, String> PYPI = new HashMap<>();
    static {
        String[] pairs = {"cv2", "opencv-python", "PIL", "pillow", "yaml", "pyyaml", "sklearn", "scikit-learn",
                "bs4", "beautifulsoup4", "dateutil", "python-dateutil", "dotenv", "python-dotenv",
                "serial", "pyserial", "Crypto", "pycryptodome", "jwt", "pyjwt", "magic", "python-magic",
                "telegram", "python-telegram-bot", "docx", "python-docx", "fitz", "pymupdf"};
        for (int i = 0; i < pairs.length; i += 2) PYPI.put(pairs[i], pairs[i + 1]);
    }

    /** Standard-library modules that pip cannot provide on Termux. */
    private static final String[] NOT_ON_PIP = {"tkinter", "_tkinter", "distutils", "turtle"};

    private MissingModule() {}

    /** Install command for the missing module named in stderr, or null when there is none to install. */
    static String installCommand(String stderr, Collection<String> projectFiles) {
        Matcher py = PYTHON.matcher(stderr);
        String name = null;
        while (py.find()) name = py.group(1);
        if (name != null) {
            String top = name.split("\\.")[0];
            if (isLocal(top, projectFiles)) return null;
            for (String s : NOT_ON_PIP) if (s.equals(top)) return null;
            String pkg = PYPI.containsKey(top) ? PYPI.get(top) : top.replace('_', '-');
            return "python -m pip install -q --disable-pip-version-check " + CommandPlanner.quote(pkg);
        }
        Matcher js = NODE.matcher(stderr);
        if (js.find()) {
            String[] parts = js.group(1).split("/");
            String pkg = parts[0].startsWith("@") && parts.length > 1 ? parts[0] + "/" + parts[1] : parts[0];
            if (pkg.startsWith("node:")) return null;
            return "npm install --silent --no-audit --no-fund " + CommandPlanner.quote(pkg);
        }
        return null;
    }

    private static boolean isLocal(String module, Collection<String> files) {
        for (String f : files) {
            if (f.equals(module + ".py") || f.startsWith(module + "/") || f.endsWith("/" + module + ".py")) return true;
        }
        return false;
    }
}
