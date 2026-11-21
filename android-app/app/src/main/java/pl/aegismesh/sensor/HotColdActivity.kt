package pl.aegismesh.sensor

import android.app.Activity
import android.net.wifi.WifiManager
import android.os.*
import android.view.Gravity
import android.view.ViewGroup.LayoutParams.*
import android.widget.*

/**
 * Hot-cold radar for one BSSID. A cone points from the phone to the router:
 * wide and red when far, narrow and green when close. Tap the ping to scan every 2 s.
 *
 * Android allows only 4 Wi-Fi scans per 2 minutes per app. Fast mode asks every 2 s,
 * but new data arrives that often only with "Wi-Fi scan throttling" turned off in
 * Developer options. The screen says so when Android refuses a scan.
 */
class HotColdActivity : Activity() {
    private val radio by lazy { applicationContext.getSystemService(WIFI_SERVICE) as WifiManager }
    private val ticker = Handler(Looper.getMainLooper())
    private lateinit var bssid: String
    private lateinit var cone: ConeView
    private lateinit var level: TextView
    private lateinit var hint: TextView
    private lateinit var info: TextView
    private var smoothed: Double? = null
    private var lastShown: Int? = null
    private var lastStamp = -1L
    private var best: Int? = null
    private var fast = false
    private var refused = 0

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        bssid = intent.getStringExtra("bssid").orEmpty().lowercase()
        val ssid = intent.getStringExtra("ssid").orEmpty().ifEmpty { "(ukryta sieć)" }
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setBackgroundColor(Palette.BLACK)
            setPadding(24, 24, 24, 24); fitsSystemWindows = true }
        root.addView(TextView(this).apply { text = "RADAR · CIEPŁO-ZIMNO"; textSize = 14f; letterSpacing = 0.2f; ink(Palette.GOLD); gravity = Gravity.CENTER })
        root.addView(TextView(this).apply { text = "$ssid\n$bssid"; textSize = 16f; ink(); gravity = Gravity.CENTER })
        cone = ConeView(this).apply { onPingTap = { toggleFast() } }
        root.addView(cone, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))
        level = TextView(this).apply { text = "—"; textSize = 44f; ink(Palette.GOLD); gravity = Gravity.CENTER }
        hint = TextView(this).apply { text = "Szukam sygnału…"; textSize = 26f; ink(); gravity = Gravity.CENTER }
        info = TextView(this).apply { textSize = 13f; ink(Palette.MUTED); gravity = Gravity.CENTER; setPadding(0, 8, 0, 8) }
        root.addView(level); root.addView(hint); root.addView(info)
        root.addView(Button(this).apply { text = "Wróć"; gold(outline = true); setOnClickListener { finish() } },
            LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        setContentView(root)
        window.statusBarColor = Palette.BLACK
        window.navigationBarColor = Palette.BLACK
    }

    override fun onResume() { super.onResume(); tick() }
    override fun onPause() { ticker.removeCallbacksAndMessages(null); super.onPause() }

    private fun toggleFast() {
        fast = !fast; cone.fast = fast
        ticker.removeCallbacksAndMessages(null); tick()
    }

    @Suppress("DEPRECATION")
    private fun tick() {
        val requested = runCatching { radio.startScan() }.getOrDefault(false)
        if (!requested) refused++ else refused = 0
        val ap = runCatching { radio.scanResults }.getOrDefault(emptyList()).firstOrNull { it.BSSID?.lowercase() == bssid }
        if (ap == null) {
            hint.text = "Brak sygnału"; hint.ink(Palette.RED)
            info.text = "Router nie pojawił się w ostatnim skanie. Wróć tam, gdzie był widoczny."
        } else if (ap.timestamp != lastStamp) {
            lastStamp = ap.timestamp
            val value = smooth(smoothed, ap.level).also { smoothed = it }
            val shown = Math.round(value).toInt()
            val word = trend(lastShown, shown)
            hint.text = word
            hint.ink(when (word) { "Cieplej" -> Palette.GREEN; "Zimniej" -> Palette.RED; else -> Palette.TEXT })
            lastShown = shown
            best = maxOf(best ?: ap.level, ap.level)
            level.text = "$shown dBm"
            level.ink(Palette.heat(distanceFactor(value)))
            cone.show(value)
        }
        if (ap != null) {
            val age = (SystemClock.elapsedRealtime() - ap.timestamp / 1000L) / 1000
            info.text = "Odczyt sprzed $age s · surowy ${ap.level} dBm · najlepszy ${best ?: "—"} dBm" +
                (if (fast) " · ping co 2 s" else " · ping co 5 s") +
                if (refused >= 2) "\nAndroid ogranicza skany (4 na 2 min). Wyłącz „Ograniczanie skanowania Wi-Fi” w opcjach programisty." else ""
        }
        ticker.postDelayed(::tick, if (fast) 2000 else 5000)
    }
}
