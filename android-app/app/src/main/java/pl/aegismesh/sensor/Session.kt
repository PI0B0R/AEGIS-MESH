package pl.aegismesh.sensor

import android.content.Context
import android.content.SharedPreferences
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import java.net.Inet4Address
import java.time.OffsetDateTime
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import org.json.JSONObject

/**
 * The logged-in session, kept in private app storage: server address, token, user and role.
 * Only the token is kept, never the password.
 */
internal class Session(context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences("aegis", Context.MODE_PRIVATE)

    val origin: String get() = prefs.getString("origin", "").orEmpty()
    val username: String get() = prefs.getString("username", "").orEmpty()
    val role: String get() = prefs.getString("role", "").orEmpty()
    val canEditFindings: Boolean get() = prefs.getBoolean("can_analityk", false)
    val loggedIn: Boolean get() = prefs.getString("token", "").orEmpty().isNotEmpty() && !expired(prefs.getString("expires", ""))
    val serverLabel: String get() = origin.substringAfter("://")

    fun api(): Api = Api(origin, prefs.getString("token", "").orEmpty())

    /** Logs in, then reads the role so the app can hide buttons the user may not use. */
    fun login(origin: String, username: String, password: String) {
        val data = Api(origin).post("/api/auth/login", JSONObject().put("username", username).put("password", password))
        val token = data.getString("token")
        val me = Api(origin, token).get("/api/auth/me")
        prefs.edit().putString("origin", origin).putString("token", token).putString("expires", data.optString("expires_at"))
            .putString("username", me.optString("username", username)).putString("role", me.optString("role"))
            .putBoolean("can_analityk", me.optJSONObject("can")?.optBoolean("analityk") == true).apply()
    }

    /** Ends the session on the server too (best effort) and forgets the token. The address stays. */
    fun logout() {
        val api = api()
        Thread { runCatching { api.post("/api/auth/logout") } }.start()
        forget()
    }

    fun forget() { prefs.edit().remove("token").remove("expires").remove("role").remove("can_analityk").apply() }

    /** Ids of P4-5 findings the phone already announced, so each one is announced once. */
    var announced: Set<String>
        get() = prefs.getStringSet("announced", emptySet()).orEmpty()
        set(value) { prefs.edit().putStringSet("announced", value).apply() }

    companion object {
        /** Address built into the APK (the laptop's Tailscale Funnel address), empty when none was given. */
        val BUILT_IN: String = BuildConfig.AEGIS_URL.trim()
    }
}

internal fun expired(iso: String?): Boolean =
    runCatching { OffsetDateTime.parse(iso).isBefore(OffsetDateTime.now()) }.getOrDefault(true)

/** Addresses to try in the phone's /24: the usual router addresses are not skipped, the phone itself is. */
internal fun lanCandidates(phoneIp: String): List<String> {
    val parts = phoneIp.split(".")
    require(parts.size == 4 && privateIpv4(phoneIp)) { "Telefon nie jest w prywatnej sieci IPv4." }
    val prefix = parts.take(3).joinToString(".")
    return (1..254).map { "$prefix.$it" }.filter { it != phoneIp }
}

/** The phone's IPv4 address on Wi-Fi or Ethernet, if it has one. */
internal fun localIpv4(context: Context): String? {
    val manager = context.getSystemService(ConnectivityManager::class.java)
    val network = manager.activeNetwork ?: return null
    val caps = manager.getNetworkCapabilities(network) ?: return null
    if (!caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) && !caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) return null
    return manager.getLinkProperties(network)?.linkAddresses?.map { it.address }
        ?.firstOrNull { it is Inet4Address && !it.isLoopbackAddress }?.hostAddress
}

/**
 * Backup mode: looks for an AEGIS MESH server on port 8000 in the same Wi-Fi network.
 * The laptop must listen in the LAN (AEGIS_BIND=0.0.0.0). Takes a few seconds; returns the origin or null.
 */
internal fun findInLan(context: Context, port: Int = 8000): String? {
    val ip = localIpv4(context) ?: return null
    val candidates = runCatching { lanCandidates(ip) }.getOrNull() ?: return null
    val pool = Executors.newFixedThreadPool(48)
    try {
        val jobs = candidates.map { host -> pool.submit(Callable { "http://$host:$port".takeIf { Api.isAegis(it, 700) } }) }
        return jobs.firstNotNullOfOrNull { runCatching { it.get(10, TimeUnit.SECONDS) }.getOrNull() }
    } finally { pool.shutdownNow() }
}
