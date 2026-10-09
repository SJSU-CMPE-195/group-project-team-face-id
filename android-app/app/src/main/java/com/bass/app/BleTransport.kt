package com.bass.app

import android.bluetooth.BluetoothDevice
import android.content.Context
import java.io.Closeable
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.joinAll
import kotlinx.coroutines.launch
import kotlinx.coroutines.supervisorScope
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull

class BleTransport private constructor(
    private val gatt: BleGattConnection,
) : Closeable {
    private val exchangeMutex = Mutex()
    private val reusable = AtomicBoolean(true)

    val generation: Long
        get() = gatt.generation

    val isReusable: Boolean
        get() = reusable.get() && gatt.isReusable

    val endpointLabel: String
        get() = gatt.endpointLabel

    internal suspend fun <T> exchange(block: suspend BleHttpExchange<T>.() -> T): T =
        exchangeMutex.withLock {
            ensureReusable()
            exchangeOnce(block)
        }

    override fun close() {
        poison(BleLinkUnusableException("BLE transport closed"))
    }

    private suspend fun <T> exchangeOnce(block: suspend BleHttpExchange<T>.() -> T): T =
        supervisorScope {
            val listener =
                withContext(Dispatchers.IO) {
                    ServerSocket().apply {
                        reuseAddress = false
                        bind(InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 1)
                        soTimeout = BleProtocol.CALLBACK_TIMEOUT_MILLIS.toInt()
                    }
                }
            val exchange = BleHttpExchange<T>(listener.localPort)
            val bridge = BridgeSession(listener, gatt, exchange)
            val bridgeJob = async(Dispatchers.IO) { bridge.run() }
            var completedBlockFailure: Throwable? = null
            val value: T =
                try {
                    withTimeout(BleProtocol.EXCHANGE_TIMEOUT_MILLIS) {
                        block(exchange)
                    }
                } catch (error: Throwable) {
                    val committed = exchange.committedResponse
                    if (committed == null) {
                        bridge.abort()
                        bridgeJob.cancel()
                        poison(error)
                        if (error is TimeoutCancellationException) {
                            throw BleLinkUnusableException("BLE API exchange timed out", error)
                        }
                        throw error
                    }
                    completedBlockFailure = error
                    committed.value
                }
            if (!exchange.isResponseComplete) {
                val error = BleLinkUnusableException("HTTP response was not fully framed")
                bridge.abort()
                bridgeJob.cancel()
                poison(error)
                throw error
            }

            val closeFailure = runCatching { bridgeJob.await() }.exceptionOrNull()
            if (closeFailure != null) {
                // The complete HTTP result remains authoritative; only future reuse is rejected.
                bridge.abort()
                poison(closeFailure)
            }
            completedBlockFailure?.let { poison(it) }
            value
        }

    private fun ensureReusable() {
        if (!isReusable) throw BleLinkUnusableException("BLE transport is not reusable")
    }

    private fun poison(cause: Throwable) {
        if (!reusable.compareAndSet(true, false)) return
        gatt.close()
    }

    companion object {
        suspend fun connect(context: Context, device: BluetoothDevice): BleTransport =
            BleTransport(BleGattConnection.connect(context, device))
    }
}

internal class BleHttpExchange<T> internal constructor(port: Int) {
    private val completionNanos = AtomicLong(0)
    private val committed = AtomicReference<CommittedResponse<T>?>(null)

    val baseUrl: String = "https://127.0.0.1:$port"

    val isResponseComplete: Boolean
        get() = completionNanos.get() != 0L

    val responseCompletedAtNanos: Long?
        get() = completionNanos.get().takeIf { it != 0L }

    val committedResponse: CommittedResponse<T>?
        get() = committed.get()

    fun commitResponse(value: T): T {
        if (committed.compareAndSet(null, CommittedResponse(value))) {
            completionNanos.compareAndSet(0, System.nanoTime())
        }
        return value
    }
}

internal data class CommittedResponse<T>(val value: T)

private class BridgeSession(
    private val listener: ServerSocket,
    private val gatt: BleGattConnection,
    private val exchange: BleHttpExchange<*>,
) {
    private val aborted = AtomicBoolean(false)

    @Volatile private var localSocket: Socket? = null

    suspend fun run() {
        val socket =
            try {
                withContext(Dispatchers.IO) { listener.accept() }
            } catch (error: SocketTimeoutException) {
                throw BleLinkUnusableException("Loopback HTTPS client did not connect", error)
            } finally {
                runCatching { listener.close() }
            }
        localSocket = socket
        socket.tcpNoDelay = true
        try {
            openTunnel()
            pump(socket)
        } finally {
            runCatching { socket.close() }
            localSocket = null
        }
    }

    fun abort() {
        if (!aborted.compareAndSet(false, true)) return
        runCatching { listener.close() }
        runCatching { localSocket?.close() }
    }

    private suspend fun openTunnel() {
        gatt.writeTunnel(BleProtocol.openFrame)
        withTimeout(BleProtocol.CALLBACK_TIMEOUT_MILLIS) {
            while (true) {
                when (val frame = readFrame()) {
                    ServerFrame.OpenOk -> return@withTimeout
                    ServerFrame.Wait -> delay(BleProtocol.WAIT_DELAY_MILLIS)
                    is ServerFrame.Error -> throw BleRemoteException(frame.code)
                    else -> throw BleProtocolException("Unexpected frame while opening tunnel")
                }
            }
        }
    }

    private suspend fun pump(socket: Socket) =
        supervisorScope {
            val payloadCap = gatt.payloadCap
            val channelCapacity = maxOf(1, BleProtocol.BUFFER_BYTES / payloadCap)
            val uplink = Channel<ByteArray>(channelCapacity)
            val downlink = Channel<ByteArray>(channelCapacity)
            val readerDone = CompletableDeferred<IoOutcome>()
            val writerDone = CompletableDeferred<IoOutcome>()

            val reader =
                launch(Dispatchers.IO) {
                    var failure: Throwable? = null
                    try {
                        val input = socket.getInputStream()
                        val buffer = ByteArray(payloadCap)
                        while (true) {
                            val count = input.read(buffer)
                            if (count < 0) break
                            if (count == 0) continue
                            uplink.send(buffer.copyOf(count))
                        }
                    } catch (error: Throwable) {
                        failure = error
                    } finally {
                        readerDone.complete(IoOutcome(failure))
                        uplink.close(failure)
                    }
                }
            val writer =
                launch(Dispatchers.IO) {
                    var failure: Throwable? = null
                    try {
                        val output = socket.getOutputStream()
                        for (bytes in downlink) output.write(bytes)
                        output.flush()
                    } catch (error: Throwable) {
                        failure = error
                    } finally {
                        writerDone.complete(IoOutcome(failure))
                        if (failure != null) {
                            downlink.cancel(CancellationException("Loopback writer failed", failure))
                        }
                    }
                }

            var pendingUplink: ByteArray? = null
            var nextReadAtNanos = 0L
            var lastDirection: PumpDirection? = null
            var closeSentAtNanos: Long? = null
            var teardownFailure: Throwable? = null
            var closed = false

            try {
                while (!closed) {
                    if (pendingUplink == null) {
                        val received = uplink.tryReceive()
                        if (received.isSuccess) pendingUplink = received.getOrThrow()
                        received.exceptionOrNull()?.let { throw it }
                    }

                    if (readerDone.isCompleted) {
                        val outcome = readerDone.await()
                        outcome.failure?.let { throw it }
                    }
                    if (writerDone.isCompleted) {
                        val failure = writerDone.await().failure
                        if (failure != null) {
                            if (!exchange.isResponseComplete) throw failure
                            teardownFailure = teardownFailure ?: failure
                        }
                    }

                    val now = System.nanoTime()
                    val responseCompletedAt = exchange.responseCompletedAtNanos
                    if (
                        responseCompletedAt != null &&
                            !readerDone.isCompleted &&
                            elapsedMillis(responseCompletedAt, now) >=
                                BleProtocol.LOCAL_EOF_TIMEOUT_MILLIS
                    ) {
                        throw BleLinkUnusableException("Loopback TLS client did not close")
                    }

                    val uplinkDrained = readerDone.isCompleted && pendingUplink == null
                    if (uplinkDrained && closeSentAtNanos == null) {
                        if (exchange.isResponseComplete) {
                            closeSentAtNanos = System.nanoTime()
                            try {
                                withTimeout(BleProtocol.CLOSE_TIMEOUT_MILLIS) {
                                    gatt.writeTunnel(BleProtocol.closeFrame)
                                }
                            } catch (error: TimeoutCancellationException) {
                                throw BleLinkUnusableException("BLE CLOSE write timed out", error)
                            }
                            lastDirection = PumpDirection.WRITE
                            continue
                        }
                    }

                    val closeStartedAt = closeSentAtNanos
                    if (
                        closeStartedAt != null &&
                            elapsedMillis(closeStartedAt, now) >= BleProtocol.CLOSE_TIMEOUT_MILLIS
                    ) {
                        throw BleLinkUnusableException("BLE CLOSE was not acknowledged")
                    }

                    val canWrite = pendingUplink != null && closeStartedAt == null
                    val canRead = now >= nextReadAtNanos
                    val writeNext =
                        canWrite &&
                            (!canRead ||
                                lastDirection == null ||
                                lastDirection == PumpDirection.READ)
                    if (writeNext) {
                        val bytes = checkNotNull(pendingUplink)
                        gatt.writeTunnel(BleProtocol.dataFrame(bytes))
                        pendingUplink = null
                        lastDirection = PumpDirection.WRITE
                        continue
                    }

                    if (canRead) {
                        val frame =
                            if (closeStartedAt == null) {
                                readFrame()
                            } else {
                                val remaining =
                                    BleProtocol.CLOSE_TIMEOUT_MILLIS -
                                        elapsedMillis(closeStartedAt, System.nanoTime())
                                if (remaining <= 0) {
                                    throw BleLinkUnusableException("BLE CLOSE was not acknowledged")
                                }
                                try {
                                    withTimeout(remaining) { readFrame() }
                                } catch (error: TimeoutCancellationException) {
                                    throw BleLinkUnusableException(
                                        "BLE CLOSE was not acknowledged",
                                        error,
                                    )
                                }
                            }
                        when (frame) {
                            is ServerFrame.Data -> {
                                val failure = writerDone.takeIf { it.isCompleted }?.await()?.failure
                                if (failure == null) {
                                    try {
                                        downlink.send(frame.bytes)
                                    } catch (error: CancellationException) {
                                        if (!exchange.isResponseComplete) throw error
                                        teardownFailure = teardownFailure ?: error.cause ?: error
                                    }
                                } else if (!exchange.isResponseComplete) {
                                    throw failure
                                } else {
                                    teardownFailure = teardownFailure ?: failure
                                }
                                nextReadAtNanos = System.nanoTime()
                            }
                            ServerFrame.Wait -> {
                                nextReadAtNanos =
                                    System.nanoTime() +
                                        BleProtocol.WAIT_DELAY_MILLIS * NANOS_PER_MILLISECOND
                            }
                            ServerFrame.Closed -> {
                                if (closeSentAtNanos == null) {
                                    throw BleProtocolException("CLOSED arrived before CLOSE")
                                }
                                closed = true
                            }
                            is ServerFrame.Error -> throw BleRemoteException(frame.code)
                            ServerFrame.OpenOk ->
                                throw BleProtocolException("Duplicate OPEN_OK frame")
                        }
                        lastDirection = PumpDirection.READ
                        continue
                    }

                    if (pendingUplink == null && !readerDone.isCompleted) {
                        val waitMillis =
                            maxOf(
                                1L,
                                (nextReadAtNanos - System.nanoTime()) / NANOS_PER_MILLISECOND,
                            )
                        val received =
                            withTimeoutOrNull(waitMillis) {
                                uplink.receiveCatching()
                            }
                        if (received?.isSuccess == true) pendingUplink = received.getOrThrow()
                        received?.exceptionOrNull()?.let { throw it }
                    } else {
                        delay(1)
                    }
                }
            } finally {
                downlink.close()
                if (!closed) {
                    runCatching { socket.shutdownInput() }
                    runCatching { socket.shutdownOutput() }
                    runCatching { socket.close() }
                    reader.cancel()
                    writer.cancel()
                }
                withTimeoutOrNull(BleProtocol.LOCAL_EOF_TIMEOUT_MILLIS) {
                    joinAll(reader, writer)
                } ?: run {
                    teardownFailure =
                        teardownFailure
                            ?: BleLinkUnusableException("Loopback bridge did not drain")
                    reader.cancel()
                    writer.cancel()
                }
            }
            teardownFailure?.let { throw BleLinkUnusableException("Loopback drain failed", it) }
        }

    private suspend fun readFrame(): ServerFrame =
        BleProtocol.parseServerFrame(gatt.readTunnel(), gatt.payloadCap)

    private fun elapsedMillis(startNanos: Long, endNanos: Long): Long =
        (endNanos - startNanos) / NANOS_PER_MILLISECOND

    private data class IoOutcome(val failure: Throwable?)

    private enum class PumpDirection {
        READ,
        WRITE,
    }

    companion object {
        private const val NANOS_PER_MILLISECOND = 1_000_000L
    }
}
