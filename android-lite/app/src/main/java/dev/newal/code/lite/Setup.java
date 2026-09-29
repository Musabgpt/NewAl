package dev.newal.code.lite;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.os.Build;
import android.system.Os;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * Puts NewAl Code on the phone and starts it.
 *
 * The APK carries programs in its native library folder, the only place Android lets an app run programs from:
 * libnewalpy.so (python), libllama-server.so (and a faster build for CPUs with dot-product instructions), and
 * Python's own libraries. Its assets carry Python's standard library, NewAl Code and the certificate list, which are
 * unpacked into the app's files once per version. NewAl Code then runs as `python -m newal_code app` and the
 * WebView shows its interface, as on a computer.
 */
final class Setup {
    static final int PORT = 8790;

    final Context ctx;
    final File files, home, python, app, bin, log;
    final String libDir, abi;

    Setup(Context c) {
        ctx = c;
        files = c.getFilesDir();
        home = new File(files, "home");
        python = new File(files, "python");
        app = new File(files, "app");
        bin = new File(files, "bin");
        log = new File(files, "newal.log");
        libDir = c.getApplicationInfo().nativeLibraryDir;
        abi = Build.SUPPORTED_ABIS[0];
    }

    /** Unpacks the assets when this version of the app has not yet. */
    void prepare() throws IOException {
        String version;
        try {
            PackageInfo p = ctx.getPackageManager().getPackageInfo(ctx.getPackageName(), 0);
            version = p.versionName + "/" + p.lastUpdateTime;
        } catch (Exception e) {
            version = "?";
        }
        File marker = new File(files, "installed.txt");
        if (marker.exists() && version.equals(read(marker))) {
            return;
        }
        delete(python);
        delete(app);
        unzip("python-stdlib.zip", python);
        unzip("python-dynload-" + abi + ".zip", new File(python, "lib/python3.14/lib-dynload"));
        unzip("newal_code.zip", app);
        copy("cacert.pem", new File(python, "cacert.pem"));
        // `python3` and `python` for the agent's commands: links to the program in the native library folder.
        bin.mkdirs();
        for (String name : new String[] {"python3", "python"}) {
            File link = new File(bin, name);
            link.delete();
            try {
                Os.symlink(libDir + "/libnewalpy.so", link.getPath());
            } catch (Exception ignored) {
                // no links on this storage: the agent still has sh and the tools
            }
        }
        File sample = new File(home, "projects/hello");
        if (!sample.exists() && sample.mkdirs()) {
            write(new File(sample, "README.md"), "# hello\n\nA first project for NewAl Code Lite. Ask it to write "
                    + "a Python script here, run it and explain it.\n");
        }
        write(marker, version);
    }

    /** The llama-server build for this CPU (the dot-product build where the CPU has those instructions). */
    String llamaServer() {
        File fast = new File(libDir, "libllama-server-dotprod.so");
        if (fast.exists() && cpuFeatures().contains(" asimddp")) {
            return fast.getPath();
        }
        return new File(libDir, "libllama-server.so").getPath();
    }

    Process start() throws IOException {
        ProcessBuilder pb = new ProcessBuilder(libDir + "/libnewalpy.so", "-m", "newal_code", "app",
                "--port", String.valueOf(PORT), "--no-browser");
        Map<String, String> env = pb.environment();
        env.put("HOME", home.getPath());
        env.put("PYTHONHOME", python.getPath());
        env.put("PYTHONPATH", app.getPath());
        env.put("PYTHONUNBUFFERED", "1");
        env.put("NEWAL_CODE_HOME", new File(home, ".newal-code").getPath());
        env.put("NEWAL_LLAMA_SERVER", llamaServer());
        env.put("LD_LIBRARY_PATH", libDir);
        env.put("SSL_CERT_FILE", new File(python, "cacert.pem").getPath());
        env.put("TMPDIR", ctx.getCacheDir().getPath());
        String path = System.getenv("PATH");
        env.put("PATH", bin.getPath() + ":" + (path != null ? path : "/system/bin"));
        home.mkdirs();
        pb.directory(home);
        pb.redirectErrorStream(true);
        pb.redirectOutput(log);
        return pb.start();
    }

    String logTail() {
        try {
            String s = read(log);
            return s.length() > 4000 ? s.substring(s.length() - 4000) : s;
        } catch (IOException e) {
            return "";
        }
    }

    // ---------------------------------------------------------------------------------------------- helpers

    private static String cpuFeatures() {
        try {
            for (String line : read(new File("/proc/cpuinfo")).split("\n")) {
                if (line.startsWith("Features")) {
                    return " " + line.substring(line.indexOf(':') + 1).trim() + " ";
                }
            }
        } catch (IOException ignored) {
        }
        return "";
    }

    private void unzip(String asset, File dest) throws IOException {
        dest.mkdirs();
        String top = dest.getCanonicalPath() + File.separator;
        try (ZipInputStream z = new ZipInputStream(ctx.getAssets().open(asset))) {
            byte[] buf = new byte[65536];
            for (ZipEntry e; (e = z.getNextEntry()) != null; ) {
                File out = new File(dest, e.getName());
                if (!out.getCanonicalPath().startsWith(top)) {
                    throw new IOException("bad entry " + e.getName());
                }
                if (e.isDirectory()) {
                    out.mkdirs();
                    continue;
                }
                out.getParentFile().mkdirs();
                try (OutputStream o = new FileOutputStream(out)) {
                    for (int n; (n = z.read(buf)) > 0; ) {
                        o.write(buf, 0, n);
                    }
                }
            }
        }
    }

    private void copy(String asset, File out) throws IOException {
        out.getParentFile().mkdirs();
        try (InputStream in = ctx.getAssets().open(asset); OutputStream o = new FileOutputStream(out)) {
            byte[] buf = new byte[65536];
            for (int n; (n = in.read(buf)) > 0; ) {
                o.write(buf, 0, n);
            }
        }
    }

    static String read(File f) throws IOException {
        StringBuilder sb = new StringBuilder();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(f),
                StandardCharsets.UTF_8))) {
            for (String line; (line = r.readLine()) != null; ) {
                sb.append(line).append('\n');
            }
        }
        return sb.toString().trim();
    }

    private static void write(File f, String text) throws IOException {
        try (OutputStream o = new FileOutputStream(f)) {
            o.write(text.getBytes(StandardCharsets.UTF_8));
        }
    }

    private static void delete(File f) {
        File[] kids = f.listFiles();
        if (kids != null) {
            for (File k : kids) {
                delete(k);
            }
        }
        f.delete();
    }
}
