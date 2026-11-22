package pl.aegismesh.sensor

import android.app.AlertDialog
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.*
import org.json.JSONObject

/** Status a finding gets from the "Sprawdzone" button: someone looked at it. */
internal const val CHECKED = "potwierdzone"

/** A P4-5 finding still waits for someone when its status is "nowe". */
internal fun waiting(finding: JSONObject) = finding.optString("status") == "nowe"

internal fun findingPlace(f: JSONObject) = "${f.optString("ip")}:${f.optInt("port")}/${f.optString("proto", "tcp")}"

/** The server's finding statuses with the words the phone shows for them. */
internal val STATUSES = linkedMapOf(
    "nowe" to "Czeka (nowe)",
    "potwierdzone" to "Sprawdzone",
    "w-trakcie" to "W trakcie",
    "rozwiązane" to "Rozwiązane",
    "fałszywy-alarm" to "Fałszywy alarm",
    "zaakceptowane-ryzyko" to "Akceptuję ryzyko",
)

internal fun statusLabel(status: String) = STATUSES[status] ?: status

/**
 * Tab 2: findings with priority 4-5, the waiting ones first, then the checked ones (so they can be undone). Tap one for the host card. Analysts can mark one "Sprawdzone", pick another status, or undo.
 */
internal class AlertsTab(private val host: MainActivity) : Tab {
    private val context = host
    private val dp = context.resources.displayMetrics.density
    private var items: List<JSONObject> = emptyList()

    private val info = TextView(context).apply { ink(Palette.MUTED); setPadding(8, 8, 8, 8) }
    private val list = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
    private val undoText = TextView(context).apply { ink(); textSize = 15f }
    private val undoButton = Button(context).apply { text = "Cofnij"; textSize = 16f; setTypeface(typeface, Typeface.BOLD); gold() }
    private val undoBar = LinearLayout(context).apply {
        gravity = Gravity.CENTER_VERTICAL; visibility = View.GONE; elevation = 12 * dp
        setPadding((18 * dp).toInt(), (10 * dp).toInt(), (10 * dp).toInt(), (10 * dp).toInt())
        background = GradientDrawable().apply { cornerRadius = 22f; setColor(Palette.PANEL); setStroke(3, Palette.GOLD) }
        addView(undoText, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        addView(undoButton)
    }
    private val hideUndo = Runnable { undoBar.animate().alpha(0f).setDuration(250).withEndAction { undoBar.visibility = View.GONE }.start() }

    override val view: View = FrameLayout(context).apply {
        addView(ScrollView(context).apply {
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                addView(info); addView(list)
                // room under the last alert so the undo bar does not cover it
                addView(View(context), LinearLayout.LayoutParams(MATCH_PARENT, (90 * dp).toInt()))
            })
        }, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        addView(undoBar, FrameLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT, Gravity.BOTTOM).apply { setMargins(12, 0, 12, 16) })
    }

    override fun refresh() {
        host.work({ host.session.api().get("/api/findings?min_priority=4") }) { result ->
            result.onSuccess(::show).onFailure(host::fail)
        }
    }

    override fun stop() { undoBar.removeCallbacks(hideUndo); undoBar.visibility = View.GONE }

    private fun show(data: JSONObject) {
        items = data.optJSONArray("findings").objects()
            .sortedWith(compareBy<JSONObject>({ !waiting(it) }, { -it.optInt("priority") }))
        host.setAlertCount(items.count(::waiting))
        host.markAnnounced(items.filter(::waiting).map { it.optInt("id").toString() })
        render()
    }

    private fun render() {
        val open = items.count(::waiting)
        info.text = when {
            items.isEmpty() -> "Brak znalezisk z priorytetem 4–5."
            open == 0 -> "Nic nie czeka. Wszystkie ${items.size} znaleziska 4–5 są sprawdzone."
            else -> "Czeka na sprawdzenie: $open z ${items.size}. Dotknij alertu, aby zobaczyć hosta."
        }
        list.removeAllViews()
        items.forEach { list.addView(item(it)) }
    }

    private fun item(f: JSONObject): View {
        val tags = (0 until (f.optJSONArray("tags")?.length() ?: 0)).joinToString(" ") { "#" + f.getJSONArray("tags").getString(it) }
        val sub = listOf(findingPlace(f), if (waiting(f)) "" else statusLabel(f.optString("status")), tags,
            reasonList(f).firstOrNull()?.let { "• $it" }.orEmpty()).filter { it.isNotEmpty() }.joinToString(" · ")
        val view = row(context, f.optInt("priority"), f.optString("title"), sub)
        if (!waiting(f)) view.alpha = 0.65f
        view.setOnClickListener { showHost(host, f.optString("ip"), focus = f) }
        if (!host.session.canEditFindings) return view

        val box = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL; addView(view) }
        val buttons = LinearLayout(context)
        val id = f.optInt("id")
        val before = f.optString("status")
        if (waiting(f)) {
            buttons.addView(Button(context).apply {
                text = "✓ Sprawdzone"; gold()
                setOnClickListener { isEnabled = false; change(id, before, CHECKED) }
            }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f).apply { setMargins(6, 0, 6, 0) })
        } else {
            buttons.addView(Button(context).apply {
                text = "↶ Cofnij"; gold(outline = true)
                setOnClickListener { isEnabled = false; change(id, before, "nowe") }
            }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f).apply { setMargins(6, 0, 6, 0) })
        }
        buttons.addView(Button(context).apply {
            text = "Status…"; gold(outline = true)
            setOnClickListener { pickStatus(id, before) }
        }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f).apply { setMargins(6, 0, 6, 0) })
        box.addView(buttons, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { setMargins(0, 0, 0, 14) })
        return box
    }

    private fun pickStatus(id: Int, before: String) {
        val keys = STATUSES.keys.toList()
        AlertDialog.Builder(context, android.R.style.Theme_DeviceDefault_Dialog_Alert)
            .setTitle("Nowy status")
            .setSingleChoiceItems(keys.map { STATUSES.getValue(it) }.toTypedArray(), keys.indexOf(before)) { dialog, which ->
                dialog.dismiss()
                if (keys[which] != before) change(id, before, keys[which])
            }
            .setNegativeButton("Anuluj", null).show()
    }

    /** Sets a status, then offers to undo it for a few seconds. [undoing] is true for the undo itself. */
    private fun change(id: Int, before: String, after: String, undoing: Boolean = false) {
        host.work({ host.session.api().patch("/api/findings/$id", JSONObject().put("status", after)) }) { result ->
            result.onSuccess {
                if (undoing) { host.say("Cofnięto: znów „${statusLabel(after)}”."); undoBar.post(hideUndo) }
                else offerUndo("Status: ${statusLabel(after)}") { change(id, after, before, undoing = true) }
                refresh()
            }.onFailure { host.fail(it); refresh() }
        }
    }

    private fun offerUndo(text: String, undo: () -> Unit) {
        undoBar.removeCallbacks(hideUndo)
        undoText.text = text
        undoButton.isEnabled = true
        undoButton.setOnClickListener { undoButton.isEnabled = false; undo() }
        undoBar.animate().cancel()
        undoBar.alpha = 0f; undoBar.translationY = 40 * dp; undoBar.visibility = View.VISIBLE
        undoBar.animate().alpha(1f).translationY(0f).setDuration(250).start()
        undoBar.postDelayed(hideUndo, 6_000)
    }
}
