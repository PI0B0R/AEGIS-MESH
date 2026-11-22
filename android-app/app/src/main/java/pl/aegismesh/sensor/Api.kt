package pl.aegismesh.sensor

import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import org.json.JSONObject

/**
 * The AEGIS MESH server API, the same one the web panel and the `aegis` command use.
 * Every call except login and health needs the token from login (it expires after 12 h).
 */

/** "https://host[:port]" from what the user typed. HTTP is allowed only for a private IPv4 address in the LAN. */
internal fun serverOrigin(input: String): String {
    val text = input.trim().let { if ("://" in it) it else "https://$it" }
    val uri = URI(text)
    val host = requireNotNull(uri.host) { "Wpisz pełny adres serwera." }
    require(uri.userInfo == null && (uri.scheme == "https" || (uri.scheme == "http" && privateIpv4(host)))) {
        "Wymagany HTTPS albo prywatny adres IPv4 w sieci lokalnej."
    }
    return URI(uri.scheme, null, host, uri.port, null, null, null).toString()
}

internal fun privateIpv4(host: String): Boolean {
    val octets = host.split(".").map { it.toIntOrNull() ?: -1 }
    return octets.size == 4 && octets.all { it in 0..255 } &&
        (octets[0] == 10 || octets.take(2) == listOf(192, 168) || (octets[0] == 172 && octets[1] in 16..31))
}

internal class HttpFailure(val code: Int, message: String) : Exception(message)

/** The server's own Polish message ("detail") when there is one. */
internal fun failureMessage(code: Int, body: String): String {
    val detail = runCatching { JSONObject(body).opt("detail") }.getOrNull()
    if (detail is String && detail.isNotBlank()) return detail
    return when (code) {
        401 -> "Sesja wygasła. Zaloguj się ponownie."
        403 -> "Twoja rola nie pozwala na tę zmianę."
        in 500..599 -> "Błąd serwera (HTTP $code)."
        else -> "Błąd połączenia (HTTP $code)."
    }
}

internal class Api(val origin: String, private val token: String = "") {
    fun get(path: String): JSONObject = call("GET", path, null)
    fun post(path: String, body: JSONObject = JSONObject()): JSONObject = call("POST", path, body)
    fun patch(path: String, body: JSONObject): JSONObject = call("PATCH", path, body)

    private fun call(method: String, path: String, body: JSONObject?, timeoutMs: Int = 8000): JSONObject {
        val connection = URL(origin + path).openConnection() as HttpURLConnection
        try {
            connection.instanceFollowRedirects = false
            connection.connectTimeout = timeoutMs
            connection.readTimeout = timeoutMs
            connection.setRequestProperty("Accept", "application/json")
            connection.requestMethod = method              // Android's HttpURLConnection (OkHttp) accepts PATCH
            if (token.isNotEmpty()) connection.setRequestProperty("Authorization", "Bearer $token")
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val status = connection.responseCode
            if (status !in 200..299) {
                val detail = connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                throw HttpFailure(status, failureMessage(status, detail))
            }
            val text = connection.inputStream.bufferedReader().use { it.readText() }
            return if (text.isBlank()) JSONObject() else JSONObject(text)
        } finally { connection.disconnect() }
    }

    companion object {
        /** Is an AEGIS MESH server answering here? Used by the LAN search, so the timeout is short. */
        fun isAegis(origin: String, timeoutMs: Int): Boolean = runCatching {
            Api(origin).call("GET", "/api/health", null, timeoutMs).optString("service") == "aegis-mesh"
        }.getOrDefault(false)
    }
}
