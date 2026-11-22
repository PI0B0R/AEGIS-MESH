package pl.aegismesh.sensor

import kotlin.math.cos
import kotlin.math.pow
import kotlin.math.sqrt

/**
 * Pure logic for the radar map (no Android imports, unit tested on the JVM):
 * a "ping" is one router heard at one GPS position, and a router's position is
 * estimated from all its pings.
 */
data class Ping(
    val time: Long, val lat: Double, val lon: Double, val accuracy: Float,
    val bssid: String, val ssid: String, val rssi: Int, val frequency: Int, val flags: String,
) {
    val ap get() = SeenAp(ssid, bssid, rssi, frequency, flags)
}

/** Where a router probably is: the centre and a radius in metres that it is very likely inside. */
data class Estimate(
    val bssid: String, val ssid: String, val lat: Double, val lon: Double, val radius: Double,
    val pings: Int, val places: Int, val bestRssi: Int, val last: Ping,
) {
    val confidence: String get() = when {
        places >= 6 && radius <= 25 -> "wysoka"
        places >= 3 -> "średnia"
        else -> "niska"
    }
}

/** Pings with GPS worse than this are kept and drawn, but left out of the router estimate: they would only blur it. */
const val MAX_ACCURACY_M = 30f

const val PING_HEADER = "time,lat,lon,accuracy_m,bssid,ssid,rssi_dbm,frequency_mhz,flags"

private fun quote(text: String) = if (text.any { it == ',' || it == '"' || it == '\n' }) "\"" + text.replace("\"", "\"\"").replace("\n", " ") + "\"" else text

fun pingLine(p: Ping): String = listOf(p.time.toString(), "%.7f".format(java.util.Locale.ROOT, p.lat), "%.7f".format(java.util.Locale.ROOT, p.lon),
    "%.1f".format(java.util.Locale.ROOT, p.accuracy), p.bssid, quote(p.ssid), p.rssi.toString(), p.frequency.toString(), quote(p.flags)).joinToString(",")

fun parsePing(line: String): Ping? = runCatching {
    val c = splitCsv(line)
    Ping(c[0].toLong(), c[1].toDouble(), c[2].toDouble(), c[3].toFloat(), c[4].lowercase(), c[5], c[6].toInt(), c[7].toInt(), c.getOrElse(8) { "" })
}.getOrNull()

/** Metres between two points; flat-earth is fine at building scale. */
fun metres(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
    val y = (lat2 - lat1) * 110_540.0
    val x = (lon2 - lon1) * 111_320.0 * cos(Math.toRadians((lat1 + lat2) / 2))
    return sqrt(x * x + y * y)
}

/** Faster than this (m/s, about 250 km/h) a phone does not move: such a jump is a mistake of the location database. */
const val MAX_SPEED_MS = 70.0

/**
 * A network or fused fix far away from the current one, sooner than anyone could get there. Android places the phone
 * by the Wi-Fi routers around it; routers brought from another city still point to that city, so right after a Wi-Fi
 * scan the position can jump hundreds of kilometres. Real GPS fixes are always believed.
 */
fun impossibleJump(oldLat: Double, oldLon: Double, oldNanos: Long, newLat: Double, newLon: Double, newNanos: Long, fromGps: Boolean): Boolean {
    if (fromGps) return false
    val distance = metres(oldLat, oldLon, newLat, newLon)
    val seconds = maxOf(1.0, (newNanos - oldNanos) / 1e9)
    return distance > 1_000 && distance / seconds > MAX_SPEED_MS
}

/** How many really different places (at least [gap] metres apart) the pings were taken from. */
fun distinctPlaces(pings: List<Ping>, gap: Double = 5.0): Int {
    val spots = mutableListOf<Ping>()
    for (p in pings) if (spots.none { metres(it.lat, it.lon, p.lat, p.lon) < gap }) spots += p
    return spots.size
}

/** Stronger signal = closer to the router, so it weighs more: +20 dB is 10× the weight. */
fun weight(rssi: Int): Double = 10.0.pow((rssi + 100) / 20.0)

/**
 * Weighted centre of all pings of one router. The radius is the weighted spread of the pings
 * around that centre, never smaller than the GPS accuracy, and at least 30 m from fewer than 3 places.
 */
fun estimate(pings: List<Ping>): List<Estimate> = pings.filter { it.accuracy <= MAX_ACCURACY_M }.groupBy { it.bssid }.map { (bssid, list) ->
    val total = list.sumOf { weight(it.rssi) }
    val lat = list.sumOf { weight(it.rssi) * it.lat } / total
    val lon = list.sumOf { weight(it.rssi) * it.lon } / total
    val spread = sqrt(list.sumOf { weight(it.rssi) * metres(lat, lon, it.lat, it.lon).pow(2) } / total)
    val gps = list.sumOf { weight(it.rssi) * it.accuracy } / total
    val places = distinctPlaces(list)
    var radius = maxOf(5.0, spread, gps)
    if (places < 3) radius = maxOf(radius, 30.0)
    val last = list.maxBy { it.time }
    Estimate(bssid, last.ssid, lat, lon, radius, list.size, places, list.maxOf { it.rssi }, last)
}.sortedBy { it.radius }

/** Rough distance in metres from signal strength (log-distance model: -40 dBm at 1 m, exponent 2.7). */
fun rssiDistance(rssi: Int): Double = 10.0.pow((-40.0 - rssi) / 27.0).coerceIn(1.0, 80.0)

/**
 * Where to draw a device heard in the current scan: at its rough distance from the phone. Wi-Fi gives no
 * direction, so the angle only comes from the BSSID (stable per device) to spread the dots apart.
 */
fun devicePosition(lat: Double, lon: Double, bssid: String, rssi: Int): Pair<Double, Double> {
    val d = rssiDistance(rssi)
    val angle = Math.toRadians((bssid.hashCode().toLong() and 0xffff) % 360.0)
    val north = d * kotlin.math.cos(angle); val east = d * kotlin.math.sin(angle)
    return (lat + north / 110_540.0) to (lon + east / (111_320.0 * cos(Math.toRadians(lat))))
}
