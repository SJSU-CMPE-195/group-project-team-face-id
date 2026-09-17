package com.bass.app

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import java.net.InetAddress
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeout

data class DiscoveredDevice(val addresses: List<InetAddress>, val port: Int) {
    val urls: List<String>
        get() = addresses.map { address ->
            val host = address.hostAddress ?: error("Device has no address")
            val formatted = if (host.contains(':')) "[$host]" else host
            "http://$formatted:$port"
        }
}

class DeviceDiscovery(private val context: Context) {
    private val manager = context.getSystemService(NsdManager::class.java)
    private val wifi = context.applicationContext.getSystemService(WifiManager::class.java)

    @Suppress("DEPRECATION")
    suspend fun find(deviceId: String): DiscoveredDevice =
        withTimeout(15000) {
            suspendCancellableCoroutine { continuation ->
                val lock =
                    wifi.createMulticastLock("bass-discovery").apply {
                        setReferenceCounted(false)
                        acquire()
                    }
                var stopped = false
                val pending = ArrayDeque<NsdServiceInfo>()
                var resolving = false
                lateinit var discovery: NsdManager.DiscoveryListener
                fun stop() {
                    if (stopped) return
                    stopped = true
                    runCatching { manager.stopServiceDiscovery(discovery) }
                    if (lock.isHeld) lock.release()
                }
                fun fail(message: String) {
                    stop()
                    if (continuation.isActive)
                        continuation.resumeWithException(IllegalStateException(message))
                }
                fun resolveNext() {
                    if (stopped || resolving || pending.isEmpty()) return
                    resolving = true
                    val service = pending.removeFirst()
                    manager.resolveService(
                        service,
                        object : NsdManager.ResolveListener {
                            override fun onResolveFailed(info: NsdServiceInfo, error: Int) {
                                resolving = false
                                resolveNext()
                            }

                            override fun onServiceResolved(info: NsdServiceInfo) {
                                resolving = false
                                if (stopped) return
                                val foundId = info.attributes["device_id"]?.toString(Charsets.UTF_8)
                                val version =
                                    info.attributes["protocol_version"]?.toString(Charsets.UTF_8)
                                val hosts =
                                    if (Build.VERSION.SDK_INT >= 34) info.hostAddresses
                                    else listOfNotNull(info.host)
                                val lanHosts = hosts.filter { address ->
                                    val local =
                                        address.isSiteLocalAddress || address.isLinkLocalAddress
                                    val unsuitable =
                                        address.isLoopbackAddress ||
                                            address.isMulticastAddress ||
                                            address.isAnyLocalAddress
                                    local && !unsuitable
                                }
                                if (
                                    foundId == deviceId &&
                                        version == "1" &&
                                        lanHosts.isNotEmpty() &&
                                        info.port in 1..65535
                                ) {
                                    stop()
                                    if (continuation.isActive)
                                        continuation.resume(DiscoveredDevice(lanHosts, info.port))
                                } else resolveNext()
                            }
                        },
                    )
                }
                discovery =
                    object : NsdManager.DiscoveryListener {
                        override fun onDiscoveryStarted(type: String) = Unit

                        override fun onDiscoveryStopped(type: String) = Unit

                        override fun onStartDiscoveryFailed(type: String, code: Int) =
                            fail(context.getString(R.string.core_discovery_failed, code))

                        override fun onStopDiscoveryFailed(type: String, code: Int) = Unit

                        override fun onServiceLost(info: NsdServiceInfo) = Unit

                        override fun onServiceFound(info: NsdServiceInfo) {
                            if (stopped) return
                            pending.addLast(info)
                            resolveNext()
                        }
                    }
                continuation.invokeOnCancellation { stop() }
                try {
                    manager.discoverServices("_bass._tcp.", NsdManager.PROTOCOL_DNS_SD, discovery)
                } catch (error: Exception) {
                    fail(error.message ?: context.getString(R.string.core_discovery_unavailable))
                }
            }
        }
}
