package ai.jarvis.app.automation.actions.builtin

import java.io.File

/**
 * Which of this app's own threads are spending its CPU (T-0154): the owner's phone ran a whole
 * core for Jarvis and power_report could only give the total. Reads /proc/self/task, which an
 * app may read about itself; it says nothing about the WebView's renderer, a separate process.
 */
object ThreadCpu {

    /** One /proc/<pid>/task/<tid>/stat line: the thread's name and its user+system ticks, or null. */
    fun parse(stat: String): Pair<String, Long>? {
        val open = stat.indexOf('(')
        val close = stat.lastIndexOf(')')
        if (open < 0 || close < open || close + 2 > stat.length) return null
        // After "(comm) " the fields run from state (field 3); utime and stime are 14 and 15.
        val fields = stat.substring(close + 2).split(' ')
        val user = fields.getOrNull(11)?.toLongOrNull() ?: return null
        val system = fields.getOrNull(12)?.toLongOrNull() ?: return null
        return stat.substring(open + 1, close) to user + system
    }

    /** Every thread of this process now: tid -> (name, ticks). */
    fun sample(): Map<Int, Pair<String, Long>> =
        File("/proc/self/task").listFiles().orEmpty().mapNotNull { dir ->
            val tid = dir.name.toIntOrNull() ?: return@mapNotNull null
            val stat = runCatching { File(dir, "stat").readText() }.getOrNull() ?: return@mapNotNull null
            parse(stat)?.let { tid to it }
        }.toMap()

    /**
     * The [top] thread names by share of one core between two samples [windowMs] apart.
     * Threads of one name (a dispatcher's pool) are added up; a thread that started in the
     * window counts from zero, one that ended is gone from [after] and drops out.
     */
    fun busiest(
        before: Map<Int, Pair<String, Long>>,
        after: Map<Int, Pair<String, Long>>,
        ticksPerSecond: Long,
        windowMs: Long,
        top: Int = 8,
    ): List<Pair<String, Double>> {
        val spent = HashMap<String, Long>()
        for ((tid, now) in after) {
            val delta = now.second - (before[tid]?.second ?: 0L)
            if (delta > 0) spent.merge(now.first, delta, Long::plus)
        }
        val perCore = ticksPerSecond * windowMs / 1000.0
        return spent.entries.sortedByDescending { it.value }.take(top)
            .map { it.key to Math.round(it.value * 1000.0 / perCore) / 10.0 }
    }
}
