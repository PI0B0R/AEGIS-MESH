package pl.aegismesh.sensor

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ScanCardTest {
    @Test fun durationIsShortAndReadable() {
        assertEquals("45 s", duration(45))
        assertEquals("3 min 05 s", duration(185))
        assertEquals("1 h 02 min", duration(3720))
    }
    @Test fun signalFromLatency() {
        assertEquals("bardzo dobre", signalLabel(120))
        assertEquals("dobre", signalLabel(800))
        assertEquals("słabe", signalLabel(3000))
    }
    @Test fun progressMovesBetweenFinishedParts() {
        // 256 addresses, 64 done; one running part of 64 halfway through its expected 64 s
        assertEquals(37.5, liveProgress(256, 64, listOf(64 to 32.0), 1.0), 0.01)
        // a running part never counts as more than 95 %
        assertEquals(48.75, liveProgress(256, 64, listOf(64 to 500.0), 1.0), 0.01)
        assertEquals(25.0, liveProgress(256, 64, emptyList(), 1.0), 0.01)
        assertEquals(100.0, liveProgress(0, 0, emptyList(), 1.0), 0.01)
    }
    @Test fun searchIgnoresCase() {
        assertTrue(matches("10.250.160.5 nginx", "NGINX"))
        assertTrue(matches("cokolwiek", "  "))
        assertFalse(matches("10.250.160.5", "apache"))
    }
    @Test fun alertFilters() {
        assertTrue(keep("nowe", 5, "waiting", 0))
        assertFalse(keep("potwierdzone", 5, "waiting", 0))
        assertTrue(keep("fałszywy-alarm", 4, "checked", 4))
        assertFalse(keep("nowe", 4, "all", 5))
        assertTrue(keep("rozwiązane", 5, "all", 0))
    }
}
