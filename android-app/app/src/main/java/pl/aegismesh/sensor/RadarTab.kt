package pl.aegismesh.sensor

import android.Manifest.permission.ACCESS_FINE_LOCATION
import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager.PERMISSION_GRANTED
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.net.wifi.WifiManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.View
import android.view.ViewGroup
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.*
import org.json.JSONArray
import org.json.JSONObject

/**
 * Tab 3: the Wi-Fi radar on a map. OpenStreetMap (Leaflet, bundled in assets/map) with the phone's GPS
 * position. After every Wi-Fi scan the radar beam turns 1.5 times. With "Zbieraj" on, every router heard
 * is saved as a ping (router + signal + GPS position); from all pings the app estimates where each router
 * is (weighted centre and an uncertainty circle). Everything stays on the phone; "Eksport CSV" saves it.
 */
internal class RadarTab(private val host: MainActivity) : Tab {
    private val context = host
    private val dp = context.resources.displayMetrics.density
    private val radio = context.applicationContext.getSystemService(WifiManager::class.java)
    private val gps = context.getSystemService(LocationManager::class.java)
    private val ticker = Handler(Looper.getMainLooper())
    private var lastScan = 0L
    private var location: Location? = null
    private val prefs = context.getSharedPreferences("aegis", Context.MODE_PRIVATE)
    private var collecting = prefs.getBoolean("radar_collect", true)
    private var mapReady = false
    private var drawnPings = -1
    private val pings: MutableList<Ping> by lazy { PingStore.load(context) }
    private var estimates: Map<String, Estimate> = emptyMap()

    private val summary = TextView(context).apply { ink(Palette.MUTED); textSize = 13f; setPadding(8, 4, 8, 4) }
    private val collect = Button(context).apply { setOnClickListener { toggleCollecting() } }
    private val enableGps = Button(context).apply {
        text = "Włącz lokalizację w telefonie"; gold(); visibility = View.GONE
        setOnClickListener { context.startActivity(Intent(android.provider.Settings.ACTION_LOCATION_SOURCE_SETTINGS)) }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private val map = WebView(context).apply {
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.userAgentString = settings.userAgentString + " AegisMesh/1.0 (radar Wi-Fi; github.com/PI0B0R/AEGIS-MESH)"
        setBackgroundColor(Palette.BLACK)
        webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = true
            override fun onPageFinished(view: WebView, url: String) { mapReady = true; drawnPings = -1; pushMap() }
        }
        loadUrl("file:///android_asset/map/radar.html")
    }

    private class Row(val bssid: String, val seen: SeenAp?, val verdict: Verdict, val estimate: Estimate?)

    private val adapter = object : ArrayAdapter<Row>(context, android.R.layout.simple_list_item_2, android.R.id.text1, mutableListOf()) {
        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val view = super.getView(position, convertView, parent)
            val row = getItem(position)!!
            val ssid = (row.seen?.ssid ?: row.estimate?.ssid).orEmpty().ifEmpty { "(ukryta sieć)" }
            val signal = row.seen?.let { "${it.rssi} dBm" } ?: "poza zasięgiem"
            view.findViewById<TextView>(android.R.id.text1).apply {
                text = "● ${row.verdict.status.label} · $ssid · $signal"
                setTextColor(Palette.status(row.verdict.status))
            }
            view.findViewById<TextView>(android.R.id.text2).apply {
                text = row.estimate?.let { "${it.pings} pingów z ${it.places} miejsc · położenie ±${it.radius.toInt()} m · pewność: ${it.confidence}" }
                    ?: "${row.bssid} · brak pingów: włącz Zbieraj i przejdź się"
                setTextColor(Palette.MUTED)
            }
            return view
        }
    }
    private val list = ListView(context).apply {
        adapter = this@RadarTab.adapter
        divider = android.graphics.drawable.ColorDrawable(Palette.GOLD_DIM); dividerHeight = 1
        setOnItemClickListener { _, _, position, _ -> details(this@RadarTab.adapter.getItem(position)!!) }
    }

    override val view: View = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        fun button(label: String, action: () -> Unit) = Button(context).apply { text = label; gold(outline = true); setOnClickListener { action() } }
        fun weighted(@Suppress("UNUSED_PARAMETER") v: View) = LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f).apply { setMargins(4, 2, 4, 2) }
        addView(LinearLayout(context).apply {
            val b1 = collect; val b2 = button("Wyśrodkuj") { js("aegis.center()") }; val b3 = button("Znane routery") { importKnown() }
            addView(b1, weighted(b1)); addView(b2, weighted(b2)); addView(b3, weighted(b3))
        })
        addView(LinearLayout(context).apply {
            val b1 = button("Eksport CSV") { exportCsv() }; val b2 = button("Wyczyść") { confirmClear() }
            addView(b1, weighted(b1)); addView(b2, weighted(b2))
        })
        addView(enableGps, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { setMargins(4, 2, 4, 2) })
        addView(summary)
        addView(map, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1.7f).apply { setMargins(0, 4, 0, 4) })
        addView(list, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))
    }

    init { showCollecting() }

    // ---- Wi-Fi scans and GPS -------------------------------------------------------------

    private val scanDone = object : BroadcastReceiver() {
        override fun onReceive(c: Context, intent: Intent) = onScan()
    }
    private var receiving = false

    private val listener = object : LocationListener {
        override fun onLocationChanged(l: Location) {
            val old = location
            // keep the better fix when a coarse network fix arrives right after a GPS one
            if (old == null || l.provider == LocationManager.GPS_PROVIDER || l.accuracy <= old.accuracy ||
                l.elapsedRealtimeNanos - old.elapsedRealtimeNanos > 20_000_000_000L) location = l
            pushMap()
        }
        @Deprecated("old API") override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
        override fun onProviderEnabled(provider: String) {}
        override fun onProviderDisabled(provider: String) {}
    }

    @SuppressLint("MissingPermission")
    override fun refresh() {
        ticker.removeCallbacksAndMessages(null)
        if (context.checkSelfPermission(ACCESS_FINE_LOCATION) != PERMISSION_GRANTED) {
            summary.text = "Radar potrzebuje zgody na lokalizację: bez niej Android nie pokazuje sieci Wi-Fi ani pozycji GPS."
            host.askWifiPermissions()
            return
        }
        startLocation()
        if (!receiving) {
            val filter = IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION)
            if (android.os.Build.VERSION.SDK_INT >= 33) context.registerReceiver(scanDone, filter, Context.RECEIVER_NOT_EXPORTED)
            else context.registerReceiver(scanDone, filter)
            receiving = true
        }
        tick()
    }

    private var listening = false

    /**
     * Location from every source the phone has switched on (GPS, network, fused), so the map finds the
     * phone indoors too. With location switched off, a button opens the phone's location settings.
     */
    @SuppressLint("MissingPermission")
    private fun startLocation() {
        val sources = runCatching { gps.getProviders(true) }.getOrDefault(emptyList<String>()).filter { it != LocationManager.PASSIVE_PROVIDER }
        enableGps.visibility = if (sources.isEmpty()) View.VISIBLE else View.GONE
        if (listening || sources.isEmpty()) return
        for (provider in sources) {
            runCatching { gps.requestLocationUpdates(provider, 2_000L, 0f, listener, Looper.getMainLooper()) }
            runCatching { gps.getLastKnownLocation(provider) }.getOrNull()?.let { l ->
                if (location.let { it == null || l.time > it.time }) location = l
            }
        }
        listening = true
    }

    /** Every 5 s: redraw from the cached scan; ask Android for a new scan at most every 30 s (its own limit is 4 per 2 min). */
    @Suppress("DEPRECATION")
    private fun tick() {
        if (SystemClock.elapsedRealtime() - lastScan > 30_000) { lastScan = SystemClock.elapsedRealtime(); radio.startScan() }
        if (!listening) startLocation()
        show()
        ticker.postDelayed(::tick, 5_000)
    }

    override fun stop() {
        ticker.removeCallbacksAndMessages(null)
        if (receiving) {
            runCatching { context.unregisterReceiver(scanDone) }
            receiving = false
        }
        if (listening) {
            runCatching { gps.removeUpdates(listener) }
            listening = false
        }
    }

    /**
     * A new scan arrived: turn the beam and save one ping per router heard (while "Zbieraj" is on, the default).
     * Pings with weak GPS are kept and drawn hollow, but do not count for the router position.
     */
    private fun onScan() {
        js("aegis.sweep()")
        val fix = location
        if (collecting && fix != null) {
            val ageNanos = SystemClock.elapsedRealtimeNanos() - fix.elapsedRealtimeNanos
            if (ageNanos < 120_000_000_000L) {
                val now = System.currentTimeMillis()
                val heard = visible().map { Ping(now, fix.latitude, fix.longitude, fix.accuracy, it.bssid, it.ssid, it.rssi, it.frequency, it.flags) }
                pings += heard
                runCatching { PingStore.append(context, heard) }
                host.say(if (fix.accuracy <= MAX_ACCURACY_M) "Zapisano ${heard.size} pingów (GPS ±${fix.accuracy.toInt()} m)."
                         else "Zapisano ${heard.size} pingów, ale GPS jest słaby (±${fix.accuracy.toInt()} m): nie liczą się do położenia routerów.")
            } else host.say("Czekam na świeżą pozycję GPS, ten skan nie został zapisany.")
        } else if (fix == null) host.say("Brak pozycji GPS: włącz lokalizację i podejdź do okna.")
        show()
    }

    @Suppress("DEPRECATION")
    private fun visible(): List<SeenAp> = runCatching {
        radio.scanResults.filter { it.BSSID != null }.map { SeenAp(it.SSID.orEmpty(), it.BSSID.lowercase(), it.level, it.frequency, it.capabilities.orEmpty()) }
    }.getOrDefault(emptyList())

    // ---- screen ------------------------------------------------------------------------

    private fun show() {
        val known = loadKnown(context)
        val seen = visible().associateBy { it.bssid }
        estimates = estimate(pings).associateBy { it.bssid }
        val rows = (seen.keys + estimates.keys).map { bssid ->
            val ap = seen[bssid] ?: estimates.getValue(bssid).last.ap
            Row(bssid, seen[bssid], classify(ap, known), estimates[bssid])
        }.sortedWith(compareBy<Row>({ it.verdict.status.rank }, { it.seen == null }, { -(it.seen?.rssi ?: -200) }))
        adapter.clear(); adapter.addAll(rows)

        val counts = rows.groupingBy { it.verdict.status }.eachCount()
        val fix = location?.let { "GPS ±${it.accuracy.toInt()} m (${it.provider})" } ?: "brak pozycji GPS"
        summary.text = Status.values().joinToString(" · ") { "${it.label}: ${counts[it] ?: 0}" } +
            "\nWidoczne teraz: ${seen.size} · pingi: ${pings.size} · routery na mapie: ${estimates.size}\n$fix · zapis: ${if (collecting) "włączony" else "wyłączony"}"
        pushMap(rows)
    }

    private fun pushMap(rows: List<Row>? = null) {
        if (!mapReady) return
        val data = JSONObject()
        location?.let { data.put("me", JSONObject().put("lat", it.latitude).put("lon", it.longitude).put("acc", it.accuracy.toDouble())) }
        if (location == null) data.put("waiting", "Czekam na GPS… Wyjdź bliżej okna albo na zewnątrz.")
        val routers = JSONArray()
        val list = rows ?: (0 until adapter.count).map { adapter.getItem(it)!! }
        list.forEach { row ->
            val e = row.estimate ?: return@forEach
            val name = e.ssid.ifEmpty { "(ukryta sieć)" }
            routers.put(JSONObject().put("lat", e.lat).put("lon", e.lon).put("r", e.radius)
                .put("color", hex(Palette.status(row.verdict.status)))
                .put("label", "$name · ±${e.radius.toInt()} m · pewność ${e.confidence}"))
        }
        data.put("routers", routers)
        val devices = JSONArray()
        location?.let { me ->
            list.filter { it.seen != null }.forEach { row ->
                val ap = row.seen!!
                val (lat, lon) = devicePosition(me.latitude, me.longitude, ap.bssid, ap.rssi)
                devices.put(JSONObject().put("lat", lat).put("lon", lon).put("rssi", ap.rssi)
                    .put("color", hex(Palette.status(row.verdict.status)))
                    .put("label", "${ap.ssid.ifEmpty { "(ukryta sieć)" }} · ${ap.rssi} dBm · ok. ${rssiDistance(ap.rssi).toInt()} m"))
            }
        }
        data.put("devices", devices)
        js("aegis.update($data)")
        if (drawnPings != pings.size) {
            drawnPings = pings.size
            val dots = JSONArray()
            pings.takeLast(3_000).forEach { dots.put(JSONArray().put(it.lat).put(it.lon).put(it.rssi).put(if (it.accuracy <= MAX_ACCURACY_M) 1 else 0)) }
            js("aegis.pings($dots)")
        }
    }

    private fun js(code: String) { if (mapReady) map.evaluateJavascript(code, null) }

    private fun hex(color: Int) = String.format("#%06X", 0xFFFFFF and color)

    private fun toggleCollecting() {
        collecting = !collecting
        prefs.edit().putBoolean("radar_collect", collecting).apply()
        showCollecting()
        if (collecting) {
            lastScan = 0L
            host.say("Zapisuję pingi. Chodź powoli po budynku; skan co ok. 30 s.")
            ticker.removeCallbacksAndMessages(null); if (receiving) tick()
        } else host.say("Zbieranie zatrzymane. Pingi są zapisane na telefonie.")
    }

    private fun showCollecting() {
        collect.text = if (collecting) "■ Stop zapisu" else "▶ Zbieraj"
        collect.gold(outline = !collecting)
    }

    private fun details(row: Row) {
        val ap = row.seen ?: row.estimate?.last?.ap
        val e = row.estimate
        val text = listOfNotNull(
            row.bssid,
            ap?.let { "Zabezpieczenie: ${securityMode(it.flags)} · ${it.frequency} MHz" },
            row.seen?.let { "Sygnał teraz: ${it.rssi} dBm" } ?: "Teraz poza zasięgiem",
            row.verdict.reason,
            e?.let { "Pingi: ${it.pings} z ${it.places} miejsc · najlepszy sygnał ${it.bestRssi} dBm\nPołożenie: ±${it.radius.toInt()} m, pewność ${it.confidence}" }
                ?: "Brak pingów. Włącz „Zbieraj” i przejdź się w pobliżu.",
        ).joinToString("\n\n")
        AlertDialog.Builder(context, android.R.style.Theme_DeviceDefault_Dialog_Alert)
            .setTitle((ap?.ssid ?: "").ifEmpty { "(ukryta sieć)" })
            .setMessage(text)
            .apply { if (e != null) setPositiveButton("Pokaż na mapie") { _, _ -> js("aegis.focus(${e.lat}, ${e.lon})") } }
            .setNeutralButton("Ciepło-zimno") { _, _ -> open(row.bssid, ap?.ssid.orEmpty()) }
            .setNegativeButton("Zamknij", null).show()
    }

    private fun open(bssid: String, ssid: String) = context.startActivity(Intent(context, HotColdActivity::class.java)
        .putExtra("bssid", bssid).putExtra("ssid", ssid))

    private fun importKnown() = host.startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
        .setType("*/*").putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("text/csv", "text/comma-separated-values", "text/plain")), MainActivity.PICK_KNOWN)

    private fun exportCsv() = host.startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
        .setType("text/csv").putExtra(Intent.EXTRA_TITLE, "aegis-pingi.csv"), MainActivity.EXPORT_PINGS)

    private fun confirmClear() {
        AlertDialog.Builder(context, android.R.style.Theme_DeviceDefault_Dialog_Alert)
            .setTitle("Usunąć wszystkie pingi?")
            .setMessage("Zebrane pingi (${pings.size}) i położenia routerów znikną z telefonu. Najpierw możesz zrobić Eksport CSV.")
            .setPositiveButton("Usuń") { _, _ -> PingStore.clear(context); pings.clear(); host.say("Pingi usunięte."); show() }
            .setNegativeButton("Anuluj", null).show()
    }
}
