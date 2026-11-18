package ai.aura.app.automation.actions.builtin

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * T-0154: the owner's phone ran a whole core for Jarvis and power_report could not say which
 * thread. These pin the /proc/self/task/<tid>/stat parse and the 1-second ranking.
 */
class ThreadCpuTest {

    @Test fun `a stat line gives the thread's name and its user plus system ticks`() {
        // Fields after "(comm) ": state ppid pgrp session tty tpgid flags minflt cminflt majflt cmajflt utime stime ...
        val line = "4242 (DefaultDispatch) R 4100 4100 0 0 -1 4194368 120 0 3 0 812 95 0 0 20 0 41 0 5000"
        assertEquals("DefaultDispatch" to 907L, ThreadCpu.parse(line))
    }

    @Test fun `a name with spaces and parentheses is read up to the last parenthesis`() {
        val line = "17 (Wake (word) 2) S 1 1 0 0 -1 0 0 0 0 0 40 2 0 0 20 0 1 0 9"
        assertEquals("Wake (word) 2" to 42L, ThreadCpu.parse(line))
    }

    @Test fun `a short or garbled line is skipped, not a crash`() {
        assertNull(ThreadCpu.parse("17 (x) S 1 2"))
        assertNull(ThreadCpu.parse("no parenthesis here"))
    }

    @Test fun `the busiest threads are ranked by ticks spent in the window, same names added up`() {
        val before = mapOf(1 to ("main" to 100L), 2 to ("pool" to 10L), 3 to ("pool" to 10L), 4 to ("idle" to 5L))
        val after = mapOf(1 to ("main" to 105L), 2 to ("pool" to 60L), 3 to ("pool" to 50L), 4 to ("idle" to 5L), 5 to ("new" to 20L))
        val ranked = ThreadCpu.busiest(before, after, ticksPerSecond = 100, windowMs = 1000, top = 3)
        assertEquals(listOf("pool" to 90.0, "new" to 20.0, "main" to 5.0), ranked)
    }
}
