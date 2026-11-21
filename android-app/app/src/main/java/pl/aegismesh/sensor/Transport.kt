package pl.aegismesh.sensor

import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.X509TrustManager
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

/** SHA-256 of a certificate as lowercase hex, the same form the laptop puts in the QR code. */
internal fun sha256Hex(der: ByteArray): String =
    MessageDigest.getInstance("SHA-256").digest(der).joinToString("") { "%02x".format(it) }

/**
 * Certificate pinning: the laptop's self-signed certificate is trusted only if its SHA-256
 * matches the fingerprint from the pairing QR. No CA has to be installed on the phone.
 */
private val pinned = HashMap<String, SSLSocketFactory>()
internal fun pinnedFactory(fingerprint: String): SSLSocketFactory = synchronized(pinned) {
    pinned.getOrPut(fingerprint) {
        val trust = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) = throw CertificateException()
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                val seen = sha256Hex(chain.first().encoded)
                if (!MessageDigest.isEqual(seen.toByteArray(), fingerprint.lowercase().toByteArray()))
                    throw CertificateException("Certyfikat laptopa nie zgadza się z kodem QR.")
            }
            override fun getAcceptedIssuers() = arrayOf<X509Certificate>()
        }
        SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), SecureRandom()) }.socketFactory
    }
}

internal fun post(origin: String, path: String, body: JSONObject, key: String = "", fingerprint: String? = null): JSONObject {
    val connection = URL(origin + path).openConnection() as HttpURLConnection
    if (connection is HttpsURLConnection && !fingerprint.isNullOrEmpty()) {
        connection.sslSocketFactory = pinnedFactory(fingerprint)
        // The pinned leaf certificate already proves who the laptop is.
        connection.hostnameVerifier = HostnameVerifier { _, _ -> true }
    }
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

/** Pairing QR from the laptop: https://IP:8767/phone-beacon#t=TOKEN&fp=SHA256 (older codes: #TOKEN). */
data class Invite(val origin: String, val token: String, val fingerprint: String?)
internal fun parseInvite(text: String): Invite? = runCatching {
    val uri = java.net.URI(text.trim())
    val origin = serverOrigin(text)
    val fragment = uri.rawFragment.orEmpty()
    val fields = fragment.split('&').mapNotNull { part -> part.split('=', limit = 2).takeIf { it.size == 2 }?.let { it[0] to it[1] } }.toMap()
    val token = fields["t"] ?: fragment
    val fp = fields["fp"]?.lowercase()
    if (!Regex("[A-Za-z0-9_-]{43}").matches(token)) return null
    if (fp != null && !Regex("[0-9a-f]{64}").matches(fp)) return null
    Invite(origin, token, fp)
}.getOrNull()
