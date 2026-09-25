package com.musab.aragpt2;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import androidx.core.app.NotificationCompat;
import androidx.core.app.ServiceCompat;

/**
 * Foreground service that keeps the app process alive while an agent task runs, so switching
 * to another app does not let Android kill a long generate/run/fix loop. It only shows progress;
 * the loop itself runs on the app's worker thread.
 */
public final class AgentService extends Service {
    private static final String CHANNEL_RUN = "agent_run", CHANNEL_DONE = "agent_done";
    private static final int ID_RUN = 1, ID_DONE = 2;
    private static final String EXTRA_TEXT = "text";

    static void start(Context c, String text) {
        Intent i = new Intent(c, AgentService.class).putExtra(EXTRA_TEXT, text);
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) c.startForegroundService(i);
            else c.startService(i);
        } catch (RuntimeException ignored) {
            // Not allowed from the background on some versions; the task still runs, just unprotected.
        }
    }

    /** Updates the progress notification (cheap; safe to call on every state change). */
    static void update(Context c, String text) {
        NotificationManager nm = channels(c);
        if (nm != null) nm.notify(ID_RUN, build(c, CHANNEL_RUN, "المهمة تعمل", text, true));
    }

    /** Stops the service and leaves a notification with the result. */
    static void finish(Context c, String title, String text) {
        c.stopService(new Intent(c, AgentService.class));
        NotificationManager nm = channels(c);
        if (nm != null) nm.notify(ID_DONE, build(c, CHANNEL_DONE, title, text, false));
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        String text = intent == null ? "" : intent.getStringExtra(EXTRA_TEXT);
        channels(this);
        int type = Build.VERSION.SDK_INT >= 34 ? ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE : 0;
        ServiceCompat.startForeground(this, ID_RUN, build(this, CHANNEL_RUN, "المهمة تعمل", text, true), type);
        return START_NOT_STICKY;
    }

    @Override public IBinder onBind(Intent intent) { return null; }

    private static Notification build(Context c, String channel, String title, String text, boolean ongoing) {
        Intent open = new Intent(c, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent pi = PendingIntent.getActivity(c, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new NotificationCompat.Builder(c, channel)
                .setSmallIcon(android.R.drawable.ic_menu_manage)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(text))
                .setOngoing(ongoing)
                .setOnlyAlertOnce(true)
                .setAutoCancel(!ongoing)
                .setContentIntent(pi)
                .build();
    }

    private static NotificationManager channels(Context c) {
        NotificationManager nm = (NotificationManager) c.getSystemService(NOTIFICATION_SERVICE);
        if (nm != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(new NotificationChannel(CHANNEL_RUN, "تقدم المهام", NotificationManager.IMPORTANCE_LOW));
            nm.createNotificationChannel(new NotificationChannel(CHANNEL_DONE, "نتائج المهام", NotificationManager.IMPORTANCE_DEFAULT));
        }
        return nm;
    }
}
