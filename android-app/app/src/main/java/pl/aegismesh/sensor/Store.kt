package pl.aegismesh.sensor

import android.content.Context
import java.io.File

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
