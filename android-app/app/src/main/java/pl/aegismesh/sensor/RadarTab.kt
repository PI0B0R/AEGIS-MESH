package pl.aegismesh.sensor

import android.Manifest.permission.ACCESS_FINE_LOCATION
import android.content.Intent
import android.content.pm.PackageManager.PERMISSION_GRANTED
import android.net.wifi.WifiManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.View
import android.view.ViewGroup
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.*

/**
 * Tab 3: the Wi-Fi radar, a demo extra. Nearby access points compared with an optional
 * list of known routers (CSV). Everything stays on the phone; nothing is sent to the server.
 * Tapping a router opens the hot-cold finder.
 */
internal class RadarTab(private val host: MainActivity) : Tab {
    private val context = host
    private val radio = context.applicationContext.getSystemService(WifiManager::class.java)
    private val ticker = Handler(Looper.getMainLooper())
    private var lastScan = 0L
    private val summary = TextView(context).apply { ink(Palette.MUTED); setPadding(8, 8, 8, 8) }
    private val radar = RadarView(context).apply { onPing = { open(it.ap) } }
    private val adapter = object : ArrayAdapter<Pair<SeenAp, Verdict>>(context, android.R.layout.simple_list_item_2, android.R.id.text1, mutableListOf()) {
        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val view = super.getView(position, convertView, parent)
            val (ap, verdict) = getItem(position)!!
            view.findViewById<TextView>(android.R.id.text1).apply {
                text = "● ${verdict.status.label} · ${ap.ssid.ifEmpty { "(ukryta sieć)" }} · ${ap.rssi} dBm"
                setTextColor(Palette.status(verdict.status))
            }
            view.findViewById<TextView>(android.R.id.text2).apply {
                text = "${ap.bssid} · ${securityMode(ap.flags)} · ${verdict.reason}"; setTextColor(Palette.MUTED)
            }
            return view
        }
    }
    private val list = ListView(context).apply {
        adapter = this@RadarTab.adapter
        divider = android.graphics.drawable.ColorDrawable(Palette.GOLD_DIM); dividerHeight = 1
        setOnItemClickListener { _, _, position, _ -> open(this@RadarTab.adapter.getItem(position)!!.first) }
    }
    override val view: View = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        addView(LinearLayout(context).apply {
            gravity = android.view.Gravity.CENTER_VERTICAL
            addView(summary, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
            addView(Button(context).apply { text = "Znane routery"; gold(outline = true); setOnClickListener { importKnown() } })
        })
        addView(radar, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1.1f))
        addView(list, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))
    }

    private fun open(ap: SeenAp) = context.startActivity(Intent(context, HotColdActivity::class.java)
        .putExtra("bssid", ap.bssid).putExtra("ssid", ap.ssid))

    private fun importKnown() = host.startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
        .setType("*/*").putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("text/csv", "text/comma-separated-values", "text/plain")), MainActivity.PICK_KNOWN)

    /** Reads the cached scan every 5 s and asks Android for a new one at most every 30 s. */
    @Suppress("DEPRECATION")
    override fun refresh() {
        ticker.removeCallbacksAndMessages(null)
        if (context.checkSelfPermission(ACCESS_FINE_LOCATION) != PERMISSION_GRANTED) {
            summary.text = "Radar potrzebuje zgody na lokalizację: bez niej Android nie pokazuje sieci Wi-Fi."
            host.askWifiPermissions()
            return
        }
        if (SystemClock.elapsedRealtime() - lastScan > 30_000) { lastScan = SystemClock.elapsedRealtime(); radio.startScan() }
        val known = loadKnown(context)
        val rows = radio.scanResults.filter { it.BSSID != null }.map {
            val ap = SeenAp(it.SSID.orEmpty(), it.BSSID.lowercase(), it.level, it.frequency, it.capabilities.orEmpty())
            ap to classify(ap, known)
        }.sortedWith(compareBy<Pair<SeenAp, Verdict>>({ it.second.status.rank }, { -it.first.rssi }))
        adapter.clear(); adapter.addAll(rows)
        radar.pings = rows.map { RadarView.Ping(it.first, it.second.status) }
        val counts = rows.groupingBy { it.second.status }.eachCount()
        summary.text = Status.values().joinToString(" · ") { "${it.label}: ${counts[it] ?: 0}" } + "\nznane routery: ${known.size}"
        ticker.postDelayed(::refresh, 5000)
    }

    override fun stop() = ticker.removeCallbacksAndMessages(null)
}
