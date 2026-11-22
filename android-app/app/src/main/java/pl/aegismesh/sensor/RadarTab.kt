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
 * is (weighted centre and an uncertainty circle). Pings stay on the phone; "Eksport CSV" saves them.
 *
 * "Zapamiętaj wszystkie jako znane" saves the routers on the AEGIS server, shared by the whole team. After
 * that the phone reports what it hears (at most once a minute) and the server raises red flags as alerts.
 */
internal class RadarTab(private val host: MainActivity) : Tab {
    private val context = host
    private val dp = context.resources.displayMetrics.density
    private val radio = context.applicationContext.getSystemService(WifiManager::class.java)
    private val gps = context.getSystemService(LocationManager::class.java)
    private val ticker = Handler(Looper.getMainLooper())
    private var lastScan = 0L
    /** The newest fix from any source, possibly old (Android keeps the last one, even from another city). */
    private var lastFix: Location? = null
    /** Only a fix from the last 2 minutes counts: an old one is never drawn, saved or reported. */
    private val location: Location? get() = lastFix?.takeIf { ageSeconds(it) < 120 }

    private fun ageSeconds(l: Location) = (SystemClock.elapsedRealtimeNanos() - l.elapsedRealtimeNanos) / 1_000_000_000L
    private val prefs = context.getSharedPreferences("aegis", Context.MODE_PRIVATE)
    private var collecting = prefs.getBoolean("radar_collect", true)
    private var mapReady = false
    private var drawnPings = -1
    private val pings: MutableList<Ping> by lazy { PingStore.load(context) }
    private var estimates: Map<String, Estimate> = emptyMap()
    /** The router picked in the list or on the map: drawn red and bigger until another one is picked. */
    private var selected: String? = null
    /** The team's known routers from the server (empty when offline or not logged in). */
    private var serverKnown: List<ServerRouter> = emptyList()
    private var lastReport = 0L

    private class ServerRouter(val ap: KnownAp, val lat: Double?, val lon: Double?, val radius: Double?)

    private val summary = TextView(context).apply { ink(Palette.MUTED); textSize = 13f; setPadding(8, 4, 8, 4) }
    private val collect = Button(context).apply { setOnClickListener { toggleCollecting() } }
    private val rememberAll = Button(context).apply {
        text = "Zapamiętaj wszystkie jako znane"; gold(outline = true)
        setOnClickListener { confirmRemember() }
    }
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
        addJavascriptInterface(object {
            @android.webkit.JavascriptInterface
            fun select(bssid: String?) { post { choose(bssid, fromMap = true) } }
        }, "AegisApp")
        loadUrl("file:///android_asset/map/radar.html")
    }

    private class Row(val bssid: String, val seen: SeenAp?, val verdict: Verdict, val estimate: Estimate?)

    private val adapter = object : ArrayAdapter<Row>(context, android.R.layout.simple_list_item_2, android.R.id.text1, mutableListOf()) {
        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val view = super.getView(position, convertView, parent)
            val row = getItem(position)!!
            view.setBackgroundColor(if (row.bssid == selected) 0x33E53935 else 0)
            val ssid = (row.seen?.ssid ?: row.estimate?.ssid).orEmpty().ifEmpty { "(ukryta sieć)" }
            val signal = row.seen?.let { "${it.rssi} dBm" } ?: "poza zasięgiem"
            view.findViewById<TextView>(android.R.id.text1).apply {
                text = (if (row.bssid == selected) "◉ " else "● ") + "${row.verdict.status.label} · $ssid · $signal"
                setTextColor(if (row.bssid == selected) SELECTED else Palette.status(row.verdict.status))
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
        setOnItemClickListener { _, _, position, _ -> val row = this@RadarTab.adapter.getItem(position)!!; choose(row.bssid); details(row) }
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
        if (host.session.canEditFindings) addView(rememberAll, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { setMargins(4, 2, 4, 2) })
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
            val old = location   // a stale fix is always replaced
            // keep the better fix when a coarse network fix arrives right after a GPS one; after 5 s any newer fix wins,
            // so the dot follows the phone while walking
            if (old == null || l.provider == LocationManager.GPS_PROVIDER || l.accuracy <= old.accuracy ||
                l.elapsedRealtimeNanos - old.elapsedRealtimeNanos > 5_000_000_000L) lastFix = l
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
        syncKnown()
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
            runCatching { gps.requestLocationUpdates(provider, 1_000L, 0f, listener, Looper.getMainLooper()) }
            // ask for a fresh fix right away instead of waiting for the next update
            if (android.os.Build.VERSION.SDK_INT >= 30)
                runCatching { gps.getCurrentLocation(provider, null, context.mainExecutor) { l -> if (l != null) listener.onLocationChanged(l) } }
            runCatching { gps.getLastKnownLocation(provider) }.getOrNull()?.let { l ->
                if (lastFix.let { it == null || l.elapsedRealtimeNanos > it.elapsedRealtimeNanos }) lastFix = l
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
                         else "Zapisano ${heard.size} pingów jako puste kółka. GPS ±${fix.accuracy.toInt()} m to za mało (potrzeba ±${MAX_ACCURACY_M.toInt()} m), więc nie przesuwają routerów.")
            } else host.say("Czekam na świeżą pozycję GPS, ten skan nie został zapisany.")
        } else if (fix == null) host.say("Brak pozycji GPS: włącz lokalizację i podejdź do okna.")
        show()
        report()
    }

    @Suppress("DEPRECATION")
    private fun visible(): List<SeenAp> = runCatching {
        radio.scanResults.filter { it.BSSID != null }.map { SeenAp(it.SSID.orEmpty(), it.BSSID.lowercase(), it.level, it.frequency, it.capabilities.orEmpty()) }
    }.getOrDefault(emptyList())

    // ---- screen ------------------------------------------------------------------------

    private fun show() {
        val known = loadKnown(context) + serverKnown.map { it.ap }
        val seen = visible().associateBy { it.bssid }
        estimates = estimate(pings).associateBy { it.bssid }
        val rows = (seen.keys + estimates.keys).map { bssid ->
            val ap = seen[bssid] ?: estimates.getValue(bssid).last.ap
            Row(bssid, seen[bssid], classify(ap, known), estimates[bssid])
        }.sortedWith(compareBy<Row>({ it.verdict.status.rank }, { it.seen == null }, { -(it.seen?.rssi ?: -200) }))
        adapter.clear(); adapter.addAll(rows)

        val counts = rows.groupingBy { it.verdict.status }.eachCount()
        val fix = location?.let { weakGps(it)?.let { w -> "⚠ $w" } ?: "GPS ±${it.accuracy.toInt()} m (${it.provider}): dobry" } ?: "szukam lokalizacji…"
        summary.text = Status.values().joinToString(" · ") { "${it.label}: ${counts[it] ?: 0}" } +
            "\nWidoczne teraz: ${seen.size} · pingi: ${pings.size} · routery na mapie: ${estimates.size} · znane (serwer): ${serverKnown.size}" +
            "\n$fix · zapis: ${if (collecting) "włączony" else "wyłączony"}"
        pushMap(rows)
    }

    private fun pushMap(rows: List<Row>? = null) {
        if (!mapReady) return
        val data = JSONObject()
        location?.let { data.put("me", JSONObject().put("lat", it.latitude).put("lon", it.longitude).put("acc", it.accuracy.toDouble())) }
        if (location == null) data.put("waiting", lastFix?.let { "Szukam aktualnej lokalizacji… Ostatnia pozycja jest sprzed ${ageSeconds(it) / 60} min, więc jej nie pokazuję." }
            ?: "Szukam lokalizacji… Wyjdź bliżej okna albo na zewnątrz.")
        location?.let(::weakGps)?.let { data.put("weak", it) }
        data.put("selected", selected ?: JSONObject.NULL)
        val routers = JSONArray()
        val list = rows ?: (0 until adapter.count).map { adapter.getItem(it)!! }
        list.forEach { row ->
            val e = row.estimate ?: return@forEach
            val name = e.ssid.ifEmpty { "(ukryta sieć)" }
            routers.put(JSONObject().put("id", e.bssid).put("lat", e.lat).put("lon", e.lon).put("r", e.radius)
                .put("color", hex(Palette.status(row.verdict.status)))
                .put("label", "$name · ±${e.radius.toInt()} m · pewność ${e.confidence}"))
        }
        // known routers from the team's map that this phone has no pings for
        serverKnown.filter { it.lat != null && it.lon != null && it.ap.bssid !in estimates }.forEach { k ->
            routers.put(JSONObject().put("id", k.ap.bssid).put("lat", k.lat).put("lon", k.lon).put("r", k.radius ?: 15.0)
                .put("color", hex(Palette.status(Status.KNOWN)))
                .put("label", "${k.ap.ssid.ifEmpty { "(ukryta sieć)" }} · znany (mapa drużyny)"))
        }
        data.put("routers", routers)
        val devices = JSONArray()
        location?.let { me ->
            list.filter { it.seen != null }.forEach { row ->
                val ap = row.seen!!
                val (lat, lon) = devicePosition(me.latitude, me.longitude, ap.bssid, ap.rssi)
                devices.put(JSONObject().put("id", ap.bssid).put("lat", lat).put("lon", lon).put("rssi", ap.rssi)
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

    // ---- the team's known routers on the server, and red flags ------------------------------

    /** Reads the known routers from the server; offline the last list stays. */
    private fun syncKnown() {
        if (!host.session.loggedIn) return
        host.work({ host.session.api().get("/api/wifi/known") }) { result ->
            result.onSuccess { data ->
                fun num(o: JSONObject, key: String) = if (o.isNull(key)) null else o.optDouble(key).takeIf { !it.isNaN() }
                serverKnown = data.optJSONArray("routers").objects().map { o ->
                    ServerRouter(KnownAp(o.optString("ssid"), o.optString("bssid"), o.text("security").orEmpty()),
                        num(o, "lat"), num(o, "lon"), num(o, "radius"))
                }
                show()
            }
        }
    }

    private fun confirmRemember() {
        val rows = (0 until adapter.count).map { adapter.getItem(it)!! }
        if (rows.isEmpty()) { host.say("Nie widzę jeszcze żadnych routerów. Poczekaj na skan."); return }
        AlertDialog.Builder(context, android.R.style.Theme_DeviceDefault_Dialog_Alert)
            .setTitle("Zapamiętać ${rows.size} routerów jako znane?")
            .setMessage("Zapiszę je na serwerze AEGIS z położeniem, nazwą, kanałem i siłą sygnału. Cała drużyna zobaczy tę samą listę.\n\n" +
                "Potem każdy nowy router albo hotspot, znana nazwa sieci na obcym urządzeniu (evil twin) oraz znany router, " +
                "który zniknął albo się przesunął, da czerwoną flagę w zakładce Alerty.")
            .setPositiveButton("Zapamiętaj") { _, _ -> remember(rows) }
            .setNegativeButton("Anuluj", null).show()
    }

    private fun remember(rows: List<Row>) {
        val fix = location?.takeIf { it.accuracy <= MAX_ACCURACY_M }
        val routers = JSONArray()
        rows.forEach { row ->
            val ap = row.seen ?: row.estimate?.last?.ap ?: return@forEach
            val o = JSONObject().put("bssid", row.bssid).put("ssid", ap.ssid).put("security", securityMode(ap.flags))
                .put("frequency", ap.frequency).put("rssi", row.seen?.rssi ?: row.estimate?.bestRssi)
            val e = row.estimate
            if (e != null) o.put("lat", e.lat).put("lon", e.lon).put("radius", e.radius)
            else if (fix != null && row.seen != null)   // no pings yet: the phone's spot, with the distance from the signal
                o.put("lat", fix.latitude).put("lon", fix.longitude).put("radius", rssiDistance(row.seen.rssi) + fix.accuracy)
            routers.put(o)
        }
        rememberAll.isEnabled = false
        host.work({ host.session.api().post("/api/wifi/known", JSONObject().put("routers", routers)) }) { result ->
            rememberAll.isEnabled = true
            result.onSuccess {
                host.say("Zapamiętano ${it.optInt("saved")} routerów na serwerze (nowych: ${it.optInt("added")}). Od teraz obce sieci dadzą czerwoną flagę.")
                syncKnown()
            }.onFailure(host::fail)
        }
    }

    /** After a scan: tells the server what the phone hears, at most once a minute. New red flags are announced. */
    private fun report() {
        if (!host.session.loggedIn || !host.session.canEditFindings || serverKnown.isEmpty()) return
        if (SystemClock.elapsedRealtime() - lastReport < 60_000) return
        val heard = visible()
        if (heard.isEmpty()) return
        lastReport = SystemClock.elapsedRealtime()
        val known = loadKnown(context) + serverKnown.map { it.ap }
        val aps = JSONArray()
        heard.forEach { ap ->
            val o = JSONObject().put("bssid", ap.bssid).put("ssid", ap.ssid).put("security", securityMode(ap.flags))
                .put("frequency", ap.frequency).put("rssi", ap.rssi).put("hotspot", classify(ap, known).status == Status.HOTSPOT)
            estimates[ap.bssid]?.let { e -> o.put("lat", e.lat).put("lon", e.lon).put("radius", e.radius) }
            aps.put(o)
        }
        val body = JSONObject().put("aps", aps)
        location?.takeIf { SystemClock.elapsedRealtimeNanos() - it.elapsedRealtimeNanos < 120_000_000_000L }?.let {
            body.put("position", JSONObject().put("lat", it.latitude).put("lon", it.longitude).put("acc", it.accuracy.toDouble()))
        }
        host.work({ host.session.api().post("/api/wifi/report", body) }) { result ->
            result.onSuccess { data ->
                val flags = data.optJSONArray("flags").objects()
                if (flags.isNotEmpty()) host.say(if (flags.size == 1) "⚑ Czerwona flaga: ${flags[0].optString("title")}. Zobacz Alerty."
                                                 else "⚑ ${flags.size} nowe czerwone flagi Wi-Fi. Zobacz Alerty.")
            }
        }
    }

    /** GPS too coarse for router positions: the words for the map and the summary, or null when it is fine. */
    private fun weakGps(l: Location): String? = if (l.accuracy <= MAX_ACCURACY_M) null else
        "GPS ±${l.accuracy.toInt()} m (${l.provider}): za słaby, potrzeba ±${MAX_ACCURACY_M.toInt()} m. Pingi się zapisują, ale nie liczą się do położenia. Podejdź do okna."

    /** Marks one router: red and bigger on the map, highlighted in the list. The previous one goes back to normal. */
    private fun choose(bssid: String?, fromMap: Boolean = false) {
        selected = bssid
        adapter.notifyDataSetChanged()
        if (fromMap) {
            val at = (0 until adapter.count).firstOrNull { adapter.getItem(it)!!.bssid == bssid }
            if (at != null) list.smoothScrollToPosition(at)
            val name = estimates[bssid]?.ssid ?: visible().firstOrNull { it.bssid == bssid }?.ssid
            host.say("Zaznaczono: ${name.orEmpty().ifEmpty { "(ukryta sieć)" }}")
        } else pushMap()
    }

    private fun js(code: String) { if (mapReady) map.evaluateJavascript(code, null) }

    private val SELECTED = 0xFFE53935.toInt()

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
            .apply { if (e != null) setPositiveButton("Pokaż na mapie") { _, _ -> js("aegis.focus(${e.lat}, ${e.lon}, ${JSONObject.quote(row.bssid)})") } }
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
