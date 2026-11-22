package pl.aegismesh.sensor

import android.text.Editable
import android.text.TextWatcher
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.*
import org.json.JSONObject

/** Statuses that no longer count as "priority 4-5" on the Pulpit (the same rule as the server's tile). */
internal val CLOSED = setOf("rozwiązane", "fałszywy-alarm", "zaakceptowane-ryzyko")

/** Does this list item contain the search text? Case does not matter; empty text matches everything. */
internal fun matches(haystack: String, needle: String): Boolean = needle.isBlank() || haystack.contains(needle.trim(), ignoreCase = true)

/**
 * The list under the Pulpit tiles: active hosts, changes after the last scan, or priority 4-5 findings,
 * whichever tile is selected. A search box filters it; long lists show 40 rows at a time.
 */
internal class TileList(private val host: MainActivity) {
    enum class Kind(val title: String, val path: String) {
        HOSTS("Aktywne hosty", "/api/inventory?state=active"),
        CHANGES("Zmiany po ostatnim skanie", "/api/changes?limit=500"),
        PRIORITY("Priorytet 4–5", "/api/findings?min_priority=4");
    }

    private val context = host
    var kind = Kind.CHANGES
        private set
    private var items: List<JSONObject> = emptyList()
    private var raw = ""
    private var shown = PAGE

    private val title = heading(context, kind.title)
    private val search = EditText(context).apply {
        hint = "Szukaj: IP, usługa, technologia…"; isSingleLine = true; textSize = 15f
        setTextColor(Palette.TEXT); setHintTextColor(Palette.MUTED); background = panelBackground(); setPadding(28, 22, 28, 22)
        addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun afterTextChanged(s: Editable?) { shown = PAGE; render() }
        })
    }
    private val info = TextView(context).apply { ink(Palette.MUTED); textSize = 13f; setPadding(8, 10, 8, 4) }
    private val rows = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
    private val more = Button(context).apply { gold(outline = true); visibility = View.GONE; setOnClickListener { shown += PAGE; render() } }

    val view: View = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        addView(title)
        addView(search, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { setMargins(6, 0, 6, 0) })
        addView(info)
        addView(rows)
        addView(more, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { setMargins(6, 8, 6, 24) })
    }

    /** Called off the main thread. */
    fun fetch(api: Api, which: Kind): JSONObject = api.get(which.path)

    /** Shows a newly selected tile's list; loads it right away. */
    fun select(which: Kind) {
        if (which != kind) { kind = which; items = emptyList(); raw = ""; shown = PAGE; search.setText("") }
        title.text = which.title.uppercase()
        rows.removeAllViews(); more.visibility = View.GONE
        info.text = "Wczytuję…"
        host.work({ fetch(host.session.api(), which) }) { result ->
            result.onSuccess { show(which, it) }.onFailure(host::fail)
        }
    }

    /** New data for a list; ignored when another tile was selected meanwhile, and not redrawn when nothing changed. */
    fun show(which: Kind, data: JSONObject) {
        if (which != kind) return
        val text = data.toString()
        if (text == raw) return
        raw = text
        items = when (which) {
            Kind.HOSTS -> data.optJSONArray("hosts").objects()
            Kind.CHANGES -> data.optJSONArray("changes").objects()
            Kind.PRIORITY -> data.optJSONArray("findings").objects().filter { it.optString("status") !in CLOSED }
        }
        render()
    }

    private fun render() {
        val needle = search.text.toString()
        val found = items.filter { matches(it.toString().replace("\\/", "/"), needle) }
        rows.removeAllViews()
        found.take(shown).forEach { rows.addView(item(it)) }
        info.text = when {
            items.isEmpty() -> empty()
            found.isEmpty() -> "Nic nie pasuje do „${needle.trim()}”."
            else -> "Pokazano ${minOf(shown, found.size)} z ${found.size}" + (if (needle.isBlank()) "" else " (z ${items.size})") + ". Dotknij, aby zobaczyć szczegóły."
        }
        val left = found.size - shown
        more.visibility = if (left > 0) View.VISIBLE else View.GONE
        more.text = "Pokaż więcej (jeszcze $left)"
    }

    private fun empty() = when (kind) {
        Kind.HOSTS -> "Brak aktywnych hostów."
        Kind.CHANGES -> "Brak zmian po ostatnim skanie."
        Kind.PRIORITY -> "Brak otwartych znalezisk z priorytetem 4–5."
    }

    private fun item(o: JSONObject): View = when (kind) {
        Kind.HOSTS -> {
            val services = o.optJSONArray("services")?.let { a -> (0 until a.length()).joinToString(", ") { a.getString(it) } }.orEmpty()
            val tech = o.optJSONArray("technologies")?.let { a -> (0 until a.length()).joinToString(", ") { a.getString(it) } }.orEmpty()
            val name = listOfNotNull(o.optString("ip"), o.text("hostname")).joinToString(" · ")
            val sub = listOf(services.ifEmpty { "brak otwartych portów" }, tech, o.text("top").orEmpty()).filter { it.isNotEmpty() }.joinToString("\n")
            val p = o.optInt("priority")
            row(context, p, name, sub, badge = if (p > 0) "P$p" else "–").apply { setOnClickListener { showHost(host, o.optString("ip")) } }
        }
        Kind.CHANGES -> row(context, o.optInt("priority"), o.optString("message"), o.optString("ip")).apply {
            o.text("ip")?.let { ip -> setOnClickListener { showHost(host, ip) } }
        }
        Kind.PRIORITY -> {
            val sub = (listOf("${findingPlace(o)} · ${o.optString("status")}") + listOfNotNull(techList(o).takeIf { it.isNotEmpty() }) +
                reasonList(o).take(1).map { "• $it" }).joinToString("\n")
            row(context, o.optInt("priority"), o.optString("title"), sub).apply { setOnClickListener { showHost(host, o.optString("ip"), focus = o) } }
        }
    }

    companion object { const val PAGE = 40 }
}
