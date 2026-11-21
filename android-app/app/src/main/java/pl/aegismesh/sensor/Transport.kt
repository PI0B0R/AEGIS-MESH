package pl.aegismesh.sensor

import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import org.json.JSONObject

internal fun serverOrigin(input: String): String {
    val uri = URI(input.trim())
    val host = requireNotNull(uri.host) { "Wpisz pełny adres serwera." }
    val octets = host.split(".").map { it.toIntOrNull() ?: -1 }
    val local = octets.size == 4 && octets.all { it in 0..255 } &&
        (octets[0] == 10 || octets.take(2) == listOf(192,168) || (octets[0] == 172 && octets[1] in 16..31))
    require(uri.userInfo == null && (uri.scheme == "https" || (uri.scheme == "http" && local))) {
        "Wymagany HTTPS lub prywatny adres IPv4 w LAN."
    }
    return URI(uri.scheme, null, host, uri.port, null, null, null).toString()
}

internal class HttpFailure(val code: Int, message: String) : Exception("HTTP " + code + " · " + message)

internal fun post(origin: String, path: String, body: JSONObject, key: String = ""): JSONObject {
    val connection = URL(origin + path).openConnection() as HttpURLConnection
    try {
        connection.instanceFollowRedirects = false
        connection.requestMethod = "POST"
        connection.connectTimeout = 6000
        connection.readTimeout = 7000
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", "application/json")
        connection.setRequestProperty("Origin", origin)
        if (key.isNotEmpty()) connection.setRequestProperty("Authorization", "Bearer " + key)
        connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
        val status = connection.responseCode
        if (status !in 200..299) {
            val detail = connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
            throw HttpFailure(status, runCatching { JSONObject(detail).getString("error") }.getOrDefault("Błąd połączenia"))
        }
        return JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
    } finally { connection.disconnect() }
}

internal fun wifiChannel(f: Int): Int? = when {
    f == 2484 -> 14
    f in 2412..2472 -> (f - 2407) / 5
    f in 4910..4980 -> (f - 4000) / 5
    f in 5005..5895 -> (f - 5000) / 5
    f == 5935 -> 2
    f in 5955..7115 -> (f - 5950) / 5
    else -> null
}
