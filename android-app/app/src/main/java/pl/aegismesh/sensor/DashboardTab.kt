package pl.aegismesh.sensor

import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.*
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import org.json.JSONObject

/**
 * Tab 1: the scan card (big icon, status, progress, connection), the same three tiles as the
 * web panel, the last and next scan, and "Co nowego". While a scan runs the card follows it
 * every 2 s; otherwise the tab checks every 15 s, so a scan started by the schedule shows up too.
 * A banner slides in when a scan starts and when it ends.
 */
internal class DashboardTab(private val host: MainActivity) : Tab {
    private val context = host
    private val dp = context.resources.displayMetrics.density
    private val ticker = Handler(Looper.getMainLooper())
    private var generation = 0
    private var firstLoad = true
    /** The scan this tab is following, so it can tell when it ends. */
    private var following: Int? = null
    /** How the last followed scan ended (failed or paused), shown until a new one starts. */
    private var ended: JSONObject? = null

    private val pulse = ScanPulseView(context)
    private val title = TextView(context).apply { textSize = 24f; setTypeface(typeface, Typeface.BOLD); gravity = Gravity.CENTER; ink(Palette.GOLD) }
    private val detail = TextView(context).apply { textSize = 15f; gravity = Gravity.CENTER; ink(Palette.TEXT); setPadding(0, 6, 0, 0) }
    private val bar = ProgressBar(context, null, android.R.attr.progressBarStyleHorizontal).apply {
        max = 1000; progressTintList = tint(Palette.GOLD); progressBackgroundTintList = tint(Palette.GOLD_DIM)
        visibility = View.GONE
    }
    private val signal = TextView(context).apply { textSize = 14f; gravity = Gravity.CENTER; setPadding(0, 14, 0, 0) }
    private val scanNow = Button(context).apply {
        text = "▶  Skanuj teraz"; textSize = 19f; setTypeface(typeface, Typeface.BOLD); gold()
        minHeight = (60 * dp).toInt(); visibility = View.GONE
    }
    private val tiles = LinearLayout(context)
    private val scans = TextView(context).apply { ink(Palette.MUTED); textSize = 14f; setPadding(8, 16, 8, 8) }
    private val news = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
    private val banner = TextView(context).apply {
        textSize = 18f; setTypeface(typeface, Typeface.BOLD); gravity = Gravity.CENTER
        setPadding((20 * dp).toInt(), (18 * dp).toInt(), (20 * dp).toInt(), (18 * dp).toInt())
        elevation = 12 * dp; visibility = View.GONE
        setOnClickListener { hideBanner() }
    }

    override val view: View = FrameLayout(context).apply {
        addView(ScrollView(context).apply {
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                addView(LinearLayout(context).apply {
                    orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER_HORIZONTAL
                    background = panelBackground(Palette.GOLD); setPadding((18 * dp).toInt(), (18 * dp).toInt(), (18 * dp).toInt(), (18 * dp).toInt())
                    addView(pulse)
                    addView(title, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = (10 * dp).toInt() })
                    addView(detail, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
                    addView(bar, LinearLayout.LayoutParams(MATCH_PARENT, (10 * dp).toInt()).apply { topMargin = (14 * dp).toInt() })
                    addView(signal, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
                    addView(scanNow, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = (16 * dp).toInt() })
                }, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { setMargins(6, 6, 6, 10) })
                addView(tiles)
                addView(scans)
                addView(heading(context, "Co nowego"))
                addView(news)
            })
        }, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        addView(banner, FrameLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT, Gravity.TOP).apply { setMargins(12, 12, 12, 0) })
    }

    init {
        scanNow.setOnClickListener {
            scanNow.isEnabled = false; scanNow.text = "Uruchamiam…"
            host.work({ host.session.api().post("/api/scans") }) { result ->
                scanNow.isEnabled = true; scanNow.text = "▶  Skanuj teraz"
                result.onSuccess { run ->
                    following = run.optInt("id"); ended = null
                    showBanner("▶  Skan #${run.optInt("id")} ruszył", Palette.GOLD, Palette.BLACK)
                    refresh()
                }.onFailure(host::fail)
            }
        }
    }

    override fun refresh() { stop(); load() }

    override fun stop() { generation++; ticker.removeCallbacksAndMessages(null) }

    private fun load() {
        val mine = generation
        val follow = following
        val withChanges = firstLoad
        host.work({
            val api = host.session.api()
            val began = SystemClock.elapsedRealtime()
            val summary = api.get("/api/summary")
            val latency = SystemClock.elapsedRealtime() - began
            val active = summary.optRun("active_run")
            val run = active?.let { api.get("/api/scans/$it") }
            val finished = follow?.takeIf { it != active }?.let { runCatching { api.get("/api/scans/$it") }.getOrNull() }
            val changes = if (active == null || withChanges) api.get("/api/changes?limit=20") else null
            Loaded(summary, run, finished, changes, latency)
        }) { result ->
            if (mine != generation) return@work
            result.onSuccess { show(it) }.onFailure { error ->
                offline()
                host.fail(error)
                if (error is HttpFailure && error.code == 401) return@onFailure
                ticker.postDelayed(::load, 15_000)
            }
        }
    }

    private class Loaded(val summary: JSONObject, val run: JSONObject?, val finished: JSONObject?, val changes: JSONObject?, val latency: Long)

    private fun show(data: Loaded) {
        val summary = data.summary
        val active = summary.optRun("active_run")
        host.say(host.session.serverLabel)

        data.finished?.let { announceEnd(it, summary) }
        if (active != null && active != following && !firstLoad) {
            ended = null
            showBanner("▶  Skan #$active ruszył", Palette.GOLD, Palette.BLACK)
        }
        following = active
        firstLoad = false

        showCard(summary, data.run)
        showSignal(data.latency)
        tiles.removeAllViews()
        tile(summary.optInt("active_hosts"), "aktywne hosty", Palette.TEXT)
        tile(summary.optInt("changes_since_last_scan"), "zmiany", Palette.TEXT)
        tile(summary.optInt("high_priority"), "priorytet 4–5", if (summary.optInt("high_priority") > 0) Palette.RED else Palette.TEXT)

        val lines = mutableListOf<String>()
        summary.optJSONObject("last_scan")?.let { lines += "Ostatni skan: #${it.optInt("id")}, ${clock(it.optString("finished_at"))}" }
        lines += summary.optJSONObject("next_scan")?.let { "Następny: ${clock(it.optString("at"))} (${it.optString("name")})" }
            ?: "Następny: brak harmonogramu"
        scans.text = lines.joinToString("\n")
        data.changes?.let(::showNews)

        ticker.postDelayed(::load, if (active != null) 2_000 else 15_000)
    }

    /** The big card: icon, one-line status, details, progress bar and the button. */
    private fun showCard(summary: JSONObject, run: JSONObject?) {
        val canScan = host.session.role in setOf("edytor", "admin")
        val last = summary.optJSONObject("last_scan")
        val paused = summary.optRun("paused_run")
        val failed = ended?.takeIf { it.optString("status") == "failed" }
        bar.visibility = View.GONE
        when {
            run != null && run.optString("status") == "queued" -> {
                pulse.mode = ScanPulseView.Mode.QUEUED
                state("SKAN CZEKA W KOLEJCE", Palette.GOLD, "Skan #${run.optInt("id")} zaraz ruszy.")
            }
            run != null -> {
                val percent = run.optDouble("progress", 0.0)
                val total = run.optInt("total_hosts")
                val chunks = run.optJSONArray("chunks")
                var found = 0
                if (chunks != null) for (i in 0 until chunks.length()) found += chunks.getJSONObject(i).optInt("hosts_up")
                pulse.mode = ScanPulseView.Mode.RUNNING; pulse.progress = percent.toFloat()
                bar.visibility = View.VISIBLE; bar.progress = (percent * 10).toInt()
                val elapsed = secondsSince(run.optString("started_at"))
                val left = run.optDouble("estimate_seconds", Double.NaN).let { if (it.isNaN() || elapsed == null) null else (it - elapsed).toLong() }
                state("SKAN TRWA · ${percent.toInt()}%", Palette.GOLD,
                    "Skan #${run.optInt("id")} · ${(percent * total / 100).toInt()} z $total adresów\n" +
                        "Znalezione hosty: $found\n" +
                        "Ruszył ${clock(run.optString("started_at"))}" +
                        (elapsed?.let { " · trwa ${duration(it)}" } ?: "") +
                        (left?.takeIf { it > 0 }?.let { " · zostało ok. ${duration(it)}" } ?: ""))
            }
            failed != null -> {
                pulse.mode = ScanPulseView.Mode.FAILED
                state("SKAN NIEUDANY", Palette.RED, "Skan #${failed.optInt("id")}: ${failed.optString("error").ifBlank { "błąd serwera" }}")
            }
            paused != null -> {
                pulse.mode = ScanPulseView.Mode.PAUSED
                state("SKAN WSTRZYMANY", Palette.ORANGE, "Skan #$paused czeka na wznowienie (panel WWW).")
            }
            last != null -> {
                pulse.mode = ScanPulseView.Mode.DONE
                state("SKAN ZAKOŃCZONY", Palette.GREEN,
                    "Skan #${last.optInt("id")} · zakończony ${clock(last.optString("finished_at"))}\n" +
                        "Ruszył ${clock(last.optString("started_at"))}" +
                        (last.optDouble("active_seconds", Double.NaN).takeUnless { it.isNaN() }?.let { " · trwał ${duration(it.toLong())}" } ?: "") +
                        "\nAktywne hosty: ${last.optInt("hosts_up")}")
            }
            else -> {
                pulse.mode = ScanPulseView.Mode.NONE
                state("BRAK SKANÓW", Palette.GOLD, "Nie było jeszcze żadnego skanu.")
            }
        }
        scanNow.visibility = if (canScan && run == null) View.VISIBLE else View.GONE
    }

    private fun state(text: String, color: Int, more: String) { title.text = text; title.ink(color); detail.text = more }

    private fun showSignal(latency: Long) {
        val label = signalLabel(latency)
        signal.text = "●  Połączenie z serwerem: $label ($latency ms)"
        signal.ink(when (label) { "bardzo dobre" -> Palette.GREEN; "dobre" -> Palette.GOLD; else -> Palette.ORANGE })
    }

    private fun offline() {
        pulse.mode = ScanPulseView.Mode.OFFLINE
        state("BRAK POŁĄCZENIA", Palette.RED, "Nie mogę połączyć się z serwerem. Spróbuję ponownie za 15 s.")
        bar.visibility = View.GONE
        signal.text = "●  Połączenie z serwerem: brak"; signal.ink(Palette.RED)
    }

    /** The scan we followed is no longer active: say how it ended. */
    private fun announceEnd(run: JSONObject, summary: JSONObject) {
        val id = run.optInt("id")
        when (run.optString("status")) {
            "done" -> {
                ended = null
                showBanner("✓  Skan #$id zakończony\n${run.optInt("hosts_up")} aktywnych hostów · ${summary.optInt("changes_since_last_scan")} zmian",
                    Palette.GREEN, Palette.BLACK)
            }
            "failed" -> { ended = run; showBanner("✗  Skan #$id nieudany", Palette.RED, Palette.TEXT) }
            "paused" -> { ended = null; showBanner("II  Skan #$id wstrzymany", Palette.ORANGE, Palette.BLACK) }
            else -> return
        }
        buzz()
    }

    private fun showBanner(text: String, color: Int, textColor: Int) {
        banner.animate().cancel()
        banner.text = text; banner.setTextColor(textColor)
        banner.background = GradientDrawable().apply { cornerRadius = 22f; setColor(color) }
        banner.visibility = View.VISIBLE
        banner.alpha = 0f; banner.translationY = -80 * dp
        banner.animate().alpha(1f).translationY(0f).setDuration(350).start()
        banner.removeCallbacks(hide); banner.postDelayed(hide, 5_000)
    }

    private val hide = Runnable { hideBanner() }

    private fun hideBanner() {
        banner.animate().alpha(0f).translationY(-80 * dp).setDuration(300).withEndAction { banner.visibility = View.GONE }.start()
    }

    private fun buzz() = runCatching {
        context.getSystemService(Vibrator::class.java)?.vibrate(VibrationEffect.createOneShot(200, VibrationEffect.DEFAULT_AMPLITUDE))
    }

    private fun showNews(changes: JSONObject) {
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

/** A run id from the summary, or null when the field is missing or null. */
private fun JSONObject.optRun(name: String): Int? = if (isNull(name)) null else optInt(name)

private fun secondsSince(iso: String?): Long? = runCatching {
    java.time.Duration.between(OffsetDateTime.parse(iso), OffsetDateTime.now()).seconds.coerceAtLeast(0)
}.getOrNull()

/** "45 s", "3 min 05 s", "1 h 02 min". */
internal fun duration(seconds: Long): String = when {
    seconds < 60 -> "$seconds s"
    seconds < 3600 -> "${seconds / 60} min %02d s".format(seconds % 60)
    else -> "${seconds / 3600} h %02d min".format(seconds % 3600 / 60)
}

/** How good the link to the server is, from the time one request took. */
internal fun signalLabel(latencyMs: Long): String = when {
    latencyMs < 400 -> "bardzo dobre"
    latencyMs < 1200 -> "dobre"
    else -> "słabe"
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
