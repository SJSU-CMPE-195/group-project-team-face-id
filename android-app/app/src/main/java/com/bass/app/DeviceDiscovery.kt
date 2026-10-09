package com.bass.app

import android.Manifest
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.ParcelUuid
import androidx.core.content.ContextCompat
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.buffer
import kotlinx.coroutines.flow.callbackFlow

private const val CANDIDATE_BUFFER_SIZE = 8
private const val MAX_REMEMBERED_ADDRESSES = 32

data class DiscoveredDevice(val bluetoothDevice: BluetoothDevice)

class BluetoothPermissionException(message: String) : IllegalStateException(message)
class BluetoothUnavailableException(message: String) : IllegalStateException(message)

class DeviceDiscovery(private val context: Context) {
    private val manager = context.getSystemService(BluetoothManager::class.java)

    fun candidates(@Suppress("UNUSED_PARAMETER") deviceId: String): Flow<DiscoveredDevice> =
        callbackFlow {
                if (!hasPermissions(context)) {
                    close(
                        BluetoothPermissionException(
                            context.getString(R.string.core_bluetooth_permission_required)
                        )
                    )
                    return@callbackFlow
                }
                val adapter = manager?.adapter
                if (adapter == null || !adapter.isEnabled) {
                    close(
                        BluetoothUnavailableException(
                            context.getString(R.string.core_bluetooth_unavailable)
                        )
                    )
                    return@callbackFlow
                }
                val scanner = adapter.bluetoothLeScanner
                if (scanner == null) {
                    close(
                        BluetoothUnavailableException(
                            context.getString(R.string.core_bluetooth_unavailable)
                        )
                    )
                    return@callbackFlow
                }
                val stopped = AtomicBoolean(false)
                val rememberedAddresses = linkedSetOf<String>()
                val callback =
                    object : ScanCallback() {
                        override fun onScanResult(callbackType: Int, result: ScanResult) {
                            val address = result.device.address
                            if (stopped.get() || !rememberedAddresses.add(address)) return
                            if (rememberedAddresses.size > MAX_REMEMBERED_ADDRESSES) {
                                rememberedAddresses.remove(rememberedAddresses.first())
                            }
                            trySend(DiscoveredDevice(result.device))
                        }

                        override fun onBatchScanResults(results: MutableList<ScanResult>) {
                            results.forEach { onScanResult(ScanSettings.CALLBACK_TYPE_ALL_MATCHES, it) }
                        }

                        override fun onScanFailed(errorCode: Int) {
                            close(
                                BluetoothUnavailableException(
                                    context.getString(R.string.core_bluetooth_scan_failed, errorCode)
                                )
                            )
                        }
                    }
                val filter =
                    ScanFilter.Builder()
                        .setServiceUuid(ParcelUuid(BleProtocol.SERVICE_UUID))
                        .build()
                val settings =
                    ScanSettings.Builder()
                        .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
                        .setCallbackType(ScanSettings.CALLBACK_TYPE_ALL_MATCHES)
                        .build()
                try {
                    scanner.startScan(listOf(filter), settings, callback)
                } catch (error: SecurityException) {
                    close(
                        BluetoothPermissionException(
                            context.getString(R.string.core_bluetooth_permission_required)
                        )
                    )
                } catch (error: Exception) {
                    close(
                        BluetoothUnavailableException(
                            error.message ?: context.getString(R.string.core_bluetooth_unavailable)
                        )
                    )
                }
                awaitClose {
                    if (stopped.compareAndSet(false, true)) {
                        try {
                            scanner.stopScan(callback)
                        } catch (_: SecurityException) {
                            // Permission can be revoked while the scan is active.
                        }
                    }
                }
            }
            .buffer(CANDIDATE_BUFFER_SIZE, BufferOverflow.DROP_OLDEST)

    companion object {
        fun requiredPermissions(): Array<String> =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                arrayOf(
                    Manifest.permission.BLUETOOTH_SCAN,
                    Manifest.permission.BLUETOOTH_CONNECT,
                )
            } else {
                arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
            }

        fun hasPermissions(context: Context): Boolean =
            requiredPermissions().all {
                ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED
            }
    }
}
