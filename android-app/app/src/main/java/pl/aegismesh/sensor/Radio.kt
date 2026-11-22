package pl.aegismesh.sensor

/**
 * Pure radio logic (no Android imports) so it can be unit tested on the JVM.
 * The phone only sees access points: SSID, BSSID, signal and security flags.
 */

enum class Status(val label: String, val rank: Int) {
    SUSPICIOUS("Podejrzany", 0), HOTSPOT("Hotspot", 1), NEW("Nowy", 2), KNOWN("Znany", 3)
}

data class KnownAp(val ssid: String, val bssid: String, val security: String)
data class SeenAp(val ssid: String, val bssid: String, val rssi: Int, val frequency: Int, val flags: String)
data class Verdict(val status: Status, val reason: String)

private val MAC = Regex("(?:[0-9a-f]{2}:){5}[0-9a-f]{2}")
private val HOTSPOT_NAME = Regex("(?i)^(iphone|android ?ap|direct-|galaxy|redmi|xiaomi|poco|huawei|honor|oneplus|moto|pixel|oppo|realme|vivo)|hotspot|tethering")

/** Same names as the server's mobile_telemetry.security_mode, so both sides agree. */
fun securityMode(flags: String): String {
    val tags = flags.uppercase()
    return when {
        "EAP" in tags || "802.1X" in tags -> if ("SUITE_B" in tags) "WPA3-ENTERPRISE" else "WPA2-ENTERPRISE"
        "SAE" in tags -> "WPA3-PERSONAL"
        "PSK" in tags -> if ("RSN" in tags || "WPA2" in tags) "WPA2-PERSONAL" else "WPA-PERSONAL"
        "OWE" in tags -> "OWE"
        "WEP" in tags -> "WEP"
        tags == "OPEN" || Regex("(?:\\[(?:ESS|WPS)\\])+").matches(tags) -> "OPEN"
        else -> "UNKNOWN"
    }
}

/** Higher is stronger. Inventory files may write "802.1X" instead of a mode name. */
fun strength(mode: String): Int = when (mode.uppercase()) {
    "OPEN" -> 0
    "WEP" -> 1
    "OWE", "WPA-PERSONAL" -> 2
    "WPA2-PERSONAL" -> 3
    "WPA3-PERSONAL", "WPA-ENTERPRISE" -> 4
    "WPA2-ENTERPRISE", "802.1X" -> 5
    "WPA3-ENTERPRISE" -> 6
    else -> -1
}

/** Second hex digit 2, 6, A or E: a randomised or software MAC, typical for phone hotspots. */
fun locallyAdministered(bssid: String) = bssid.length >= 2 && bssid[1].lowercaseChar() in "26ae"

fun classify(ap: SeenAp, known: List<KnownAp>): Verdict {
    val mode = securityMode(ap.flags)
    val bssid = ap.bssid.lowercase()
    val exact = known.firstOrNull { it.bssid == bssid }
    if (exact != null) {
        val expected = strength(exact.security)
        return if (expected >= 0 && strength(mode) in 0 until expected)
            Verdict(Status.SUSPICIOUS, "Znany BSSID, ale zabezpieczenie $mode zamiast ${exact.security}")
        else Verdict(Status.KNOWN, "Na liście znanych routerów")
    }
    val twin = known.filter { it.ssid.isNotEmpty() && it.ssid == ap.ssid }
    if (twin.isNotEmpty()) {
        val expected = twin.maxOf { strength(it.security) }
        return if (strength(mode) in 0 until expected)
            Verdict(Status.SUSPICIOUS, "Możliwy evil twin: nazwa znanej sieci, obcy BSSID i słabsze zabezpieczenie ($mode)")
        else Verdict(Status.NEW, "Nowy punkt znanej sieci. Dopisz go do listy, jeśli jest wasz")
    }
    val hints = mutableListOf<String>()
    if (HOTSPOT_NAME.containsMatchIn(ap.ssid)) hints.add("nazwa jak hotspot telefonu")
    if (locallyAdministered(bssid)) hints.add("MAC lokalnie administrowany")
    val personal = strength(mode) in 0..4 && !mode.endsWith("ENTERPRISE")
    if (hints.isNotEmpty() && (personal || hints.size > 1))
        return Verdict(Status.HOTSPOT, hints.joinToString(", ").replaceFirstChar { it.uppercase() })
    return Verdict(Status.NEW, if (known.isEmpty()) "Brak listy znanych routerów" else "Brak na liście znanych routerów")
}

/** CSV with header ssid,bssid,security (the same format as aegis-web/static/examples/inventory.csv). */
fun parseKnown(text: String): List<KnownAp> {
    val lines = text.lineSequence().map { it.trim().removePrefix("﻿") }.filter { it.isNotEmpty() }.toList()
    if (lines.isEmpty()) return emptyList()
    val header = splitCsv(lines[0]).map { it.lowercase() }
    val ssid = header.indexOf("ssid"); val bssid = header.indexOf("bssid"); val security = header.indexOf("security")
    require(ssid >= 0 && bssid >= 0 && security >= 0) { "Plik CSV musi mieć kolumny ssid,bssid,security." }
    return lines.drop(1).mapNotNull { line ->
        val cells = splitCsv(line)
        val mac = cells.getOrNull(bssid)?.lowercase()?.replace('-', ':') ?: return@mapNotNull null
        if (!MAC.matches(mac)) return@mapNotNull null
        KnownAp(cells.getOrNull(ssid).orEmpty(), mac, cells.getOrNull(security).orEmpty().uppercase())
    }.distinctBy { it.ssid to it.bssid }
}

internal fun splitCsv(line: String): List<String> {
    val out = mutableListOf<String>(); val cell = StringBuilder(); var quoted = false; var i = 0
    while (i < line.length) {
        val c = line[i]
        when {
            c == '"' && quoted && i + 1 < line.length && line[i + 1] == '"' -> { cell.append('"'); i++ }
            c == '"' -> quoted = !quoted
            c == ',' && !quoted -> { out.add(cell.toString().trim()); cell.clear() }
            else -> cell.append(c)
        }
        i++
    }
    out.add(cell.toString().trim())
    return out
}

/** Hot-cold hint from two RSSI readings in dBm. A 3 dB dead band hides normal jitter. */
fun trend(previous: Int?, current: Int): String = when {
    previous == null -> "Idź powoli i obserwuj sygnał"
    current - previous >= 3 -> "Cieplej"
    previous - current >= 3 -> "Zimniej"
    else -> "Bez zmian"
}

/** Rough 0-100 % bar for -95 dBm (nothing) .. -35 dBm (next to the router). */
fun signalPercent(rssi: Int) = ((rssi + 95) * 100 / 60).coerceIn(0, 100)

/**
 * Exponential smoothing of RSSI. Single Wi-Fi readings jump by ±5 dB even when standing still,
 * so the hot-cold view shows a smoothed value. alpha 0.4 = new reading weighs 40 %.
 */
fun smooth(previous: Double?, reading: Int, alpha: Double = 0.4): Double =
    if (previous == null) reading.toDouble() else previous + alpha * (reading - previous)

/** 0.0 = right next to the router (≥ -40 dBm), 1.0 = far away (≤ -90 dBm). */
fun distanceFactor(rssi: Double): Double = ((-40.0 - rssi) / 50.0).coerceIn(0.0, 1.0)

/** Half-angle of the search cone in degrees: narrow (8°) when close, wide (70°) when far. */
fun coneHalfAngle(rssi: Double): Double = 8.0 + 62.0 * distanceFactor(rssi)
