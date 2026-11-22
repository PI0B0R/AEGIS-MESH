package pl.aegismesh.sensor

import android.view.View
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.*
import org.json.JSONObject

/** Status a finding gets from the "Sprawdzone" button: someone looked at it. */
internal const val CHECKED = "potwierdzone"

/** A P4-5 finding still waits for someone when its status is "nowe". */
internal fun waiting(finding: JSONObject) = finding.optString("status") == "nowe"

internal fun findingPlace(f: JSONObject) = "${f.optString("ip")}:${f.optInt("port")}/${f.optString("proto", "tcp")}"

/** Tab 2: findings with priority 4-5, the waiting ones first, each with a "Sprawdzone" button. */
internal class AlertsTab(private val host: MainActivity) : Tab {
    private val context = host
    private val info = TextView(context).apply { ink(Palette.MUTED); setPadding(8, 8, 8, 8) }
    private val list = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
    override val view: View = ScrollView(context).apply {
        addView(LinearLayout(context).apply { orientation = LinearLayout.VERTICAL; addView(info); addView(list) })
    }

    override fun refresh() {
        host.work({ host.session.api().get("/api/findings?min_priority=4") }) { result ->
            result.onSuccess(::show).onFailure(host::fail)
        }
    }

    private fun show(data: JSONObject) {
        val all = data.optJSONArray("findings") ?: return
        val items = (0 until all.length()).map { all.getJSONObject(it) }
            .sortedWith(compareBy<JSONObject>({ !waiting(it) }, { -it.optInt("priority") }))
        val open = items.count(::waiting)
        info.text = when {
            items.isEmpty() -> "Brak znalezisk z priorytetem 4–5."
            open == 0 -> "Wszystkie ${items.size} znaleziska 4–5 są sprawdzone."
            else -> "Czeka na sprawdzenie: $open z ${items.size}."
        }
        list.removeAllViews()
        items.forEach { list.addView(item(it)) }
        host.markAnnounced(items.filter(::waiting).map { it.optInt("id").toString() })
    }

    private fun item(f: JSONObject): View {
        val tags = (0 until (f.optJSONArray("tags")?.length() ?: 0)).joinToString(" ") { "#" + f.getJSONArray("tags").getString(it) }
        val sub = listOf(findingPlace(f), if (waiting(f)) "" else f.optString("status"), tags).filter { it.isNotEmpty() }.joinToString(" · ")
        val view = row(context, f.optInt("priority"), f.optString("title"), sub)
        if (!waiting(f)) view.alpha = 0.6f
        view.setOnClickListener { showFinding(context, f) }
        if (waiting(f) && host.session.canEditFindings) {
            val box = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL; addView(view) }
            box.addView(Button(context).apply {
                text = "Sprawdzone"; gold()
                setOnClickListener { isEnabled = false; markChecked(f.optInt("id")) }
            }, LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply { setMargins(6, 0, 6, 14) })
            return box
        }
        return view
    }

    private fun markChecked(id: Int) {
        host.work({ host.session.api().patch("/api/findings/$id", JSONObject().put("status", CHECKED)) }) { result ->
            result.onSuccess { host.say("Oznaczono jako sprawdzone."); refresh() }.onFailure { host.fail(it); refresh() }
        }
    }
}
