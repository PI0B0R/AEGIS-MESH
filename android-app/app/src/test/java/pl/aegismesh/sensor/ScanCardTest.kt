package pl.aegismesh.sensor

import org.junit.Assert.assertEquals
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
}
