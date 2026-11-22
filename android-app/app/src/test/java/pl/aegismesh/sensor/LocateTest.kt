package pl.aegismesh.sensor

import org.junit.Assert.*
import org.junit.Test

class LocateTest {
    private val lat0 = 50.0647
    private val lon0 = 19.9234
    // about 1 m in degrees
    private val dLat = 1 / 110_540.0
    private fun ping(northM: Double, rssi: Int, bssid: String = "aa:bb:cc:dd:ee:01", acc: Float = 5f) =
        Ping(1000L, lat0 + northM * dLat, lon0, acc, bssid, "Biuro, 2p", rssi, 2437, "[WPA2-PSK-CCMP][ESS]")

    @Test fun csvRoundTripKeepsCommasInNames() {
        val p = ping(3.0, -61)
        val back = parsePing(pingLine(p))!!
        assertEquals(p.ssid, back.ssid)
        assertEquals(p.rssi, back.rssi)
        assertEquals(p.lat, back.lat, 1e-6)
        assertNull(parsePing("zly,wiersz"))
    }

    @Test fun strongerSignalPullsTheEstimate() {
        // strong at 0 m, weak at 40 m: the router is much closer to 0 m
        val e = estimate(listOf(ping(0.0, -45), ping(40.0, -85))).single()
        assertTrue(metres(lat0, lon0, e.lat, e.lon) < 5)
        assertEquals(2, e.places)
        assertEquals("niska", e.confidence)
        assertTrue(e.radius >= 30)
    }

    @Test fun morePlacesGiveMoreConfidence() {
        val list = (0 until 8).map { ping(it * 6.0, -50 - it) }
        val e = estimate(list).single()
        assertEquals(8, e.places)
        assertTrue(e.confidence == "wysoka" || e.confidence == "średnia")
    }

    @Test fun routersAreSeparated() {
        val list = listOf(ping(0.0, -50, "aa:00:00:00:00:01"), ping(10.0, -50, "aa:00:00:00:00:02"))
        assertEquals(2, estimate(list).size)
    }

    @Test fun closePingsCountAsOnePlace() {
        assertEquals(1, distinctPlaces(listOf(ping(0.0, -50), ping(2.0, -50), ping(4.0, -50))))
    }
}
