package com.bass.app

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat
import java.io.Closeable
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeout

internal class BleGattConnection private constructor(
    private val context: Context,
    private val device: BluetoothDevice,
    val generation: Long,
) : Closeable {
    private val callbackLock = Any()
    private val operationMutex = Mutex()
    private val reusable = AtomicBoolean(true)
    private val connected = CompletableDeferred<Unit>()
    private var bluetoothGatt: BluetoothGatt? = null
    private var negotiatedMtu = BleProtocol.DEFAULT_MTU
    private var protocolCharacteristic: BluetoothGattCharacteristic? = null
    private var tunnelCharacteristic: BluetoothGattCharacteristic? = null
    private var pendingMtu: CompletableDeferred<Int>? = null
    private var pendingDiscovery: CompletableDeferred<Unit>? = null
    private var pendingRead: PendingRead? = null
    private var pendingWrite: CompletableDeferred<Unit>? = null

    val isReusable: Boolean
        get() = reusable.get()

    val payloadCap: Int
        get() = BleProtocol.tlsPayloadCap(negotiatedMtu)

    val endpointLabel: String
        get() = "ble://${device.address}"

    private val callback =
        object : BluetoothGattCallback() {
            override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
                if (!accepts(gatt)) return
                if (status == BluetoothGatt.GATT_SUCCESS && newState == BluetoothProfile.STATE_CONNECTED) {
                    connected.complete(Unit)
                    return
                }
                val error =
                    BleLinkUnusableException(
                        "BLE connection changed state (status=$status, state=$newState)",
                    )
                connected.completeExceptionally(error)
                poison(error)
            }

            override fun onMtuChanged(gatt: BluetoothGatt, mtu: Int, status: Int) {
                if (!accepts(gatt)) return
                val deferred = synchronized(callbackLock) { pendingMtu.also { pendingMtu = null } }
                    ?: return
                if (status == BluetoothGatt.GATT_SUCCESS && mtu >= BleProtocol.DEFAULT_MTU) {
                    deferred.complete(mtu)
                } else {
                    val error = BleLinkUnusableException("BLE MTU negotiation failed: $status")
                    deferred.completeExceptionally(error)
                    poison(error)
                }
            }

            override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) {
                if (!accepts(gatt)) return
                val deferred =
                    synchronized(callbackLock) {
                        pendingDiscovery.also { pendingDiscovery = null }
                    } ?: return
                if (status == BluetoothGatt.GATT_SUCCESS) {
                    deferred.complete(Unit)
                } else {
                    val error = BleLinkUnusableException("BLE service discovery failed: $status")
                    deferred.completeExceptionally(error)
                    poison(error)
                }
            }

            @Deprecated("Deprecated in API 33")
            override fun onCharacteristicRead(
                gatt: BluetoothGatt,
                characteristic: BluetoothGattCharacteristic,
                status: Int,
            ) {
                completeRead(gatt, characteristic.uuid, characteristic.value?.clone(), status)
            }

            override fun onCharacteristicRead(
                gatt: BluetoothGatt,
                characteristic: BluetoothGattCharacteristic,
                value: ByteArray,
                status: Int,
            ) {
                completeRead(gatt, characteristic.uuid, value.clone(), status)
            }

            override fun onCharacteristicWrite(
                gatt: BluetoothGatt,
                characteristic: BluetoothGattCharacteristic,
                status: Int,
            ) {
                if (!accepts(gatt) || characteristic.uuid != BleProtocol.TUNNEL_UUID) return
                val deferred =
                    synchronized(callbackLock) { pendingWrite.also { pendingWrite = null } }
                        ?: return
                if (status == BluetoothGatt.GATT_SUCCESS) {
                    deferred.complete(Unit)
                } else {
                    val error = BleLinkUnusableException("BLE characteristic write failed: $status")
                    deferred.completeExceptionally(error)
                    poison(error)
                }
            }
        }

    suspend fun connectAndDiscover() {
        try {
            val created =
                connectGattChecked()
            bluetoothGatt = created
            awaitCallback { connected.await() }
            requestMtu()
            discoverServices()
            validateCharacteristics()
            val protocol = readCharacteristic(checkNotNull(protocolCharacteristic))
            if (!protocol.contentEquals(byteArrayOf(BleProtocol.VERSION.toByte()))) {
                throw BleProtocolException("Unsupported BLE tunnel protocol")
            }
        } catch (error: Throwable) {
            poison(error)
            throw error
        }
    }

    suspend fun writeTunnel(value: ByteArray) =
        operationMutex.withLock {
            ensureReusable()
            require(value.size <= minOf(negotiatedMtu - 3, BleProtocol.MAX_ATTRIBUTE_VALUE_BYTES)) {
                "BLE write exceeds negotiated MTU"
            }
            val deferred = CompletableDeferred<Unit>()
            synchronized(callbackLock) {
                check(pendingWrite == null && pendingRead == null) { "Another GATT operation is active" }
                pendingWrite = deferred
            }
            val started = startWrite(checkNotNull(tunnelCharacteristic), value)
            if (!started) {
                synchronized(callbackLock) { if (pendingWrite === deferred) pendingWrite = null }
                val error = BleLinkUnusableException("BLE characteristic write did not start")
                poison(error)
                throw error
            }
            awaitOperation { deferred.await() }
        }

    suspend fun readTunnel(): ByteArray =
        operationMutex.withLock {
            ensureReusable()
            readCharacteristic(checkNotNull(tunnelCharacteristic))
        }

    override fun close() {
        poison(BleLinkUnusableException("BLE connection closed"))
    }

    private suspend fun requestMtu() {
        val gatt = checkNotNull(bluetoothGatt)
        val deferred = CompletableDeferred<Int>()
        synchronized(callbackLock) { pendingMtu = deferred }
        if (!requestMtuChecked(gatt)) {
            synchronized(callbackLock) { if (pendingMtu === deferred) pendingMtu = null }
            negotiatedMtu = BleProtocol.DEFAULT_MTU
            return
        }
        negotiatedMtu = awaitOperation { deferred.await() }
    }

    private suspend fun discoverServices() {
        val gatt = checkNotNull(bluetoothGatt)
        val deferred = CompletableDeferred<Unit>()
        synchronized(callbackLock) { pendingDiscovery = deferred }
        if (!discoverServicesChecked(gatt)) {
            synchronized(callbackLock) {
                if (pendingDiscovery === deferred) pendingDiscovery = null
            }
            val error = BleLinkUnusableException("BLE service discovery did not start")
            poison(error)
            throw error
        }
        awaitOperation { deferred.await() }
    }

    private fun validateCharacteristics() {
        val service = bluetoothGatt?.getService(BleProtocol.SERVICE_UUID)
            ?: throw BleProtocolException("BASS BLE service is missing")
        val protocol = service.getCharacteristic(BleProtocol.PROTOCOL_UUID)
            ?: throw BleProtocolException("BASS protocol characteristic is missing")
        val tunnel = service.getCharacteristic(BleProtocol.TUNNEL_UUID)
            ?: throw BleProtocolException("BASS tunnel characteristic is missing")
        if (protocol.properties and BluetoothGattCharacteristic.PROPERTY_READ == 0) {
            throw BleProtocolException("BASS protocol characteristic is not readable")
        }
        if (
            tunnel.properties and BluetoothGattCharacteristic.PROPERTY_READ == 0 ||
                tunnel.properties and BluetoothGattCharacteristic.PROPERTY_WRITE == 0
        ) {
            throw BleProtocolException("BASS tunnel characteristic requires read and write")
        }
        protocolCharacteristic = protocol
        tunnelCharacteristic = tunnel
    }

    private suspend fun readCharacteristic(characteristic: BluetoothGattCharacteristic): ByteArray {
        val deferred = CompletableDeferred<ByteArray>()
        synchronized(callbackLock) {
            check(pendingRead == null && pendingWrite == null) { "Another GATT operation is active" }
            pendingRead = PendingRead(characteristic.uuid, deferred)
        }
        val started = readCharacteristicChecked(checkNotNull(bluetoothGatt), characteristic)
        if (!started) {
            synchronized(callbackLock) { if (pendingRead?.deferred === deferred) pendingRead = null }
            val error = BleLinkUnusableException("BLE characteristic read did not start")
            poison(error)
            throw error
        }
        return awaitOperation { deferred.await() }
    }

    @Suppress("DEPRECATION")
    @SuppressLint("MissingPermission")
    private fun startWrite(characteristic: BluetoothGattCharacteristic, value: ByteArray): Boolean {
        requireConnectPermission()
        val gatt = checkNotNull(bluetoothGatt)
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                gatt.writeCharacteristic(
                    characteristic,
                    value,
                    BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT,
                ) == BluetoothStatusCodes.SUCCESS
            } else {
                characteristic.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                characteristic.value = value
                gatt.writeCharacteristic(characteristic)
            }
        } catch (error: SecurityException) {
            throw permissionRevoked(error)
        }
    }

    private fun completeRead(
        gatt: BluetoothGatt,
        characteristicUuid: UUID,
        value: ByteArray?,
        status: Int,
    ) {
        if (!accepts(gatt)) return
        val pending =
            synchronized(callbackLock) {
                pendingRead?.takeIf { it.characteristicUuid == characteristicUuid }
                    ?.also { pendingRead = null }
            } ?: return
        if (status == BluetoothGatt.GATT_SUCCESS && value != null) {
            pending.deferred.complete(value)
        } else {
            val error = BleLinkUnusableException("BLE characteristic read failed: $status")
            pending.deferred.completeExceptionally(error)
            poison(error)
        }
    }

    private suspend fun <T> awaitCallback(block: suspend () -> T): T =
        try {
            withTimeout(BleProtocol.CALLBACK_TIMEOUT_MILLIS) { block() }
        } catch (error: Throwable) {
            poison(error)
            throw error
        }

    private suspend fun <T> awaitOperation(block: suspend () -> T): T = awaitCallback(block)

    private fun accepts(gatt: BluetoothGatt): Boolean {
        val current = bluetoothGatt
        return reusable.get() && (current == null || current === gatt)
    }

    private fun ensureReusable() {
        if (!reusable.get()) throw BleLinkUnusableException("BLE connection is not reusable")
    }

    private fun poison(cause: Throwable) {
        if (!reusable.compareAndSet(true, false)) return
        val error =
            if (cause is BleLinkUnusableException) cause
            else BleLinkUnusableException("BLE connection became unusable", cause)
        synchronized(callbackLock) {
            pendingMtu?.completeExceptionally(error)
            pendingMtu = null
            pendingDiscovery?.completeExceptionally(error)
            pendingDiscovery = null
            pendingRead?.deferred?.completeExceptionally(error)
            pendingRead = null
            pendingWrite?.completeExceptionally(error)
            pendingWrite = null
        }
        connected.completeExceptionally(error)
        bluetoothGatt?.let(::closeGattChecked)
        bluetoothGatt = null
    }

    @SuppressLint("MissingPermission")
    private fun connectGattChecked(): BluetoothGatt {
        requireConnectPermission()
        return try {
            device.connectGatt(
                context.applicationContext,
                false,
                callback,
                BluetoothDevice.TRANSPORT_LE,
            )
        } catch (error: SecurityException) {
            throw permissionRevoked(error)
        }
    }

    @SuppressLint("MissingPermission")
    private fun requestMtuChecked(gatt: BluetoothGatt): Boolean {
        requireConnectPermission()
        return try {
            gatt.requestMtu(BleProtocol.REQUESTED_MTU)
        } catch (error: SecurityException) {
            throw permissionRevoked(error)
        }
    }

    @SuppressLint("MissingPermission")
    private fun discoverServicesChecked(gatt: BluetoothGatt): Boolean {
        requireConnectPermission()
        return try {
            gatt.discoverServices()
        } catch (error: SecurityException) {
            throw permissionRevoked(error)
        }
    }

    @SuppressLint("MissingPermission")
    private fun readCharacteristicChecked(
        gatt: BluetoothGatt,
        characteristic: BluetoothGattCharacteristic,
    ): Boolean {
        requireConnectPermission()
        return try {
            gatt.readCharacteristic(characteristic)
        } catch (error: SecurityException) {
            throw permissionRevoked(error)
        }
    }

    @SuppressLint("MissingPermission")
    private fun closeGattChecked(gatt: BluetoothGatt) {
        val hasPermission = hasConnectPermission()
        if (hasPermission) {
            try {
                gatt.disconnect()
            } catch (_: SecurityException) {
                // Permission can be revoked between the explicit check and the call.
            }
        }
        try {
            gatt.close()
        } catch (_: SecurityException) {
            // Pending work has already failed; there is no safe operation left to perform.
        }
    }

    private fun requireConnectPermission() {
        if (!hasConnectPermission()) throw permissionRevoked()
    }

    private fun hasConnectPermission(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.S ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.BLUETOOTH_CONNECT) ==
                PackageManager.PERMISSION_GRANTED

    private fun permissionRevoked(cause: SecurityException? = null): BluetoothPermissionException {
        val error =
            BluetoothPermissionException(
                context.getString(R.string.core_bluetooth_permission_required),
            )
        if (cause != null) error.initCause(cause)
        poison(error)
        return error
    }

    private data class PendingRead(
        val characteristicUuid: UUID,
        val deferred: CompletableDeferred<ByteArray>,
    )

    companion object {
        private val nextGeneration = AtomicLong()

        suspend fun connect(context: Context, device: BluetoothDevice): BleGattConnection {
            val connection = BleGattConnection(context, device, nextGeneration.incrementAndGet())
            connection.connectAndDiscover()
            return connection
        }
    }
}
