package pl.aegismesh.sensor

import android.content.Context
import java.io.File

/** Pings collected on the radar map, kept only on the phone in one CSV file (the same format as the export). */
internal object PingStore {
    private const val LIMIT = 50_000
    private fun file(context: Context) = File(context.filesDir, "pings.csv")

    fun load(context: Context): MutableList<Ping> {
        val f = file(context)
        if (!f.exists()) return mutableListOf()
        return f.readLines().drop(1).mapNotNull(::parsePing).takeLast(LIMIT).toMutableList()
    }

    fun append(context: Context, pings: List<Ping>) {
        if (pings.isEmpty()) return
        val f = file(context)
        if (!f.exists()) f.writeText(PING_HEADER + "\n")
        f.appendText(pings.joinToString("") { pingLine(it) + "\n" })
    }

    fun text(context: Context): String = file(context).takeIf { it.exists() }?.readText() ?: (PING_HEADER + "\n")

    fun clear(context: Context) { file(context).delete() }
}
