package pl.aegismesh.sensor

import android.content.Context
import android.content.SharedPreferences
import java.io.File
import java.util.UUID

/** One id per installation, so several phones can be told apart on the laptop. */
internal fun sensorId(prefs: SharedPreferences): String {
    prefs.getString("sensor_id", null)?.let { return it }
    val id = "PHONE-" + UUID.randomUUID().toString().replace("-", "").take(8).uppercase()
    prefs.edit().putString("sensor_id", id).apply()
    return id
}

internal fun savedPlace(prefs: SharedPreferences): Place? = place(
    prefs.getString("place_building", "").orEmpty(), prefs.getString("place_floor", "").orEmpty(),
    prefs.getString("place_room", "").orEmpty())

internal fun storePlace(prefs: SharedPreferences, value: Place?) {
    prefs.edit().putString("place_building", value?.building.orEmpty()).putString("place_floor", value?.floor.orEmpty())
        .putString("place_room", value?.room.orEmpty()).apply()
}

private fun knownFile(context: Context) = File(context.filesDir, "known_routers.csv")
@Volatile private var cache: Pair<Long, List<KnownAp>>? = null

/** Known routers imported from CSV (ssid,bssid,security). Cached until the file changes. */
internal fun loadKnown(context: Context): List<KnownAp> {
    val file = knownFile(context)
    if (!file.exists()) return emptyList()
    cache?.takeIf { it.first == file.lastModified() }?.let { return it.second }
    val list = runCatching { parseKnown(file.readText()) }.getOrDefault(emptyList())
    cache = file.lastModified() to list
    return list
}

/** Validates first, then replaces the stored list. Returns the number of routers. */
internal fun storeKnown(context: Context, text: String): Int {
    val list = parseKnown(text)
    require(list.isNotEmpty()) { "W pliku nie ma poprawnych wierszy ssid,bssid,security." }
    knownFile(context).writeText(text)
    cache = null
    return list.size
}

/** Downloads the laptop's known-router list (its inventory). Quietly keeps the old list on failure. */
internal fun syncKnown(context: Context, prefs: SharedPreferences): Int? = runCatching {
    val origin = prefs.getString("origin", null) ?: return null
    val key = prefs.getString("key", null) ?: return null
    val rows = post(origin, "/known", org.json.JSONObject(), key, prefs.getString("fp", null)).getJSONArray("routers")
    if (rows.length() == 0) return 0
    fun cell(v: String) = "\"" + v.replace("\"", "\"\"") + "\""
    val csv = StringBuilder("ssid,bssid,security\n")
    for (i in 0 until rows.length()) {
        val r = rows.getJSONObject(i)
        csv.append(cell(r.optString("ssid"))).append(',').append(r.getString("bssid")).append(',').append(r.optString("security")).append('\n')
    }
    storeKnown(context, csv.toString())
}.getOrNull()

