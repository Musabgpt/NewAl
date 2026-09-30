package dev.newal.code.lite;

import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.Settings;
import android.widget.Toast;

import java.util.HashSet;
import java.util.Set;

/**
 * The one permission, on a phone: NewAl Code's "Give full access" (full-auto threads) and then, from the same tap,
 * what Android asks for itself, one after another: notifications (Android 13 and up), the phone's files (All files
 * access), screen control (NewAl Code's accessibility service) and Termux's commands when Termux is installed.
 * Android shows each on its own screen or dialog; this walks the user through them and skips what is granted
 * already. A step the user declines is not asked again in the same walk.
 */
final class Access {
    static final int REQUEST = 11;
    private static boolean running, dialog, page, left;
    private static final Set<String> asked = new HashSet<>();

    private Access() {
    }

    static void start(MainActivity a) {
        running = true;
        dialog = page = left = false;
        asked.clear();
        next(a);
    }

    /** The app went to the back (a settings page opened). */
    static void paused() {
        if (page) {
            left = true;
        }
    }

    /** Back from a settings page: the next step. */
    static void resumed(MainActivity a) {
        if (running && page && left) {
            page = left = false;
            next(a);
        }
    }

    /** A permission dialog answered: the next step. */
    static void answered(MainActivity a, int request) {
        if (running && dialog && request == REQUEST) {
            dialog = false;
            next(a);
        }
    }

    static boolean notifications(MainActivity a) {
        return Build.VERSION.SDK_INT < 33
                || a.checkSelfPermission("android.permission.POST_NOTIFICATIONS") == PackageManager.PERMISSION_GRANTED;
    }

    static boolean files(android.content.Context a) {
        if (Build.VERSION.SDK_INT >= 30) {
            return Environment.isExternalStorageManager();
        }
        return a.checkSelfPermission("android.permission.READ_EXTERNAL_STORAGE") == PackageManager.PERMISSION_GRANTED;
    }

    private static void next(MainActivity a) {
        if (!notifications(a) && asked.add("notifications")) {
            ask(a, "android.permission.POST_NOTIFICATIONS");
            return;
        }
        if (!files(a) && asked.add("files")) {
            if (Build.VERSION.SDK_INT >= 30) {
                if (open(a, new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                        Uri.parse("package:" + a.getPackageName())), "Files: turn on \"Allow access to manage all "
                        + "files\", then come back")
                        || open(a, new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION),
                        "Files: turn NewAl Code on, then come back")) {
                    return;
                }
            } else {
                ask(a, "android.permission.READ_EXTERNAL_STORAGE", "android.permission.WRITE_EXTERNAL_STORAGE");
                return;
            }
        }
        if (PhoneControlService.on == null && asked.add("screen")) {
            String hint = Build.VERSION.SDK_INT >= 33
                    ? "Screen control: turn NewAl Code on (if it is greyed out: App info > ⋮ > Allow restricted "
                    + "settings), then come back"
                    : "Screen control: turn NewAl Code on, then come back";
            Intent details = new Intent("android.settings.ACCESSIBILITY_DETAILS_SETTINGS")
                    .putExtra(Intent.EXTRA_COMPONENT_NAME,
                            new ComponentName(a, PhoneControlService.class).flattenToString());
            if ((details.resolveActivity(a.getPackageManager()) != null && open(a, details, hint))
                    || open(a, new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS), hint)) {
                return;
            }
        }
        if (Termux.installed(a) && !Termux.allowed(a) && asked.add("termux")) {
            ask(a, Termux.PERMISSION);
            return;
        }
        running = false;
        a.accessDone();
    }

    private static void ask(MainActivity a, String... permissions) {
        dialog = true;
        a.requestPermissions(permissions, REQUEST);
    }

    private static boolean open(MainActivity a, Intent i, String hint) {
        try {
            page = true;
            left = false;
            a.startActivity(i);
            Toast.makeText(a, hint, Toast.LENGTH_LONG).show();
            return true;
        } catch (Exception e) {
            page = false;
            return false;
        }
    }
}
