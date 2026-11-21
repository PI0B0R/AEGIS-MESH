package pl.aegismesh.sensor

import android.Manifest.permission.*
import android.app.Activity
import android.app.AlertDialog
import android.content.*
import android.content.pm.PackageManager.PERMISSION_GRANTED
import android.graphics.Typeface
import android.graphics.drawable.ColorDrawable
import android.net.http.SslCertificate
import android.net.http.SslError
import android.net.wifi.WifiManager
import android.os.*
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.ViewGroup.LayoutParams.*
import android.webkit.*
import android.widget.*
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import org.json.JSONObject

/**
 * Two states, nothing else on screen:
 *  - not paired: one big button "Zeskanuj kod z laptopa";
 *  - paired: radar + list of nearby routers + one big Start/Stop button.
 * Everything else (place, file import, laptop panel, disconnect) lives under "⋯ Więcej".
 */
class MainActivity : Activity(), SharedPreferences.OnSharedPreferenceChangeListener {
    private val prefs by lazy { getSharedPreferences("sensor", MODE_PRIVATE) }
    private val radio by lazy { applicationContext.getSystemService(WIFI_SERVICE) as WifiManager }
    private val ticker = Handler(Looper.getMainLooper())
    private lateinit var welcome: LinearLayout
    private lateinit var live: LinearLayout
    private lateinit var page: WebView
    private lateinit var list: ListView
    private lateinit var radar: RadarView
    private lateinit var status: TextView
    private lateinit var summary: TextView
    private lateinit var toggle: Button
    private lateinit var scan: Button
    private lateinit var adapter: ArrayAdapter<Row>
    private var pairing = false
    private var startAfterGrant = false
    private var lastOwnScan = 0L
    private val permissions get() = mutableListOf(ACCESS_COARSE_LOCATION, ACCESS_FINE_LOCATION).also {
        if (Build.VERSION.SDK_INT >= 33) { it.add(NEARBY_WIFI_DEVICES); it.add(POST_NOTIFICATIONS) }
    }
    private val paired get() = prefs.contains("key")

    private class Row(val ap: SeenAp, val verdict: Verdict)

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        window.statusBarColor = Palette.BLACK
        window.navigationBarColor = Palette.BLACK
        val frame = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; fitsSystemWindows = true
            setPadding(16, 12, 16, 16); setBackgroundColor(Palette.BLACK) }

        // Header: brand + the only secondary entry point.
        val header = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        header.addView(TextView(this).apply { text = "◆ AEGIS MESH"; textSize = 20f; letterSpacing = 0.15f
            setTypeface(typeface, Typeface.BOLD); ink(Palette.GOLD) }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        header.addView(Button(this).apply { text = "⋯ Więcej"; gold(outline = true); setOnClickListener { more() } })
        frame.addView(header)

        // State 1: not paired.
        welcome = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER; setPadding(24, 0, 24, 0) }
        welcome.addView(TextView(this).apply { text = "◎"; textSize = 96f; ink(Palette.GOLD); gravity = Gravity.CENTER })
        welcome.addView(TextView(this).apply { text = "Połącz z laptopem"; textSize = 26f; setTypeface(typeface, Typeface.BOLD)
            ink(Palette.GOLD); gravity = Gravity.CENTER })
        welcome.addView(TextView(this).apply { textSize = 16f; ink(); gravity = Gravity.CENTER; setPadding(0, 16, 0, 32)
            text = "Uruchom AEGIS na laptopie. Na jego stronie zobaczysz kod QR „Połącz”.\nZeskanuj go, a czujnik włączy się sam." })
        scan = Button(this).apply { text = "Zeskanuj kod z laptopa"; textSize = 20f; gold(); setPadding(32, 40, 32, 40)
            setOnClickListener { scanQr(::handleQr) } }
        welcome.addView(scan, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        frame.addView(welcome, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))

        // State 2: paired.
        live = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        status = TextView(this).apply { textSize = 15f; setPadding(4, 12, 4, 2); ink() }
        summary = TextView(this).apply { textSize = 13f; setPadding(4, 0, 4, 8); ink(Palette.MUTED) }
        live.addView(status); live.addView(summary)
        radar = RadarView(this).apply { onPing = { openRadar(it.ap) } }
        live.addView(radar, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1.2f))
        adapter = object : ArrayAdapter<Row>(this, android.R.layout.simple_list_item_2, android.R.id.text1, mutableListOf()) {
            override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
                val view = super.getView(position, convertView, parent)
                val item = getItem(position)!!
                view.findViewById<TextView>(android.R.id.text1).apply {
                    text = "● ${item.verdict.status.label} · ${item.ap.ssid.ifEmpty { "(ukryta sieć)" }} · ${item.ap.rssi} dBm"
                    setTextColor(Palette.status(item.verdict.status))
                }
                view.findViewById<TextView>(android.R.id.text2).apply {
                    text = item.verdict.reason
                    setTextColor(Palette.MUTED)
                }
                return view
            }
        }
        list = ListView(this).apply {
            adapter = this@MainActivity.adapter
            divider = ColorDrawable(Palette.GOLD_DIM); dividerHeight = 1
            setOnItemClickListener { _, _, position, _ -> openRadar(this@MainActivity.adapter.getItem(position)!!.ap) }
        }
        live.addView(list, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))
        page = WebView(this).apply { visibility = View.GONE }
        live.addView(page, LinearLayout.LayoutParams(MATCH_PARENT, 0, 2.2f))
        toggle = Button(this).apply { textSize = 18f; gold(); setPadding(24, 28, 24, 28); setOnClickListener { toggleSensor() } }
        live.addView(toggle, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = 12 })
        frame.addView(live, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))
        setContentView(frame)
        setupPanel()
        prefs.registerOnSharedPreferenceChangeListener(this)
        update()
        // Opening the app is enough: if it was paired and the user did not stop it, the sensor runs.
        if (state == null && paired && prefs.getBoolean("wanted", true) && !SensorService.live) startSensor()
    }

    private fun setupPanel() {
        page.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            setGeolocationEnabled(true)
            allowFileAccess = false
            allowContentAccess = false
            javaScriptCanOpenWindowsAutomatically = false
        }
        page.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean =
                !sameOrigin(request.url.toString())
            // Same pinning as the sensor: accept the laptop's own certificate, nothing else.
            override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
                val der = SslCertificate.saveState(error.certificate).getByteArray("x509-certificate")
                val pin = prefs.getString("fp", null)
                if (der != null && pin != null && sameOrigin(error.url) && sha256Hex(der) == pin) handler.proceed() else handler.cancel()
            }
        }
        page.webChromeClient = object : WebChromeClient() {
            override fun onGeolocationPermissionsShowPrompt(origin: String, callback: GeolocationPermissions.Callback) {
                callback.invoke(origin, sameOrigin(origin) && checkSelfPermission(ACCESS_FINE_LOCATION) == PERMISSION_GRANTED, false)
            }
        }
    }

    private fun dialog() = AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
    private fun openRadar(ap: SeenAp) = startActivity(Intent(this, HotColdActivity::class.java)
        .putExtra("bssid", ap.bssid).putExtra("ssid", ap.ssid))

    override fun onResume() { super.onResume(); refresh() }
    override fun onPause() { ticker.removeCallbacksAndMessages(null); super.onPause() }

    /** Reads the cached scan every 5 s. When the sensor is off, asks for a new scan at most every 30 s. */
    @Suppress("DEPRECATION")
    private fun refresh() {
        ticker.removeCallbacksAndMessages(null)
        if (paired && checkSelfPermission(ACCESS_FINE_LOCATION) == PERMISSION_GRANTED) {
            if (!SensorService.live && SystemClock.elapsedRealtime() - lastOwnScan > SensorService.PERIOD_S * 1000) {
                lastOwnScan = SystemClock.elapsedRealtime(); radio.startScan()
            }
            val known = loadKnown(this)
            val rows = radio.scanResults.filter { it.BSSID != null }.map {
                val ap = SeenAp(it.SSID.orEmpty(), it.BSSID.lowercase(), it.level, it.frequency, it.capabilities.orEmpty())
                Row(ap, classify(ap, known))
            }.sortedWith(compareBy<Row>({ it.verdict.status.rank }, { -it.ap.rssi }))
            adapter.clear(); adapter.addAll(rows)
            radar.pings = rows.map { RadarView.Ping(it.ap, it.verdict.status) }
            val counts = rows.groupingBy { it.verdict.status }.eachCount()
            val alarms = (counts[Status.SUSPICIOUS] ?: 0) + (counts[Status.HOTSPOT] ?: 0)
            summary.text = (if (alarms > 0) "⚠ Do sprawdzenia: $alarms · " else "Brak alarmów · ") +
                "sieci w pobliżu: ${rows.size} · znane z laptopa: ${known.size}"
        }
        ticker.postDelayed(::refresh, 5000)
    }

    private fun sameOrigin(url: String) = runCatching { serverOrigin(url) == prefs.getString("origin", "") }.getOrDefault(false)

    private fun scanQr(onText: (String) -> Unit) {
        val options = GmsBarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build()
        GmsBarcodeScanning.getClient(this, options).startScan()
            .addOnSuccessListener { code -> code.rawValue?.let(onText) }
            .addOnFailureListener { toast("Skaner QR niedostępny: " + it.localizedMessage) }
    }

    /** One scanner for both codes: the laptop's "Połącz" code and a door sticker with a place. */
    private fun handleQr(text: String) {
        parsePlace(text)?.let { storePlace(prefs, it); toast("Miejsce: $it"); update(); return }
        val invite = parseInvite(text) ?: return toast("To nie jest kod AEGIS. Zeskanuj kod „Połącz” z ekranu laptopa.")
        pair(invite)
    }

    private fun pair(invite: Invite) {
        pairing = true; update()
        Thread {
            val result = runCatching {
                val data = post(invite.origin, "/exchange", JSONObject().put("token", invite.token), "", invite.fingerprint)
                prefs.edit().putString("origin", invite.origin).putString("key", data.getString("key"))
                    .putString("fp", invite.fingerprint).putString("scope", data.optString("scope", "local"))
                    .putBoolean("wanted", true).putInt("sent", 0).putString("status", "Połączono z laptopem").commit()
                syncKnown(this, prefs)
            }
            runOnUiThread {
                pairing = false
                if (isDestroyed) return@runOnUiThread
                result.onSuccess { startSensor() }
                    .onFailure { toast((it.localizedMessage ?: "Błąd") + ". Na laptopie kliknij „Dodaj inny telefon” i zeskanuj nowy kod.") }
                update()
            }
        }.start()
    }

    private fun more() {
        val items = mutableListOf<Pair<String, () -> Unit>>()
        items.add("Miejsce: zeskanuj kod z drzwi" to { scanQr(::handleQr) })
        items.add("Miejsce: wpisz ręcznie" to ::editPlace)
        if (paired) {
            items.add((if (page.visibility == View.VISIBLE) "Ukryj panel laptopa" else "Panel laptopa") to ::togglePanel)
            items.add("Odśwież znane routery z laptopa" to {
                Thread { val n = syncKnown(this, prefs); runOnUiThread { toast(if (n == null) "Laptop nie odpowiada." else "Znane routery: $n"); refresh() } }.start()
            })
        }
        items.add("Wczytaj znane routery z pliku CSV" to ::importKnown)
        if (paired) items.add("Połącz z innym laptopem" to { disconnect(); scanQr(::handleQr) })
        if (paired) items.add("Rozłącz" to ::disconnect)
        dialog().setTitle("Więcej").setItems(items.map { it.first }.toTypedArray()) { _, i -> items[i].second() }.show()
    }

    private fun disconnect() {
        stopService(Intent(this, SensorService::class.java))
        prefs.edit().remove("key").remove("origin").remove("fp").putString("status", "Rozłączono").apply()
        page.visibility = View.GONE
        update()
    }

    private fun editPlace() {
        val current = savedPlace(prefs)
        val fields = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 8, 32, 8) }
        fun field(hint: String, value: String?) = EditText(this).apply { this.hint = hint; setSingleLine(); setText(value.orEmpty()); fields.addView(this) }
        val building = field("Budynek, np. B1", current?.building)
        val floor = field("Piętro, np. 2", current?.floor)
        val room = field("Sala, np. 215", current?.room)
        dialog().setTitle("Gdzie jesteś?")
            .setMessage("W budynku GPS zwykle nie działa. To miejsce trafia do raportów, gdy nie ma dobrego GPS.")
            .setView(fields)
            .setNegativeButton("Wyczyść") { _, _ -> storePlace(prefs, null); update() }
            .setPositiveButton("Zapisz") { _, _ ->
                val value = place(building.text.toString(), floor.text.toString(), room.text.toString())
                if (value == null) toast("Podaj co najmniej budynek.")
                storePlace(prefs, value); update()
            }.show()
    }

    private fun importKnown() {
        startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
            .setType("*/*").putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("text/csv", "text/comma-separated-values", "text/plain")), 3)
    }

    @Deprecated("Document picker result")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        val uri = data?.data
        if (requestCode != 3 || resultCode != RESULT_OK || uri == null) return
        runCatching {
            val bytes = contentResolver.openInputStream(uri)!!.use { it.readBytes() }
            require(bytes.size <= 1_000_000) { "Plik CSV jest większy niż 1 MB." }
            storeKnown(this, String(bytes, Charsets.UTF_8))
        }.onSuccess { toast("Wczytano znane routery: $it"); refresh() }
            .onFailure { toast(it.localizedMessage ?: "Nie udało się wczytać pliku CSV.") }
    }

    private fun togglePanel() {
        val show = page.visibility != View.VISIBLE
        if (show) page.loadUrl(prefs.getString("origin", "") + "/phone-beacon#apk=" + prefs.getString("key", ""))
        page.visibility = if (show) View.VISIBLE else View.GONE
        list.visibility = if (show) View.GONE else View.VISIBLE
        radar.visibility = list.visibility
    }

    private fun toggleSensor() {
        if (SensorService.live) {
            prefs.edit().putBoolean("wanted", false).apply()
            stopService(Intent(this, SensorService::class.java))
        } else { prefs.edit().putBoolean("wanted", true).apply(); startSensor() }
    }

    /** Asks for all permissions in one system dialog, then starts the background sensor. */
    private fun startSensor() {
        if (permissions.any { checkSelfPermission(it) != PERMISSION_GRANTED }) {
            startAfterGrant = true
            requestPermissions(permissions.toTypedArray(), 1)
        } else startForegroundService(Intent(this, SensorService::class.java))
    }

    override fun onRequestPermissionsResult(code: Int, names: Array<String>, results: IntArray) {
        super.onRequestPermissionsResult(code, names, results)
        if (code == 1 && startAfterGrant) {
            startAfterGrant = false
            if (checkSelfPermission(ACCESS_FINE_LOCATION) == PERMISSION_GRANTED) startForegroundService(Intent(this, SensorService::class.java))
            else toast("Bez zgody na lokalizację Android nie pokaże sieci Wi-Fi.")
        }
        refresh()
    }

    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_LONG).show()

    private fun update() {
        welcome.visibility = if (paired) View.GONE else View.VISIBLE
        live.visibility = if (paired) View.VISIBLE else View.GONE
        scan.isEnabled = !pairing
        scan.text = if (pairing) "Łączenie…" else "Zeskanuj kod z laptopa"
        val active = SensorService.live
        toggle.text = if (active) "■  Zatrzymaj czujnik" else "▶  Włącz czujnik"
        val place = savedPlace(prefs)?.let { " · $it" } ?: ""
        status.text = (if (active) "● Czujnik działa" else "○ Czujnik wyłączony") + " · wysłano: ${prefs.getInt("sent", 0)}" + place +
            prefs.getString("status", "").orEmpty().let { if (it.isEmpty()) "" else "\n$it" }
        status.ink(if (active) Palette.GREEN else Palette.MUTED)
    }
    override fun onSharedPreferenceChanged(p: SharedPreferences?, key: String?) {
        if (key in setOf("status", "active", "key", "sent")) runOnUiThread(::update)
    }
    override fun onDestroy() {
        prefs.unregisterOnSharedPreferenceChangeListener(this)
        page.destroy()
        super.onDestroy()
    }
}
