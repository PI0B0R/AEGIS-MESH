package pl.aegismesh.sensor

import android.app.AlertDialog
import android.graphics.Typeface
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.*
import org.json.JSONArray
import org.json.JSONObject

/** A JSON string that may be missing or JSON null, as null. */
internal fun JSONObject.text(name: String): String? = if (isNull(name)) null else optString(name).takeIf { it.isNotBlank() && it != "null" }

internal fun JSONArray?.objects(): List<JSONObject> = if (this == null) emptyList() else (0 until length()).map { getJSONObject(it) }

internal fun hostState(state: String) = when (state) {
    "active" -> "aktywny"
    "unstable" -> "niestabilny"
    "inactive" -> "nieaktywny"
    else -> state
}

/** "nginx 1.25.3 (Ubuntu)" from a service row. */
internal fun serviceLabel(s: JSONObject): String =
    listOfNotNull(s.text("name"), s.text("product"), s.text("version"), s.text("extrainfo")?.let { "($it)" }).joinToString(" ")

/** One finding: where it is, its status, technologies and why it got its priority. */
internal fun showFinding(context: android.content.Context, f: JSONObject, openHost: ((String) -> Unit)? = null) {
    val tech = f.optJSONArray("technologies").objects()
        .joinToString(", ") { t -> listOfNotNull(t.text("name"), t.text("version")).joinToString(" ") }
    val reasons = reasonList(f).joinToString("\n") { "• $it" }
    val text = listOfNotNull(findingPlace(f), "Status: ${f.optString("status")}",
        tech.takeIf { it.isNotEmpty() }?.let { "Technologie: $it" }, reasons.takeIf { it.isNotEmpty() }?.let { "Dlaczego:\n$it" },
        f.text("note")?.let { "Notatka: $it" }).joinToString("\n\n")
    AlertDialog.Builder(context, android.R.style.Theme_DeviceDefault_Dialog_Alert)
        .setTitle("P${f.optInt("priority")} · ${f.optString("title")}").setMessage(text).setPositiveButton("OK", null)
        .apply { if (openHost != null) setNeutralButton("Karta hosta") { _, _ -> openHost(f.optString("ip")) } }
        .show()
}

internal fun techList(f: JSONObject): String = f.optJSONArray("technologies").objects()
    .joinToString(", ") { t -> listOfNotNull(t.text("name"), t.text("version")).joinToString(" ") }

/** Why a finding has its priority: each reason once, without the one that only repeats the title. */
internal fun reasonList(f: JSONObject): List<String> = f.optJSONArray("reasons").objects().mapNotNull { it.text("message")?.trim() }
    .distinct().filter { it != f.optString("title").trim() }

/**
 * The host card: state, services with versions and web details, findings with their reasons, recent changes.
 * With [focus] (a finding), the card scrolls to that finding and marks it with a gold edge.
 */
internal fun showHost(host: MainActivity, ip: String, focus: JSONObject? = null) {
    host.say("Wczytuję hosta $ip…")
    host.work({ host.session.api().get("/api/hosts/$ip") }) { result ->
        result.onSuccess { card ->
            host.say(host.session.serverLabel)
            hostDialog(host, card, focus)
        }.onFailure(host::fail)
    }
}

private fun hostDialog(host: MainActivity, card: JSONObject, focus: JSONObject?) {
    val context = host
    val body = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL; setPadding(40, 20, 40, 20) }
    fun line(text: String, color: Int = Palette.TEXT, size: Float = 15f, bold: Boolean = false) = body.addView(TextView(context).apply {
        this.text = text; textSize = size; ink(color); setPadding(0, 4, 0, 4); if (bold) setTypeface(typeface, Typeface.BOLD)
    })
    fun section(title: String) = body.addView(heading(context, title).apply { setPadding(0, 24, 0, 8) })

    card.text("hostname")?.let { line(it, Palette.GOLD, 16f, bold = true) }
    line("Stan: ${hostState(card.optString("state"))}", if (card.optString("state") == "active") Palette.GREEN else Palette.ORANGE, bold = true)
    line("Pierwszy raz: ${clock(card.text("first_seen"))} · ostatnio: ${clock(card.text("last_seen"))}", Palette.MUTED, 13f)
    card.optJSONObject("daypart")?.text("pattern")?.let { line("Obecność: $it", Palette.MUTED, 13f) }

    val services = card.optJSONArray("services").objects()
    section("Usługi (${services.size})")
    if (services.isEmpty()) line("Brak otwartych portów w ostatnim skanie.", Palette.MUTED)
    services.forEach { s ->
        line("${s.optInt("port")}/${s.optString("proto")}  ${serviceLabel(s)}", bold = true)
        s.optJSONObject("web")?.let { w ->
            val parts = listOfNotNull(w.text("url"), w.optInt("status").takeIf { it > 0 }?.let { "HTTP $it" },
                w.text("title")?.let { "„$it”" }, w.text("server")?.let { "serwer: $it" })
            if (parts.isNotEmpty()) line(parts.joinToString(" · "), Palette.MUTED, 13f)
            w.optJSONObject("tls")?.let { t ->
                val notes = listOfNotNull(t.text("subject")?.let { "certyfikat: $it" }, t.text("not_after")?.let { "ważny do ${clock(it)}" },
                    "wygasły".takeIf { t.optBoolean("expired") }, "self-signed".takeIf { t.optBoolean("self_signed") })
                line(notes.joinToString(" · "), if (t.optBoolean("expired") || t.optBoolean("self_signed")) Palette.ORANGE else Palette.MUTED, 13f)
            }
            w.text("error")?.let { line("błąd sondy: $it", Palette.MUTED, 13f) }
        }
    }

    val findings = card.optJSONArray("findings").objects().filter { it.optString("state") == "active" }
    var target: android.view.View? = null
    section("Znaleziska (${findings.size})")
    if (findings.isEmpty()) line("Brak znalezisk.", Palette.MUTED)
    findings.forEach { f ->
        val sub = (listOf("${f.optInt("port")}/${f.optString("proto")} · ${f.optString("status")}") +
            listOfNotNull(techList(f).takeIf { it.isNotEmpty() }) + reasonList(f).map { "• $it" }).joinToString("\n")
        body.addView(row(context, f.optInt("priority"), f.optString("title"), sub).apply {
            if (focus != null && f.optInt("id") == focus.optInt("id")) { background = panelBackground(Palette.GOLD); target = this }
            setOnClickListener { showFinding(context, f) }
        })
    }

    val changes = card.optJSONArray("changes").objects().take(10)
    if (changes.isNotEmpty()) {
        section("Ostatnie zmiany")
        changes.forEach { line("• ${it.optString("message")}", Palette.MUTED, 13f) }
    }

    val scroll = ScrollView(context).apply { addView(body, FrameLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)) }
    AlertDialog.Builder(context, android.R.style.Theme_DeviceDefault_Dialog_Alert)
        .setTitle(card.optString("ip"))
        .setView(scroll)
        .setPositiveButton("Zamknij", null).show()
    target?.let { row -> scroll.post { scroll.smoothScrollTo(0, (row.top - 24).coerceAtLeast(0)) } }
}
