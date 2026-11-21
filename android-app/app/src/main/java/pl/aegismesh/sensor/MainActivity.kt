package pl.aegismesh.sensor

import android.Manifest.permission.*
import android.app.Activity
import android.app.AlertDialog
import android.content.*
import android.content.pm.PackageManager.PERMISSION_GRANTED
import android.graphics.Color
import android.graphics.Typeface
import android.net.wifi.WifiManager
import android.os.*
import android.text.InputType
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
 * Main screen: pairing (QR or PIN), start/stop of the background sensor, manual place,
 * known-router list and a live list of nearby access points with their status.
 * Tapping a router opens the hot-cold finder. The laptop panel stays available as a WebView.
 */
class MainActivity : Activity(), SharedPreferences.OnSharedPreferenceChangeListener {
    private val prefs by lazy { getSharedPreferences("sensor", MODE_PRIVATE) }
    private val radio by lazy { applicationContext.getSystemService(WIFI_SERVICE) as WifiManager }
    private val ticker = Handler(Looper.getMainLooper())
    private lateinit var page: WebView
    private lateinit var list: ListView
    private lateinit var status: TextView
    private lateinit var where: TextView
    private lateinit var summary: TextView
    private lateinit var toggle: Button
    private lateinit var connect: Button
    private lateinit var panel: Button
    private lateinit var adapter: ArrayAdapter<Row>
    private var pairing = false
    private var startAfterGrant = false
    private var lastOwnScan = 0L
    private val permissions get() = mutableListOf(ACCESS_COARSE_LOCATION, ACCESS_FINE_LOCATION).also {
        if (Build.VERSION.SDK_INT >= 33) { it.add(NEARBY_WIFI_DEVICES); it.add(POST_NOTIFICATIONS) }
    }

    private class Row(val ap: SeenAp, val verdict: Verdict)

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        val frame = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; fitsSystemWindows = true; setPadding(8, 8, 8, 0) }
        connect = button("Połącz") { configure() }
        toggle = button("") { toggleSensor() }
        frame.addView(row(connect, toggle))
        panel = button("Panel laptopa") { togglePanel() }
        frame.addView(row(button("Miejsce") { editPlace() }, button("Znane routery") { importKnown() }, panel))
        where = TextView(this).apply { setPadding(12, 8, 12, 0); setTypeface(typeface, Typeface.BOLD) }
        status = TextView(this).apply { setPadding(12, 4, 12, 4) }
        summary = TextView(this).apply { setPadding(12, 0, 12, 8) }
        frame.addView(where); frame.addView(status); frame.addView(summary)
        adapter = object : ArrayAdapter<Row>(this, android.R.layout.simple_list_item_2, android.R.id.text1, mutableListOf()) {
            override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
                val view = super.getView(position, convertView, parent)
                val item = getItem(position)!!
                view.findViewById<TextView>(android.R.id.text1).apply {
                    text = "${item.verdict.status.label} · ${item.ap.ssid.ifEmpty { "(ukryta sieć)" }} · ${item.ap.rssi} dBm"
                    setTextColor(color(item.verdict.status))
                }
                view.findViewById<TextView>(android.R.id.text2).text =
                    "${item.ap.bssid} · ${securityMode(item.ap.flags)} · ${item.verdict.reason}"
                return view
            }
        }
        list = ListView(this).apply {
            adapter = this@MainActivity.adapter
            setOnItemClickListener { _, _, position, _ ->
                val item = this@MainActivity.adapter.getItem(position)!!
                startActivity(Intent(this@MainActivity, HotColdActivity::class.java)
                    .putExtra("bssid", item.ap.bssid).putExtra("ssid", item.ap.ssid))
            }
        }
        frame.addView(list, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))
        page = WebView(this).apply { visibility = View.GONE }
        frame.addView(page, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))
        setContentView(frame)
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
        }
        page.webChromeClient = object : WebChromeClient() {
            override fun onGeolocationPermissionsShowPrompt(origin: String, callback: GeolocationPermissions.Callback) {
                callback.invoke(origin, sameOrigin(origin) && checkSelfPermission(ACCESS_FINE_LOCATION) == PERMISSION_GRANTED, false)
            }
        }
        prefs.registerOnSharedPreferenceChangeListener(this)
        update()
        if (!prefs.contains("key")) status.text = "Połącz telefon z laptopem: Połącz → Skanuj QR albo wpisz adres i PIN."
        if (state == null && permissions.any { checkSelfPermission(it) != PERMISSION_GRANTED })
            requestPermissions(permissions.toTypedArray(), 1)
    }

    private fun button(label: String, action: () -> Unit) = Button(this).apply { text = label; setOnClickListener { action() } }
    private fun row(vararg views: View) = LinearLayout(this).apply {
        views.forEach { addView(it, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f)) }
    }
    private fun color(s: Status) = when (s) {
        Status.SUSPICIOUS -> Color.rgb(198, 40, 40)
        Status.HOTSPOT -> Color.rgb(230, 81, 0)
        Status.NEW -> Color.rgb(21, 101, 192)
        Status.KNOWN -> Color.rgb(46, 125, 50)
    }

    override fun onResume() { super.onResume(); refresh() }
    override fun onPause() { ticker.removeCallbacksAndMessages(null); super.onPause() }

    /** Reads the cached scan every 5 s. When the service is off, asks for a new scan at most every 30 s. */
    @Suppress("DEPRECATION")
    private fun refresh() {
        ticker.removeCallbacksAndMessages(null)
        if (checkSelfPermission(ACCESS_FINE_LOCATION) == PERMISSION_GRANTED) {
            if (!SensorService.live && SystemClock.elapsedRealtime() - lastOwnScan > SensorService.PERIOD_S * 1000) {
                lastOwnScan = SystemClock.elapsedRealtime(); radio.startScan()
            }
            val known = loadKnown(this)
            val rows = radio.scanResults.filter { it.BSSID != null }.map {
                val ap = SeenAp(it.SSID.orEmpty(), it.BSSID.lowercase(), it.level, it.frequency, it.capabilities.orEmpty())
                Row(ap, classify(ap, known))
            }.sortedWith(compareBy<Row>({ it.verdict.status.rank }, { -it.ap.rssi }))
            adapter.clear(); adapter.addAll(rows)
            val counts = rows.groupingBy { it.verdict.status }.eachCount()
            summary.text = Status.values().joinToString(" · ") { "${it.label}: ${counts[it] ?: 0}" } +
                " · lista znanych: ${known.size}"
        } else summary.text = "Brak zgody na lokalizację: Android nie pokaże sieci Wi-Fi."
        ticker.postDelayed(::refresh, 5000)
    }

    private fun sameOrigin(url: String) = runCatching { serverOrigin(url) == prefs.getString("origin", "") }.getOrDefault(false)

    private fun scanQr(onText: (String) -> Unit) {
        val options = GmsBarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build()
        GmsBarcodeScanning.getClient(this, options).startScan()
            .addOnSuccessListener { code -> code.rawValue?.let(onText) }
            .addOnFailureListener { status.text = "Skaner QR niedostępny: " + it.localizedMessage }
    }

    /** One QR scanner for both kinds of codes: pairing links and door stickers with a place. */
    private fun handleQr(text: String) {
        val found = parsePlace(text)
        if (found != null) { storePlace(prefs, found); update(); return }
        runCatching { pairAddress(text, "") }.onFailure { status.text = it.localizedMessage }
    }

    private fun configure() {
        val fields = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 8, 32, 8) }
        val address = EditText(this).apply { hint = "Adres sesji telefonu"; setSingleLine(); setText(prefs.getString("origin", "")) }
        val pin = EditText(this).apply { hint = "Jednorazowy PIN (8 cyfr)"; inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD }
        fields.addView(address); fields.addView(pin)
        AlertDialog.Builder(this).setTitle("Połączenie z laptopem").setView(fields)
            .setNeutralButton("Skanuj QR") { _, _ -> scanQr(::handleQr) }
            .setNegativeButton("Anuluj", null).setPositiveButton("Połącz") { _, _ ->
                runCatching { pairAddress(address.text.toString(), pin.text.toString()) }
                    .onFailure { status.text = it.localizedMessage }
            }.show()
    }

    private fun editPlace() {
        val current = savedPlace(prefs)
        val fields = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 8, 32, 8) }
        fun field(hint: String, value: String?) = EditText(this).apply { this.hint = hint; setSingleLine(); setText(value.orEmpty()); fields.addView(this) }
        val building = field("Budynek, np. B1", current?.building)
        val floor = field("Piętro, np. 2", current?.floor)
        val room = field("Sala, np. 215", current?.room)
        AlertDialog.Builder(this).setTitle("Gdzie jesteś?")
            .setMessage("W budynku GPS zwykle nie działa. To miejsce dołączamy do raportów, gdy nie ma dobrego GPS.")
            .setView(fields)
            .setNeutralButton("QR z drzwi") { _, _ -> scanQr(::handleQr) }
            .setNegativeButton("Wyczyść") { _, _ -> storePlace(prefs, null); update() }
            .setPositiveButton("Zapisz") { _, _ ->
                val value = place(building.text.toString(), floor.text.toString(), room.text.toString())
                if (value == null) status.text = "Podaj co najmniej budynek (litery, cyfry, do 40 znaków)."
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
            val text = String(bytes, Charsets.UTF_8)
            storeKnown(this, text)
        }.onSuccess { status.text = "Wczytano znane routery: $it"; refresh() }
            .onFailure { status.text = it.localizedMessage ?: "Nie udało się wczytać pliku CSV." }
    }

    private fun pairAddress(address: String, pin: String) {
        val origin = serverOrigin(address)
        val invite = android.net.Uri.parse(address.trim()).fragment.orEmpty()
        val body = JSONObject()
        if (Regex("[0-9]{8}").matches(pin)) body.put("pin", pin)
        else if (Regex("[A-Za-z0-9_-]{43}").matches(invite)) body.put("token", invite)
        else throw IllegalArgumentException("Podaj ośmiocyfrowy PIN lub zeskanuj jednorazowy kod QR.")
        pairing = true; update(); status.text = "Parowanie…"
        Thread {
            val result = runCatching { post(origin, "/exchange", body) }
            runOnUiThread {
                pairing = false
                if (isDestroyed) return@runOnUiThread
                result.onSuccess { data ->
                    prefs.edit().putString("origin", origin).putString("key", data.getString("key"))
                        .putString("scope", data.optString("scope", "AGH-LAB")).putString("status", "Sparowano z laptopem").apply()
                }.onFailure { status.text = it.localizedMessage }
                update()
            }
        }.start()
    }

    private fun togglePanel() {
        val show = page.visibility != View.VISIBLE
        if (show && !prefs.contains("key")) { status.text = "Najpierw połącz telefon z laptopem."; return }
        if (show) page.loadUrl(prefs.getString("origin", "") + "/phone-beacon#apk=" + prefs.getString("key", ""))
        page.visibility = if (show) View.VISIBLE else View.GONE
        list.visibility = if (show) View.GONE else View.VISIBLE
        panel.text = if (show) "Lista sieci" else "Panel laptopa"
    }

    private fun toggleSensor() {
        if (SensorService.live) {
            stopService(Intent(this, SensorService::class.java))
        } else if (permissions.any { checkSelfPermission(it) != PERMISSION_GRANTED }) {
            startAfterGrant = true
            requestPermissions(permissions.toTypedArray(), 1)
        } else startForegroundService(Intent(this, SensorService::class.java))
    }

    override fun onRequestPermissionsResult(code: Int, names: Array<String>, results: IntArray) {
        super.onRequestPermissionsResult(code, names, results)
        if (code == 1 && startAfterGrant) {
            startAfterGrant = false
            if (checkSelfPermission(ACCESS_FINE_LOCATION) == PERMISSION_GRANTED) toggleSensor()
            else status.text = "Do pomiaru potrzebna jest dokładna lokalizacja i dostęp do Wi-Fi."
        }
        refresh()
    }

    private fun update() {
        val active = SensorService.live
        toggle.text = if (active) "Zatrzymaj czujnik" else "Uruchom czujnik"
        connect.isEnabled = !active && !pairing
        connect.text = if (prefs.contains("key")) "Połączono ✓" else "Połącz"
        toggle.isEnabled = !pairing
        where.text = "Miejsce: " + (savedPlace(prefs)?.toString() ?: "nie ustawiono (tylko GPS)") + " · " + sensorId(prefs)
        status.text = prefs.getString("status", "Czujnik gotowy")
    }
    override fun onSharedPreferenceChanged(p: SharedPreferences?, key: String?) { if (key == "status" || key == "active" || key == "key") runOnUiThread(::update) }
    override fun onDestroy() {
        prefs.unregisterOnSharedPreferenceChangeListener(this)
        page.destroy()
        super.onDestroy()
    }
}
