package pl.aegismesh.sensor

import org.junit.Assert.*
import org.junit.Test

class ApiTest {
    @Test fun funnelAddressGetsHttps() {
        assertEquals("https://aegis.example.ts.net", serverOrigin("aegis.example.ts.net"))
        assertEquals("https://aegis.example.ts.net", serverOrigin(" https://aegis.example.ts.net/api/x?y=1 "))
    }
    @Test fun httpOnlyForPrivateLan() {
        assertEquals("http://192.168.1.20:8000", serverOrigin("http://192.168.1.20:8000/"))
        assertEquals("http://172.20.0.5:8000", serverOrigin("http://172.20.0.5:8000"))
        assertThrows(IllegalArgumentException::class.java) { serverOrigin("http://aegis.example.ts.net") }
        assertThrows(IllegalArgumentException::class.java) { serverOrigin("http://8.8.8.8:8000") }
        assertThrows(IllegalArgumentException::class.java) { serverOrigin("https://user:pass@aegis.example.ts.net") }
    }
    @Test fun lanSearchCoversTheSlash24WithoutThePhone() {
        val hosts = lanCandidates("192.168.1.37")
        assertEquals(253, hosts.size)
        assertTrue("192.168.1.1" in hosts && "192.168.1.254" in hosts)
        assertFalse("192.168.1.37" in hosts)
        assertThrows(IllegalArgumentException::class.java) { lanCandidates("100.64.1.2") }
    }
    @Test fun tokenExpiry() {
        assertTrue(expired("2000-01-01T00:00:00+00:00"))
        assertFalse(expired("2999-01-01T00:00:00+00:00"))
        assertTrue(expired(""))
        assertTrue(expired(null))
    }
}
