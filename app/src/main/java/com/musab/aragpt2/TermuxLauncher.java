package com.musab.aragpt2;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;

/**
 * Starts newal_agent.py through Termux's official RUN_COMMAND service as a background task.
 * Termux keeps its own foreground service alive while the task runs, so the agent persists
 * without the Termux UI being open.
 */
final class TermuxLauncher implements TermuxBridge.Launcher {
    static final String PACKAGE = "com.termux";
    static final String PERMISSION = "com.termux.permission.RUN_COMMAND";
    private static final String PYTHON = "/data/data/com.termux/files/usr/bin/python";
    private static final String HOME = "/data/data/com.termux/files/home";

    /**
     * The only command the user ever types, once: lets this app run commands (RUN_COMMAND is
     * refused until allow-external-apps is set inside Termux) and installs Python for the agent.
     */
    static final String SETUP_COMMAND =
            "mkdir -p ~/.termux && f=~/.termux/termux.properties && touch $f && "
            + "(grep -q '^allow-external-apps' $f && sed -i 's/^allow-external-apps.*/allow-external-apps=true/' $f "
            + "|| echo 'allow-external-apps=true' >> $f) && termux-reload-settings && pkg install -y python";

    private final Context context;

    TermuxLauncher(Context context) { this.context = context.getApplicationContext(); }

    static boolean isInstalled(Context c) {
        try {
            c.getPackageManager().getPackageInfo(PACKAGE, 0);
            return true;
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        }
    }

    static boolean hasPermission(Context c) {
        return c.checkSelfPermission(PERMISSION) == PackageManager.PERMISSION_GRANTED;
    }

    @Override public void launch(String script) {
        if (!isInstalled(context)) throw new IllegalStateException("Termux غير مثبت");
        if (!hasPermission(context)) throw new IllegalStateException("لم يُمنح إذن تشغيل الأوامر في Termux");
        Intent i = new Intent("com.termux.RUN_COMMAND");
        i.setClassName(PACKAGE, "com.termux.app.RunCommandService");
        i.putExtra("com.termux.RUN_COMMAND_PATH", PYTHON);
        i.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", new String[]{"-c", script, "newal-agent"});
        i.putExtra("com.termux.RUN_COMMAND_WORKDIR", HOME);
        i.putExtra("com.termux.RUN_COMMAND_BACKGROUND", true);
        i.putExtra("com.termux.RUN_COMMAND_COMMAND_LABEL", "NewAl agent");
        start(i);
    }

    /**
     * Opens a visible Termux session running an interactive program in its project folder,
     * leaving a shell there when it exits.
     */
    void openInteractive(String projectId, String command) {
        Intent i = new Intent("com.termux.RUN_COMMAND");
        i.setClassName(PACKAGE, "com.termux.app.RunCommandService");
        i.putExtra("com.termux.RUN_COMMAND_PATH", "/data/data/com.termux/files/usr/bin/bash");
        i.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", new String[]{"-c",
                command + "; echo; echo '[program finished]'; exec bash"});
        i.putExtra("com.termux.RUN_COMMAND_WORKDIR", HOME + "/newal/projects/" + projectId);
        i.putExtra("com.termux.RUN_COMMAND_BACKGROUND", false);
        i.putExtra("com.termux.RUN_COMMAND_SESSION_ACTION", "0"); // new session, bring Termux to front
        start(i);
    }

    private void start(Intent i) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(i);
        else context.startService(i);
    }
}
