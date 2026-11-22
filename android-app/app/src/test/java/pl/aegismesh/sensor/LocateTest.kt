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

    @Test fun weakGpsPingsDoNotMoveTheEstimate() {
        val good = ping(0.0, -50)
        val bad = ping(200.0, -40, acc = 80f)
        val e = estimate(listOf(good, bad)).single()
        assertTrue(metres(lat0, lon0, e.lat, e.lon) < 1)
        assertEquals(1, e.pings)
        assertTrue(estimate(listOf(bad)).isEmpty())
    }

    @Test fun devicesAreDrawnAtTheirRoughDistance() {
        assertTrue(rssiDistance(-45) < rssiDistance(-75))
        val (lat, lon) = devicePosition(lat0, lon0, "aa:bb:cc:dd:ee:01", -67)
        val d = metres(lat0, lon0, lat, lon)
        assertEquals(rssiDistance(-67), d, 0.5)
        assertEquals(devicePosition(lat0, lon0, "aa:bb:cc:dd:ee:01", -67), devicePosition(lat0, lon0, "aa:bb:cc:dd:ee:01", -67))
    }

    @Test fun aJumpToAnotherCityRightAfterAScanIsIgnored() {
        val sec = 1_000_000_000L
        // Bydgoszcz -> Kraków (about 300 km) in 5 s from the network provider: impossible
        assertTrue(impossibleJump(53.1235, 18.0084, 0, 50.0647, 19.9234, 5 * sec, fromGps = false))
        // the same jump reported by real GPS is believed
        assertFalse(impossibleJump(53.1235, 18.0084, 0, 50.0647, 19.9234, 5 * sec, fromGps = true))
        // walking 30 m, or a 800 m correction of a coarse fix, is fine
        assertFalse(impossibleJump(53.1235, 18.0084, 0, 53.1238, 18.0084, 20 * sec, fromGps = false))
        assertFalse(impossibleJump(53.1235, 18.0084, 0, 53.1307, 18.0084, 2 * sec, fromGps = false))
        // a long train ride over hours is possible
        assertFalse(impossibleJump(53.1235, 18.0084, 0, 50.0647, 19.9234, 4 * 3600 * sec, fromGps = false))
    }

    @Test fun pingsCarryTheirOwnIdAndThePhonesAgentId() {
        val p = ping(0.0, -50).copy(uid = "0b9c6a4e-1f2d-4c3b-8a7e-5d6c7b8a9f01", agent = "3f2b8c1e-6d4a-4b7e-9c2f-1a2b3c4d5e6f")
        val back = parsePing(pingLine(p))!!
        assertEquals(p.uid, back.uid); assertEquals(p.agent, back.agent)
        assertTrue(PING_HEADER.endsWith(",uid,agent"))
        // a line saved before ids existed gets a stable id from its own text
        val old = "1000,50.0647000,19.9234000,5.0,aa:bb:cc:dd:ee:01,Biuro,-50,2437,[ESS]"
        assertEquals(parsePing(old)!!.uid, parsePing(old)!!.uid)
        assertEquals(36, parsePing(old)!!.uid.length)
        assertEquals("", parsePing(old)!!.agent)
    }
}
