package pl.aegismesh.sensor

import android.app.Activity
import android.graphics.Color
import android.net.wifi.WifiManager
import android.os.*
import android.view.Gravity
import android.view.ViewGroup.LayoutParams.*
import android.widget.*

/**
 * "Ciepło-zimno": live signal of one BSSID. Walk where the number grows.
 * Android limits scans to about 4 per 2 minutes; for the demo turn off
 * "Wi-Fi scan throttling" in Developer options to get a reading every few seconds.
 */
class HotColdActivity : Activity() {
    private val radio by lazy { applicationContext.getSystemService(WIFI_SERVICE) as WifiManager }
    private val ticker = Handler(Looper.getMainLooper())
    private lateinit var bssid: String
    private lateinit var root: LinearLayout
    private lateinit var level: TextView
    private lateinit var hint: TextView
    private lateinit var bar: ProgressBar
    private lateinit var info: TextView
    private var previous: Int? = null
    private var lastStamp = -1L
    private var best: Int? = null

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        bssid = intent.getStringExtra("bssid").orEmpty().lowercase()
        val ssid = intent.getStringExtra("ssid").orEmpty().ifEmpty { "(ukryta sieć)" }
        root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER_HORIZONTAL
            setPadding(32, 48, 32, 32); fitsSystemWindows = true }
        root.addView(TextView(this).apply { text = "$ssid\n$bssid"; textSize = 18f; gravity = Gravity.CENTER })
        level = TextView(this).apply { text = "—"; textSize = 72f; gravity = Gravity.CENTER }
        hint = TextView(this).apply { text = "Szukam sygnału…"; textSize = 32f; gravity = Gravity.CENTER }
        bar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply { max = 100 }
        info = TextView(this).apply { textSize = 14f; gravity = Gravity.CENTER; setPadding(0, 24, 0, 0) }
        root.addView(level); root.addView(hint)
        root.addView(bar, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        root.addView(info)
        root.addView(Button(this).apply { text = "Wróć"; setOnClickListener { finish() } })
        for (i in 0 until root.childCount) (root.getChildAt(i) as? TextView)?.takeIf { it !is Button }?.setTextColor(Color.WHITE)
        root.setBackgroundColor(Color.rgb(60, 70, 220))
        setContentView(root)
    }

    override fun onResume() { super.onResume(); tick() }
    override fun onPause() { ticker.removeCallbacksAndMessages(null); super.onPause() }

    @Suppress("DEPRECATION")
    private fun tick() {
        val requested = runCatching { radio.startScan() }.getOrDefault(false)
        val ap = runCatching { radio.scanResults }.getOrDefault(emptyList()).firstOrNull { it.BSSID?.lowercase() == bssid }
        if (ap == null) {
            hint.text = "Brak sygnału"
            info.text = "Router nie pojawił się w ostatnim skanie. Wróć tam, gdzie był widoczny."
        } else if (ap.timestamp != lastStamp) {
            lastStamp = ap.timestamp
            val now = ap.level
            hint.text = trend(previous, now)
            previous = now
            best = maxOf(best ?: now, now)
            level.text = "$now dBm"
            bar.progress = signalPercent(now)
            // Blue when far, red when close.
            val p = signalPercent(now) / 100f
            root.setBackgroundColor(Color.rgb((60 + 160 * p).toInt(), 70, (220 - 160 * p).toInt()))
        }
        if (ap != null) {
            val age = (SystemClock.elapsedRealtime() - ap.timestamp / 1000L) / 1000
            info.text = "Odczyt sprzed $age s · najlepszy: ${best ?: "—"} dBm · kanał ${wifiChannel(ap.frequency) ?: "?"}" +
                if (requested) "" else "\nAndroid ogranicza skany. Na demo wyłącz „Ograniczanie skanowania Wi-Fi” w opcjach programisty."
        }
        ticker.postDelayed(::tick, 4000)
    }
}
