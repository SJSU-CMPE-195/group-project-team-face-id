package com.bass.app

import java.io.Closeable
import java.util.ArrayDeque
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch

internal class ApiScheduler(
    private val generation: Long,
    private val isGenerationCurrent: () -> Boolean,
) : Closeable {
    private val lock = Any()
    private val cancelQueue = ArrayDeque<ScheduledWork>()
    private val normalQueue = ArrayDeque<ScheduledWork>()
    private val wakeups = Channel<Unit>(Channel.CONFLATED)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val closed = AtomicBoolean(false)
    private var activeCount = 0

    init {
        scope.launch { runWorker() }
    }

    suspend fun <T> submit(
        cancelPriority: Boolean = false,
        block: suspend () -> T,
    ): T {
        checkCurrent()
        val work = TypedScheduledWork(generation, block)
        synchronized(lock) {
            checkCurrent()
            if (cancelQueue.size + normalQueue.size + activeCount >= MAX_WORK_ITEMS) {
                throw BleSchedulerBusyException()
            }
            if (cancelPriority) cancelQueue.addLast(work) else normalQueue.addLast(work)
        }
        wakeups.trySend(Unit)
        return try {
            work.await()
        } catch (error: CancellationException) {
            val removed =
                synchronized(lock) {
                    if (work.started) {
                        false
                    } else {
                        cancelQueue.remove(work) || normalQueue.remove(work)
                    }
                }
            if (removed) work.cancel(error)
            throw error
        }
    }

    override fun close() {
        if (!closed.compareAndSet(false, true)) return
        val queued =
            synchronized(lock) {
                buildList {
                    addAll(cancelQueue)
                    addAll(normalQueue)
                    cancelQueue.clear()
                    normalQueue.clear()
                }
            }
        val error = BleLinkUnusableException("BLE API scheduler is closed")
        queued.forEach { it.cancel(error) }
        wakeups.close()
    }

    private suspend fun runWorker() {
        for (ignored in wakeups) {
            while (true) {
                val work =
                    synchronized(lock) {
                        val next =
                            if (cancelQueue.isNotEmpty()) cancelQueue.removeFirst()
                            else if (normalQueue.isNotEmpty()) normalQueue.removeFirst()
                            else null
                        next?.also {
                            it.started = true
                            activeCount++
                        }
                    } ?: break
                try {
                    if (closed.get() || !isGenerationCurrent()) {
                        work.cancel(BleLinkUnusableException("BLE connection generation changed"))
                    } else {
                        work.run()
                    }
                } finally {
                    synchronized(lock) { activeCount-- }
                }
            }
        }
    }

    private fun checkCurrent() {
        if (closed.get() || !isGenerationCurrent()) {
            throw BleLinkUnusableException("BLE connection generation is no longer usable")
        }
    }

    companion object {
        private const val MAX_WORK_ITEMS = 16
    }
}

private interface ScheduledWork {
    var started: Boolean

    suspend fun run()

    fun cancel(error: Throwable)
}

private class TypedScheduledWork<T>(
    private val generation: Long,
    private val block: suspend () -> T,
) : ScheduledWork {
    private val result = CompletableDeferred<T>()

    override var started: Boolean = false

    override suspend fun run() {
        try {
            result.complete(block())
        } catch (error: Throwable) {
            result.completeExceptionally(error)
        }
    }

    override fun cancel(error: Throwable) {
        result.completeExceptionally(error)
    }

    suspend fun await(): T = result.await()

    override fun toString(): String = "ScheduledWork(generation=$generation, started=$started)"
}
