package pl.aegismesh.sensor

import android.graphics.Typeface
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.*
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import org.json.JSONObject

/** Tab 1: the same three tiles as the web panel, the last and next scan, and "Co nowego". */
internal class DashboardTab(private val host: MainActivity) : Tab {
    private val context = host
    private val tiles = LinearLayout(context)
    private val scans = TextView(context).apply { ink(Palette.MUTED); setPadding(8, 16, 8, 8) }
    private val news = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
    private val scanNow = Button(context).apply { text = "Skanuj teraz"; gold(outline = true) }
    override val view: View = ScrollView(context).apply {
        addView(LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            addView(tiles)
            addView(scans)
            addView(scanNow, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT))
            addView(heading(context, "Co nowego"))
            addView(news)
        })
    }

    init {
        scanNow.visibility = View.GONE
        scanNow.setOnClickListener {
            scanNow.isEnabled = false
            host.work({ host.session.api().post("/api/scans") }) { result ->
                scanNow.isEnabled = true
                result.onSuccess { host.say("Skan ruszył. Pulpit odświeży się po jego końcu.") }.onFailure(host::fail)
            }
        }
    }

    override fun refresh() {
        host.work({
            val api = host.session.api()
            api.get("/api/summary") to api.get("/api/changes?limit=20")
        }) { result ->
            result.onSuccess { (summary, changes) -> show(summary, changes) }.onFailure(host::fail)
        }
    }

    private fun show(summary: JSONObject, changes: JSONObject) {
        tiles.removeAllViews()
        tile(summary.optInt("active_hosts"), "aktywne hosty", Palette.TEXT)
        tile(summary.optInt("changes_since_last_scan"), "zmiany", Palette.TEXT)
        tile(summary.optInt("high_priority"), "priorytet 4–5", if (summary.optInt("high_priority") > 0) Palette.RED else Palette.TEXT)

        val lines = mutableListOf<String>()
        summary.optJSONObject("last_scan")?.let { lines += "Ostatni skan: #${it.optInt("id")}, ${clock(it.optString("finished_at"))}" }
        if (!summary.isNull("active_run")) lines += "Trwa skan #${summary.optInt("active_run")}"
        if (!summary.isNull("paused_run")) lines += "Wstrzymany skan #${summary.optInt("paused_run")}"
        lines += summary.optJSONObject("next_scan")?.let { "Następny: ${clock(it.optString("at"))} (${it.optString("name")})" }
            ?: "Następny: brak harmonogramu"
        scans.text = lines.joinToString("\n")
        scanNow.visibility = if (host.session.role in setOf("edytor", "admin") && summary.isNull("active_run")) View.VISIBLE else View.GONE

        news.removeAllViews()
        val items = changes.optJSONArray("changes")
        if (items == null || items.length() == 0) {
            news.addView(TextView(context).apply { text = "Brak zmian po ostatnim skanie."; ink(Palette.MUTED); setPadding(8, 8, 8, 8) })
            return
        }
        for (i in 0 until items.length()) {
            val c = items.getJSONObject(i)
            news.addView(row(context, c.optInt("priority"), c.optString("message"), c.optString("ip")))
        }
    }

    private fun tile(value: Int, label: String, color: Int) {
        val box = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL; background = panelBackground(); setPadding(20, 18, 20, 18)
            addView(TextView(context).apply { text = value.toString(); textSize = 34f; setTypeface(typeface, Typeface.BOLD); setTextColor(color) })
            addView(TextView(context).apply { text = label; ink(Palette.MUTED) })
        }
        tiles.addView(box, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f).apply { setMargins(6, 6, 6, 6) })
    }
}

internal interface Tab {
    val view: View
    fun refresh()
    fun stop() {}
}

internal fun heading(context: android.content.Context, text: String) = TextView(context).apply {
    this.text = text.uppercase(); letterSpacing = 0.15f; textSize = 13f; ink(Palette.GOLD); setPadding(8, 28, 8, 10)
}

/** One list row: a priority badge, the sentence and a small line under it. */
internal fun row(context: android.content.Context, priority: Int, title: String, sub: String): LinearLayout =
    LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL; background = panelBackground(); setPadding(18, 16, 18, 16)
        layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { setMargins(6, 6, 6, 6) }
        addView(TextView(context).apply {
            text = "P$priority"; gravity = Gravity.CENTER; setTypeface(typeface, Typeface.BOLD)
            setTextColor(priorityColor(priority)); background = panelBackground(priorityColor(priority)); setPadding(12, 6, 12, 6)
        }, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { setMargins(0, 0, 18, 0) })
        addView(LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            addView(TextView(context).apply { text = title; ink() })
            if (sub.isNotEmpty()) addView(TextView(context).apply { text = sub; textSize = 12f; ink(Palette.MUTED) })
        }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
    }

private val SHORT = DateTimeFormatter.ofPattern("d.MM HH:mm")
internal fun clock(iso: String?): String = runCatching {
    OffsetDateTime.parse(iso).atZoneSameInstant(ZoneId.systemDefault()).format(SHORT)
}.getOrDefault("-")
