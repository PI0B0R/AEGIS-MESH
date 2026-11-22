package pl.aegismesh.sensor

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import org.json.JSONObject

/**
 * Every 15 minutes (the shortest period Android allows) asks the server for findings with
 * priority 4-5 that still wait ("nowe") and shows one notification for those not announced yet.
 * Uses the platform JobScheduler, so the app needs no extra libraries.
 */
class AlertJob : JobService() {
    @Volatile private var worker: Thread? = null

    override fun onStartJob(params: JobParameters): Boolean {
        val session = Session(this)
        if (!session.loggedIn) return false
        worker = Thread {
            // A network error is retried by Android; an HTTP answer (e.g. expired login) is not.
            val retry = runCatching { checkNow(this, session) }.exceptionOrNull()?.let { it !is HttpFailure } ?: false
            jobFinished(params, retry)
        }.apply { start() }
        return true
    }

    override fun onStopJob(params: JobParameters): Boolean { worker?.interrupt(); return true }

    companion object {
        private const val JOB_ID = 4501
        private const val CHANNEL = "alerty"

        fun schedule(context: Context) {
            val job = JobInfo.Builder(JOB_ID, ComponentName(context, AlertJob::class.java))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setPeriodic(15 * 60 * 1000L)
                .setPersisted(false)
                .build()
            context.getSystemService(JobScheduler::class.java).schedule(job)
        }

        fun cancel(context: Context) = context.getSystemService(JobScheduler::class.java).cancel(JOB_ID)

        /** Fetches waiting P4-5 findings and notifies about the new ones. Returns how many were new. */
        internal fun checkNow(context: Context, session: Session): Int {
            val data = session.api().get("/api/findings?min_priority=4")
            val all = data.optJSONArray("findings") ?: return 0
            val open = (0 until all.length()).map { all.getJSONObject(it) }.filter(::waiting)
            val fresh = open.filter { it.optInt("id").toString() !in session.announced }
            session.announced = open.map { it.optInt("id").toString() }.toSet()   // checked ones drop out
            if (fresh.isNotEmpty()) announce(context, fresh)
            return fresh.size
        }

        private fun announce(context: Context, fresh: List<JSONObject>) {
            val manager = context.getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(NotificationChannel(CHANNEL, "Alerty priorytet 4–5", NotificationManager.IMPORTANCE_HIGH))
            val first = fresh.maxBy { it.optInt("priority") }
            val title = if (fresh.size == 1) "P${first.optInt("priority")} · ${findingPlace(first)}"
                        else "${fresh.size} nowe znaleziska z priorytetem 4–5"
            val open = PendingIntent.getActivity(context, 0,
                Intent(context, MainActivity::class.java).putExtra("tab", 1).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val note = Notification.Builder(context, CHANNEL)
                .setSmallIcon(android.R.drawable.stat_sys_warning)
                .setContentTitle(title)
                .setContentText(first.optString("title"))
                .setStyle(Notification.BigTextStyle().bigText(fresh.take(5).joinToString("\n") {
                    "P${it.optInt("priority")} ${findingPlace(it)}: ${it.optString("title")}" }))
                .setContentIntent(open).setAutoCancel(true).build()
            runCatching { manager.notify(1, note) }      // no notification permission: nothing to show, no crash
        }
    }
}
