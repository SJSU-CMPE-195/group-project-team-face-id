package com.bass.app

import java.util.UUID

object BleProtocol {
    val SERVICE_UUID: UUID = UUID.fromString("c74f82e0-a691-4f6f-a6b3-1220c3f7679a")
    val PROTOCOL_UUID: UUID = UUID.fromString("c74f82e1-a691-4f6f-a6b3-1220c3f7679a")
    val TUNNEL_UUID: UUID = UUID.fromString("c74f82e2-a691-4f6f-a6b3-1220c3f7679a")

    const val VERSION: Int = 1
    const val REQUESTED_MTU: Int = 517
    const val DEFAULT_MTU: Int = 23
    const val MAX_ATTRIBUTE_VALUE_BYTES: Int = 512
    const val BUFFER_BYTES: Int = 32 * 1024
    const val WAIT_DELAY_MILLIS: Long = 100
    const val CALLBACK_TIMEOUT_MILLIS: Long = 10_000
    const val LOCAL_EOF_TIMEOUT_MILLIS: Long = 2_000
    const val CLOSE_TIMEOUT_MILLIS: Long = 5_000
    const val EXCHANGE_TIMEOUT_MILLIS: Long = 60_000

    private const val CLIENT_OPEN: Int = 0x01
    private const val CLIENT_DATA: Int = 0x02
    private const val CLIENT_CLOSE: Int = 0x03
    private const val SERVER_OPEN_OK: Int = 0x81
    private const val SERVER_DATA: Int = 0x82
    private const val SERVER_WAIT: Int = 0x83
    private const val SERVER_CLOSED: Int = 0x84
    private const val SERVER_ERROR: Int = 0xff

    val openFrame: ByteArray
        get() = byteArrayOf(CLIENT_OPEN.toByte(), VERSION.toByte())

    val closeFrame: ByteArray
        get() = byteArrayOf(CLIENT_CLOSE.toByte())

    fun dataFrame(bytes: ByteArray): ByteArray {
        require(bytes.isNotEmpty()) { "TLS data frame must not be empty" }
        return byteArrayOf(CLIENT_DATA.toByte()) + bytes
    }

    fun tlsPayloadCap(mtu: Int): Int {
        require(mtu >= DEFAULT_MTU) { "Invalid negotiated MTU" }
        return minOf(mtu - 4, MAX_ATTRIBUTE_VALUE_BYTES - 1)
    }

    fun parseServerFrame(value: ByteArray, payloadCap: Int): ServerFrame {
        if (value.isEmpty()) throw BleProtocolException("Empty tunnel frame")
        return when (value[0].toInt() and 0xff) {
            SERVER_OPEN_OK -> {
                if (value.size != 2 || value[1].toInt() and 0xff != VERSION) {
                    throw BleProtocolException("Invalid OPEN_OK frame")
                }
                ServerFrame.OpenOk
            }
            SERVER_DATA -> {
                if (value.size !in 2..payloadCap + 1) {
                    throw BleProtocolException("Invalid DATA frame length")
                }
                ServerFrame.Data(value.copyOfRange(1, value.size))
            }
            SERVER_WAIT -> {
                if (value.size != 1) throw BleProtocolException("Invalid WAIT frame")
                ServerFrame.Wait
            }
            SERVER_CLOSED -> {
                if (value.size != 1) throw BleProtocolException("Invalid CLOSED frame")
                ServerFrame.Closed
            }
            SERVER_ERROR -> {
                if (value.size != 2) throw BleProtocolException("Invalid ERROR frame")
                ServerFrame.Error(BleErrorCode.fromWire(value[1].toInt() and 0xff))
            }
            else -> throw BleProtocolException("Unknown tunnel frame")
        }
    }
}

sealed interface ServerFrame {
    data object OpenOk : ServerFrame

    data class Data(val bytes: ByteArray) : ServerFrame

    data object Wait : ServerFrame

    data object Closed : ServerFrame

    data class Error(val code: BleErrorCode) : ServerFrame
}

enum class BleErrorCode(val wireValue: Int) {
    BUSY(0x01),
    BAD_STATE(0x02),
    UNSUPPORTED_VERSION(0x03),
    UPSTREAM_UNAVAILABLE(0x04),
    TIMEOUT(0x05),
    UNKNOWN(-1),
    ;

    companion object {
        fun fromWire(value: Int): BleErrorCode = entries.firstOrNull { it.wireValue == value } ?: UNKNOWN
    }
}

open class BleTransportException(message: String, cause: Throwable? = null) :
    Exception(message, cause)

class BleProtocolException(message: String) : BleTransportException(message)

class BleRemoteException(val errorCode: BleErrorCode) :
    BleTransportException("BLE tunnel failed: ${errorCode.name}")

class BleLinkUnusableException(message: String, cause: Throwable? = null) :
    BleTransportException(message, cause)

class BleSchedulerBusyException : BleTransportException("BLE API queue is full")
