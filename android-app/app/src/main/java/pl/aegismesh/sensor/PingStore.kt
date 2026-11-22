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
        val lines = f.readLines()
        val pings = lines.drop(1).mapNotNull(::parsePing).takeLast(LIMIT).toMutableList()
        // a file from before ping ids: write it again with the new columns, so the export has them too
        if (lines.firstOrNull() != PING_HEADER) f.writeText(PING_HEADER + "\n" + pings.joinToString("") { pingLine(it) + "\n" })
        return pings
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
