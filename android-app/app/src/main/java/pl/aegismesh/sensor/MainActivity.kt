package pl.aegismesh.sensor

import android.Manifest.permission.*
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager.PERMISSION_GRANTED
import android.graphics.Typeface
import android.os.*
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.*
import java.io.IOException

/**
 * AEGIS MESH on the phone: login, then three tabs (Pulpit, Alerty, Radar Wi-Fi).
 *
 * The server address is built into the APK (the laptop's Tailscale Funnel address). When it
 * cannot be reached, the app looks for the server in the same Wi-Fi network. A long press on
 * the logo on the login screen shows the address field ("inny serwer") for tests.
 */
class MainActivity : Activity() {
    internal val session by lazy { Session(this) }
    private val main = Handler(Looper.getMainLooper())
    private lateinit var content: FrameLayout
    private lateinit var status: TextView
    private var tabs: List<Tab> = emptyList()
    private var buttons: List<Button> = emptyList()
    private var current = 0
    private var askedWifi = false

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        window.statusBarColor = Palette.BLACK
        window.navigationBarColor = Palette.BLACK
        current = intent.getIntExtra("tab", state?.getInt("tab") ?: 0)
        if (session.loggedIn) showTabs() else showLogin(null)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (session.loggedIn && tabs.isNotEmpty()) select(intent.getIntExtra("tab", current))
    }

    override fun onSaveInstanceState(out: Bundle) { super.onSaveInstanceState(out); out.putInt("tab", current) }
    override fun onResume() { super.onResume(); if (tabs.isNotEmpty()) tabs[current].refresh() }
    override fun onPause() { tabs.forEach { it.stop() }; super.onPause() }

    // ---- login -----------------------------------------------------------------------

    private fun showLogin(message: String?) {
        tabs.forEach { it.stop() }; tabs = emptyList()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER_HORIZONTAL; fitsSystemWindows = true
            setBackgroundColor(Palette.BLACK); setPadding(56, 120, 56, 56)
        }
        val logo = TextView(this).apply {
            text = "◆ AEGIS MESH"; textSize = 26f; letterSpacing = 0.15f; setTypeface(typeface, Typeface.BOLD)
            ink(Palette.GOLD); gravity = Gravity.CENTER; setPadding(0, 0, 0, 48)
        }
        val start = session.origin.ifEmpty { Session.BUILT_IN }
        val server = input("Adres serwera, np. https://aegis.twoja-siec.ts.net").apply {
            setText(start); inputType = InputType.TYPE_TEXT_VARIATION_URI
            visibility = if (start.isEmpty()) View.VISIBLE else View.GONE
        }
        val where = TextView(this).apply {
            text = if (start.isEmpty()) "Brak adresu w aplikacji: wpisz go albo zostaw puste, żeby szukać w sieci Wi-Fi."
                   else "Serwer: ${start.substringAfter("://")}"
            ink(Palette.MUTED); textSize = 12f; gravity = Gravity.CENTER; setPadding(0, 0, 0, 24)
        }
        logo.setOnLongClickListener {
            server.visibility = View.VISIBLE; where.text = "Inny serwer: wpisz adres albo zostaw puste, żeby szukać w sieci Wi-Fi."; true
        }
        val user = input("Login").apply { setText(session.username) }
        val password = input("Hasło").apply { inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD }
        status = TextView(this).apply { text = message.orEmpty(); ink(Palette.ORANGE); gravity = Gravity.CENTER; setPadding(0, 24, 0, 0) }
        val go = Button(this).apply { text = "Zaloguj"; gold() }
        go.setOnClickListener {
            go.isEnabled = false; status.ink(Palette.MUTED); status.text = "Łączenie…"
            work({ login(server.text.toString(), user.text.toString().trim(), password.text.toString()) }) { result ->
                go.isEnabled = true
                result.onSuccess { password.setText(""); afterLogin() }
                    .onFailure { status.ink(Palette.ORANGE); status.text = it.localizedMessage ?: "Nie udało się zalogować." }
            }
        }
        listOf(logo, where, server, user, password).forEach { root.addView(it, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)) }
        root.addView(go, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = 24 })
        root.addView(status)
        root.addView(TextView(this).apply {
            text = "Konta zakłada administrator w panelu AEGIS MESH. Po 5 błędnych hasłach konto blokuje się na 15 minut."
            ink(Palette.MUTED); textSize = 12f; gravity = Gravity.CENTER; setPadding(0, 48, 0, 0)
        })
        setContentView(ScrollView(this).apply { setBackgroundColor(Palette.BLACK); isFillViewport = true; addView(root) })
    }

    /** Tries the given address; if it cannot be reached (or none was given), looks for the server in the Wi-Fi network. */
    private fun login(typed: String, username: String, password: String) {
        require(username.isNotEmpty() && password.isNotEmpty()) { "Podaj login i hasło." }
        val origin = typed.trim().takeIf { it.isNotEmpty() }?.let(::serverOrigin)
        if (origin != null) {
            try { session.login(origin, username, password); return }
            catch (e: IOException) { main.post { status.text = "Serwer ${origin.substringAfter("://")} nie odpowiada. Szukam w sieci Wi-Fi…" } }
        } else main.post { status.text = "Szukam serwera w sieci Wi-Fi…" }
        val found = findInLan(this) ?: throw IOException(
            "Nie znaleziono serwera. Sprawdź, czy laptop działa i czy telefon jest w tej samej sieci Wi-Fi (AEGIS_BIND=0.0.0.0).")
        session.login(found, username, password)
    }

    private fun afterLogin() {
        AlertJob.schedule(this)
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(POST_NOTIFICATIONS) != PERMISSION_GRANTED)
            requestPermissions(arrayOf(POST_NOTIFICATIONS), 2)
        showTabs()
    }

    private fun logout() {
        AlertJob.cancel(this)
        session.logout()
        showLogin("Wylogowano.")
    }

    // ---- tabs --------------------------------------------------------------------------

    private fun showTabs() {
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; fitsSystemWindows = true
            setBackgroundColor(Palette.BLACK); setPadding(16, 12, 16, 0) }
        root.addView(LinearLayout(this).apply {
            gravity = Gravity.CENTER_VERTICAL; setPadding(8, 4, 8, 12)
            addView(TextView(context).apply { text = "◆ AEGIS MESH"; textSize = 18f; letterSpacing = 0.15f
                setTypeface(typeface, Typeface.BOLD); ink(Palette.GOLD) }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
            addView(TextView(context).apply { text = "${session.username} · ${session.role}"; ink(Palette.MUTED); setPadding(0, 0, 16, 0) })
            addView(Button(context).apply { text = "Wyloguj"; gold(outline = true); setOnClickListener { logout() } })
        })
        status = TextView(this).apply { ink(Palette.MUTED); textSize = 12f; setPadding(8, 0, 8, 8); text = session.serverLabel }
        root.addView(status)
        content = FrameLayout(this)
        root.addView(content, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))
        tabs = listOf(DashboardTab(this), AlertsTab(this), RadarTab(this))
        buttons = listOf("Pulpit", "Alerty", "Radar Wi-Fi").mapIndexed { i, label ->
            Button(this).apply { text = label; setOnClickListener { select(i) } }
        }
        root.addView(LinearLayout(this).apply {
            setPadding(0, 8, 0, 8)
            buttons.forEach { addView(it, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f).apply { setMargins(4, 0, 4, 0) }) }
        })
        setContentView(root)
        select(current.coerceIn(0, tabs.size - 1))
    }

    private fun select(index: Int) {
        tabs.forEach { it.stop() }
        current = index
        buttons.forEachIndexed { i, b -> b.gold(outline = i != index) }
        content.removeAllViews()
        content.addView(tabs[index].view, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        tabs[index].refresh()
    }

    // ---- helpers used by the tabs ------------------------------------------------------

    /** Runs a network call off the main thread and hands the result back on it. */
    internal fun <T> work(task: () -> T, done: (Result<T>) -> Unit) {
        Thread {
            val result = runCatching(task)
            main.post { if (!isDestroyed) done(result) }
        }.start()
    }

    internal fun say(text: String) { status.ink(Palette.MUTED); status.text = text }

    /** An expired login goes back to the login screen; other errors are shown above the tab. */
    internal fun fail(error: Throwable) {
        if (error is HttpFailure && error.code == 401) {
            AlertJob.cancel(this); session.forget(); showLogin("Sesja wygasła. Zaloguj się ponownie."); return
        }
        status.ink(Palette.ORANGE)
        status.text = if (error is IOException) "Brak połączenia z serwerem ${session.serverLabel}." else error.localizedMessage
    }

    /** The Alerts tab saw these waiting findings, so the background check does not announce them again. */
    internal fun markAnnounced(ids: List<String>) { session.announced = session.announced + ids }

    internal fun askWifiPermissions() {
        if (askedWifi) return
        askedWifi = true
        val wanted = mutableListOf(ACCESS_FINE_LOCATION, ACCESS_COARSE_LOCATION)
        if (Build.VERSION.SDK_INT >= 33) wanted += NEARBY_WIFI_DEVICES
        requestPermissions(wanted.toTypedArray(), 1)
    }

    override fun onRequestPermissionsResult(code: Int, names: Array<String>, results: IntArray) {
        super.onRequestPermissionsResult(code, names, results)
        if (code == 1 && tabs.isNotEmpty() && current == 2) tabs[2].refresh()
    }

    @Deprecated("Document picker result")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        val uri = data?.data
        if (requestCode != PICK_KNOWN || resultCode != RESULT_OK || uri == null) return
        runCatching {
            val bytes = contentResolver.openInputStream(uri)!!.use { it.readBytes() }
            require(bytes.size <= 1_000_000) { "Plik CSV jest większy niż 1 MB." }
            storeKnown(this, String(bytes, Charsets.UTF_8))
        }.onSuccess { say("Wczytano znane routery: $it") }.onFailure { status.ink(Palette.ORANGE); status.text = it.localizedMessage }
        if (tabs.isNotEmpty() && current == 2) tabs[2].refresh()
    }

    private fun input(hint: String) = EditText(this).apply {
        this.hint = hint; setSingleLine(); ink(); setHintTextColor(Palette.MUTED); backgroundTintList = tint(Palette.GOLD)
    }

    companion object { const val PICK_KNOWN = 3 }
}
