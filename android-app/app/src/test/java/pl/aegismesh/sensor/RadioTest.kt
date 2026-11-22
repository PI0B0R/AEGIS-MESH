package pl.aegismesh.sensor

import org.junit.Assert.*
import org.junit.Test

class RadioTest {
    private val known = parseKnown("ssid,bssid,security\nAGH-LAB,02:00:00:00:00:01,WPA2-ENTERPRISE\n\"Biuro, 2p\",AA-BB-CC-00-11-22,WPA2-PERSONAL\nzly,xx,OPEN\n")
    private fun ap(ssid: String, bssid: String, flags: String) = SeenAp(ssid, bssid, -60, 2437, flags)

    @Test fun parsesCsvWithQuotesAndSkipsBadRows() {
        assertEquals(2, known.size)
        assertEquals(KnownAp("Biuro, 2p", "aa:bb:cc:00:11:22", "WPA2-PERSONAL"), known[1])
    }
    @Test fun knownRouter() = assertEquals(Status.KNOWN, classify(ap("AGH-LAB", "02:00:00:00:00:01", "[WPA2-EAP-CCMP][ESS]"), known).status)
    @Test fun knownBssidDowngradedIsSuspicious() =
        assertEquals(Status.SUSPICIOUS, classify(ap("AGH-LAB", "02:00:00:00:00:01", "[ESS]"), known).status)
    @Test fun evilTwinOpenCopy() =
        assertEquals(Status.SUSPICIOUS, classify(ap("AGH-LAB", "12:34:56:78:9a:bc", "[ESS]"), known).status)
    @Test fun newApOfKnownNetworkWithSameSecurity() =
        assertEquals(Status.NEW, classify(ap("AGH-LAB", "12:34:56:78:9a:bc", "[RSN-EAP-CCMP][ESS]"), known).status)
    @Test fun phoneHotspot() {
        assertEquals(Status.HOTSPOT, classify(ap("iPhone (Jan)", "12:34:56:78:9a:bc", "[RSN-PSK-CCMP][ESS]"), known).status)
        assertEquals(Status.HOTSPOT, classify(ap("DIRECT-xy-Printer", "00:11:22:33:44:55", "[WPA2-PSK-CCMP][ESS]"), known).status)
        assertEquals(Status.HOTSPOT, classify(ap("Siec", "a6:11:22:33:44:55", "[RSN-SAE-CCMP][ESS]"), known).status)
    }
    @Test fun ordinaryUnknownRouter() =
        assertEquals(Status.NEW, classify(ap("Kawiarnia", "00:11:22:33:44:55", "[WPA2-PSK-CCMP][ESS]"), known).status)
    @Test fun securityModesMatchServer() {
        assertEquals("OPEN", securityMode("[ESS][WPS]"))
        assertEquals("WPA2-ENTERPRISE", securityMode("[RSN-EAP-CCMP][ESS]"))
        assertEquals("WPA3-PERSONAL", securityMode("[RSN-SAE-CCMP][ESS]"))
        assertEquals("UNKNOWN", securityMode("[IBSS]"))
    }
    @Test fun hotColdTrend() {
        assertEquals("Cieplej", trend(-70, -64))
        assertEquals("Zimniej", trend(-60, -66))
        assertEquals("Bez zmian", trend(-60, -61))
        assertEquals(0, signalPercent(-100)); assertEquals(100, signalPercent(-30))
    }
    @Test fun coneNarrowsWhenClose() {
        assertEquals(8.0, coneHalfAngle(-35.0), 0.01)
        assertEquals(70.0, coneHalfAngle(-95.0), 0.01)
        assertTrue(coneHalfAngle(-50.0) < coneHalfAngle(-75.0))
    }
    @Test fun smoothingDampsJumps() {
        assertEquals(-70.0, smooth(null, -70), 0.01)
        assertEquals(-66.0, smooth(-70.0, -60), 0.01)
    }
}
