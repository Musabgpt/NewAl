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
    private static volatile boolean stopping;
    static volatile String error = "";

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        stopping = false;
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
            watch(python);
        } catch (Exception e) {
            error = String.valueOf(e);
            Log.e("NewAlCode", "cannot start", e);
        }
    }

    /**
     * NewAl Code started again when it ends while the app runs: Android may kill it to free memory (a 3 GB phone
     * installing Termux's packages with a model loaded did), and without it the app shows nothing. At most five
     * restarts in ten minutes (a crash on start would not end).
     */
    private void watch(Process p) {
        new Thread(() -> {
            try {
                int code = p.waitFor();
                if (stopping) {
                    return;
                }
                Log.w("NewAlCode", "NewAl Code ended (" + code + "): starting it again");
                long now = System.currentTimeMillis();
                synchronized (AgentService.class) {
                    restarts.removeIf(t -> now - t > 600_000);
                    if (restarts.size() >= 5) {
                        error = "NewAl Code keeps stopping (exit " + code + ")";
                        return;
                    }
                    restarts.add(now);
                }
                Thread.sleep(1500);
                ensureRunning();
            } catch (InterruptedException ignored) {
            }
        }, "newal-watch").start();
    }

    private static final java.util.List<Long> restarts = new java.util.ArrayList<>();

    @Override
    public void onDestroy() {
        stopping = true;
        if (python != null) {
            python.destroy();          // SIGTERM: NewAl Code stops its models, then exits
        }
        super.onDestroy();
    }
}
