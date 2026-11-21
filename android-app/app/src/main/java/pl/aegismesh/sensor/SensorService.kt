package pl.aegismesh.sensor

import android.app.*
import android.content.*
import android.location.*
import android.net.wifi.ScanResult
import android.net.wifi.WifiManager
import android.os.*
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Background sensor. Every 30 s it asks Android for a Wi-Fi scan (the system allows
 * about 4 scans per 2 minutes), sends only results newer than the last batch,
 * attaches a fresh GPS fix or the manual place, and raises a notification for
 * suspicious routers and hotspots.
 */
class SensorService : Service(), LocationListener {
    companion object {
        @Volatile var live = false
            private set
        const val PERIOD_S = 30L
        private const val MAX_RADIO_AGE_MS = 15000L
    }
    private val prefs by lazy { getSharedPreferences("sensor", MODE_PRIVATE) }
    private val location by lazy { requireNotNull(getSystemService(LocationManager::class.java)) }
    private val radio by lazy { applicationContext.getSystemService(WIFI_SERVICE) as WifiManager }
    private val notifications by lazy { requireNotNull(getSystemService(NotificationManager::class.java)) }
    private val executor = Executors.newSingleThreadScheduledExecutor()
    @Volatile private var latest: Location? = null
    @Volatile private var running = false
    private var lastScanMicros = -1L
    private val alerted = HashSet<String>()

    override fun onBind(intent: Intent?) = null
    override fun onLocationChanged(fix: Location) { latest = Location(fix) }
    override fun onProviderDisabled(provider: String) { latest = null }
    override fun onProviderEnabled(provider: String) {}
    @Deprecated("Legacy Android callback")
    override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "stop") { stopSelf(); return START_NOT_STICKY }
        if (running) return START_NOT_STICKY
        notifications.createNotificationChannel(NotificationChannel("sensor", "Pomiary telefonu", NotificationManager.IMPORTANCE_LOW))
        notifications.createNotificationChannel(NotificationChannel("alerts", "Podejrzane routery", NotificationManager.IMPORTANCE_HIGH))
        val stop = PendingIntent.getService(this, 0, Intent(this, SensorService::class.java).setAction("stop"),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        startForeground(1, Notification.Builder(this, "sensor").setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle("AEGIS Mesh · czujnik Wi-Fi").setContentText("Skan co $PERIOD_S s i wysyłka do laptopa")
            .setContentIntent(openApp()).setOngoing(true).addAction(android.R.drawable.ic_delete, "Zatrzymaj", stop).build())
        running = true
        live = true
        prefs.edit().putBoolean("active", true).putString("status", "Czujnik uruchomiony").apply()
        // GPS is optional: indoors the manual place (building / floor / room) is used instead.
        runCatching { location.requestLocationUpdates(LocationManager.GPS_PROVIDER, 1000L, 0f, this, mainLooper) }
        executor.execute { syncKnown(this, prefs) }
        executor.scheduleWithFixedDelay(::tick, 0, PERIOD_S, TimeUnit.SECONDS)
        return START_NOT_STICKY
    }

    private fun openApp() = PendingIntent.getActivity(this, 1, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)

    @Suppress("DEPRECATION")
    private fun tick() {
        try {
            if (!running) return
            val requested = radio.startScan()
            val scan = radio.scanResults
            alert(scan)
            val newest = scan.maxOfOrNull { it.timestamp } ?: -1L
            if (newest <= lastScanMicros) {
                report("Android nie dał nowego skanu (limit systemu) · widoczne punkty: ${scan.size}")
                return
            }
            val origin = prefs.getString("origin", null)
            val key = prefs.getString("key", null)
            if (origin == null || key == null) {
                lastScanMicros = newest
                report("Brak sparowania z laptopem. Alerty działają lokalnie · widoczne punkty: ${scan.size}")
                return
            }
            val point = position()
            val scoped = prefs.getString("scope", "AGH-LAB") != "local"
            val rows = JSONArray()
            for (ap in scan) {
                val age = SystemClock.elapsedRealtime() - ap.timestamp / 1000L
                if (age !in 0L..MAX_RADIO_AGE_MS || (scoped && ap.SSID != "AGH-LAB") || ap.BSSID == null) continue
                rows.put(JSONObject().put("ssid", ap.SSID).put("bssid", ap.BSSID)
                    .put("rssi_dbm", ap.level).put("frequency_mhz", ap.frequency)
                    .put("channel", wifiChannel(ap.frequency) ?: JSONObject.NULL)
                    .put("security", ap.capabilities).put("age_ms", age))
            }
            val body = JSONObject().put("sensor_id", sensorId(prefs)).put("location", point).put("networks", rows)
            if (!running) return
            post(origin, "/api/mobile-telemetry", body, key, prefs.getString("fp", null))
            prefs.edit().putInt("sent", prefs.getInt("sent", 0) + 1).putLong("sent_at", System.currentTimeMillis()).apply()
            lastScanMicros = newest
            val where = when (point.getString("provider")) {
                "gps" -> "GPS ±" + point.getDouble("accuracy").toInt() + " m"
                "manual" -> "miejsce: " + savedPlace(prefs)
                else -> "bez lokalizacji (ustaw miejsce w ⋯ Więcej)"
            }
            report("Ostatni raport: $where · sieci: ${rows.length()}" + if (requested) "" else " · skan z pamięci Androida")
        } catch (e: Exception) {
            if (e is HttpFailure && e.code == 401) {
                prefs.edit().remove("key").apply()
                report("Sesja wygasła. Sparuj telefon ponownie.")
            } else report(e.localizedMessage ?: "Błąd pomiaru")
        }
    }

    /** Fresh GPS fix (≤ 50 m, ≤ 15 s) first; otherwise the place set by hand or from a door QR code. */
    private fun position(): JSONObject {
        val manual = savedPlace(prefs)
        val fix = latest
        val point = if (fix != null && fix.hasAccuracy() && fix.accuracy <= 50 &&
            SystemClock.elapsedRealtimeNanos() - fix.elapsedRealtimeNanos in 0L..15_000_000_000L) {
            JSONObject().put("provider", "gps").put("lat", fix.latitude).put("lng", fix.longitude)
                .put("accuracy", fix.accuracy.toDouble()).put("timestamp", fix.time / 1000.0)
        } else {
            // No GPS fix: send the manual place if set, otherwise the scan alone (no pin on the map).
            JSONObject().put("provider", if (manual != null) "manual" else "none").put("timestamp", System.currentTimeMillis() / 1000.0)
        }
        if (manual != null) point.put("place", JSONObject().put("building", manual.building)
            .put("floor", manual.floor).put("room", manual.room))
        return point
    }

    private fun alert(scan: List<ScanResult>) {
        val known = loadKnown(this)
        for (ap in scan) {
            @Suppress("DEPRECATION") val name = ap.SSID.orEmpty()
            val bssid = ap.BSSID ?: continue
            val verdict = classify(SeenAp(name, bssid, ap.level, ap.frequency, ap.capabilities.orEmpty()), known)
            if (verdict.status.rank > Status.HOTSPOT.rank || !alerted.add(bssid.lowercase())) continue
            notifications.notify(bssid.hashCode(), Notification.Builder(this, "alerts")
                .setSmallIcon(android.R.drawable.stat_sys_warning)
                .setContentTitle(verdict.status.label + ": " + name.ifEmpty { "(ukryta sieć)" })
                .setContentText("$bssid · ${ap.level} dBm · ${verdict.reason}")
                .setStyle(Notification.BigTextStyle().bigText("$bssid · ${ap.level} dBm\n${verdict.reason}"))
                .setContentIntent(openApp()).setAutoCancel(true).build())
        }
    }

    private fun report(text: String) { prefs.edit().putString("status", text).apply() }
    override fun onDestroy() {
        running = false
        live = false
        executor.shutdownNow()
        location.removeUpdates(this)
        prefs.edit().putBoolean("active", false).putString("status", "Czujnik zatrzymany").apply()
        super.onDestroy()
    }
}
