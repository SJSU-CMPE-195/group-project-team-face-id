package com.bass.app

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import java.net.InetAddress
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.buffer
import kotlinx.coroutines.flow.callbackFlow

private const val MAX_PENDING_RESOLUTIONS = 16
private const val MAX_CANDIDATE_ADDRESSES = 2
private const val MAX_REMEMBERED_CANDIDATES = 32
private const val CANDIDATE_BUFFER_SIZE = 8

data class DiscoveredDevice(val addresses: List<InetAddress>, val port: Int) {
    val urls: List<String>
        get() = addresses.map { address ->
            val host = address.hostAddress ?: error("Device has no address")
            val formatted = if (host.contains(':')) "[$host]" else host
            "https://$formatted:$port"
        }
}

class DeviceDiscovery(private val context: Context) {
    private val manager = context.getSystemService(NsdManager::class.java)
    private val wifi = context.applicationContext.getSystemService(WifiManager::class.java)

    @Suppress("DEPRECATION")
    fun candidates(deviceId: String): Flow<DiscoveredDevice> =
        callbackFlow {
                val multicastLock =
                    wifi.createMulticastLock("bass-discovery").apply {
                        setReferenceCounted(false)
                        acquire()
                    }
                val stopped = AtomicBoolean(false)
                val pending = ArrayDeque<NsdServiceInfo>()
                val pendingNames = mutableSetOf<String>()
                val rememberedCandidates = linkedSetOf<String>()
                var resolving = false
                lateinit var discovery: NsdManager.DiscoveryListener

                fun stop() {
                    if (!stopped.compareAndSet(false, true)) return
                    runCatching { manager.stopServiceDiscovery(discovery) }
                    if (multicastLock.isHeld) multicastLock.release()
                }

                fun fail(message: String) {
                    close(IllegalStateException(message))
                }

                fun emitCandidate(info: NsdServiceInfo) {
                    val foundId = info.attributes["device_id"]?.toString(Charsets.UTF_8)
                    val version = info.attributes["protocol_version"]?.toString(Charsets.UTF_8)
                    val transport = info.attributes["transport"]?.toString(Charsets.UTF_8)
                    val hosts =
                        if (Build.VERSION.SDK_INT >= 34) info.hostAddresses
                        else listOfNotNull(info.host)
                    val lanHosts =
                        hosts
                            .filter { address ->
                                val local =
                                    address.isSiteLocalAddress || address.isLinkLocalAddress
                                val unsuitable =
                                    address.isLoopbackAddress ||
                                        address.isMulticastAddress ||
                                        address.isAnyLocalAddress
                                local && !unsuitable
                            }
                            .distinctBy { it.hostAddress }
                            .take(MAX_CANDIDATE_ADDRESSES)
                    if (
                        foundId != deviceId ||
                            version != "3" ||
                            transport != "https" ||
                            lanHosts.isEmpty() ||
                            info.port !in 1..65535
                    ) {
                        return
                    }

                    val key =
                        info.port.toString() +
                            ":" +
                            lanHosts.mapNotNull { it.hostAddress }.sorted().joinToString(",")
                    if (!rememberedCandidates.add(key)) return
                    if (rememberedCandidates.size > MAX_REMEMBERED_CANDIDATES) {
                        rememberedCandidates.remove(rememberedCandidates.first())
                    }
                    trySend(DiscoveredDevice(lanHosts, info.port))
                }

                fun resolveNext() {
                    if (stopped.get() || resolving || pending.isEmpty()) return
                    resolving = true
                    val service = pending.removeFirst()
                    pendingNames.remove(service.serviceName)
                    try {
                        manager.resolveService(
                            service,
                            object : NsdManager.ResolveListener {
                                override fun onResolveFailed(
                                    info: NsdServiceInfo,
                                    error: Int,
                                ) {
                                    resolving = false
                                    resolveNext()
                                }

                                override fun onServiceResolved(info: NsdServiceInfo) {
                                    resolving = false
                                    if (stopped.get()) return
                                    emitCandidate(info)
                                    resolveNext()
                                }
                            },
                        )
                    } catch (_: Exception) {
                        resolving = false
                        resolveNext()
                    }
                }

                fun queue(info: NsdServiceInfo) {
                    if (!pendingNames.add(info.serviceName)) return
                    if (pending.size == MAX_PENDING_RESOLUTIONS) {
                        pendingNames.remove(pending.removeFirst().serviceName)
                    }
                    pending.addLast(info)
                    resolveNext()
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
                            if (!stopped.get()) queue(info)
                        }
                    }
                try {
                    manager.discoverServices("_bass._tcp.", NsdManager.PROTOCOL_DNS_SD, discovery)
                } catch (error: Exception) {
                    fail(error.message ?: context.getString(R.string.core_discovery_unavailable))
                }
                awaitClose(::stop)
            }
            .buffer(CANDIDATE_BUFFER_SIZE, BufferOverflow.DROP_OLDEST)
}
