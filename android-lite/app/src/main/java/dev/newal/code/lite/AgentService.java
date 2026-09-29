package dev.newal.code.lite;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

/**
 * Keeps NewAl Code (and the model it starts) running while the app is open or working: a foreground service, so
 * Android does not stop it to free memory in the middle of an answer.
 */
public class AgentService extends Service {
    private static final String CHANNEL = "newal";
    private static Process python;
    static volatile String error = "";

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        foreground();
        new Thread(this::ensureRunning, "newal-start").start();
        return START_STICKY;
    }

    private void foreground() {
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, getString(R.string.app_name),
                NotificationManager.IMPORTANCE_LOW));
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class),
                PendingIntent.FLAG_IMMUTABLE);
        Notification n = new Notification.Builder(this, CHANNEL)
                .setContentTitle(getString(R.string.app_name))
                .setContentText(getString(R.string.running))
                .setSmallIcon(R.drawable.ic_stat)
                .setContentIntent(open)
                .setOngoing(true)
                .build();
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(1, n);
        }
    }

    private synchronized void ensureRunning() {
        if (python != null && python.isAlive()) {
            return;
        }
        try {
            Setup s = new Setup(this);
            s.prepare();
            PhoneServer.start(this, s.key());
            python = s.start();
            error = "";
        } catch (Exception e) {
            error = String.valueOf(e);
            Log.e("NewAlCode", "cannot start", e);
        }
    }

    @Override
    public void onDestroy() {
        if (python != null) {
            python.destroy();          // SIGTERM: NewAl Code stops its models, then exits
        }
        super.onDestroy();
    }
}
