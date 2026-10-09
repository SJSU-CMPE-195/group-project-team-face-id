package com.bass.app

import android.content.Context
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.flow.firstOrNull
import kotlinx.coroutines.flow.mapNotNull
import kotlinx.coroutines.flow.take
import kotlinx.coroutines.withTimeout
import org.json.JSONObject

data class ConnectedDevice(
    val api: DeviceApi,
    val info: JSONObject,
    val pairing: Pairing,
)

class DeviceIdentityException(message: String) : Exception(message)
class PairingExchangeException(message: String, cause: Throwable? = null) :
    Exception(message, cause)

private data class VerifiedBleChannel(
    val publicApi: DeviceApi,
)

class DeviceConnector(
    private val context: Context,
    private val discovery: DeviceDiscovery,
) {
    suspend fun commission(
        pending: PendingCommissioning,
        onConnecting: () -> Unit,
        onExchangeStarted: () -> Unit,
    ): ConnectedDevice {
        val channel =
            find(pending.qr.hostDeviceId, onConnecting) { candidate ->
                openVerifiedChannel(candidate, pending.qr.certificateSha256, pending.qr.hostDeviceId)
            }
        try {
            try {
                return connectKnownToken(channel.publicApi, pending)
            } catch (error: CancellationException) {
                throw error
            } catch (error: ApiException) {
                if (error.statusCode != 401) throw error
            }
            if (pending.qr.version == 3 && pending.qr.purpose != CommissioningPurpose.DEVICE) {
                val status = channel.publicApi.publicJson("/api/commissioning/status")
                require(status.optBoolean("powered")) {
                    context.getString(R.string.core_commissioning_unavailable)
                }
                val ownership = status.getString("ownership")
                val expected =
                    if (pending.qr.purpose == CommissioningPurpose.ACTIVATION) "unclaimed"
                    else "claimed"
                require(ownership == expected) {
                    context.getString(R.string.core_commissioning_unavailable)
                }
            }
            onExchangeStarted()
            val response = exchangeCommissioning(channel.publicApi, pending)
            if (pending.qr.version == 3) {
                require(response.getString("request_id") == pending.requestId) {
                    "Commissioning response does not match request"
                }
                if (pending.qr.purpose != CommissioningPurpose.DEVICE) {
                    require(response.optBoolean("ok") && response.getInt("generation") >= 0) {
                        "Invalid commissioning response"
                    }
                }
            }
            return connectKnownToken(channel.publicApi, pending, response)
        } catch (error: Exception) {
            channel.publicApi.close()
            throw error
        }
    }

    suspend fun connect(
        pairing: Pairing,
        onConnecting: () -> Unit,
    ): ConnectedDevice =
        find(pairing.hostDeviceId, onConnecting) { candidate ->
            connectCandidate(candidate, pairing)
        }

    private suspend fun <T> find(
        hostDeviceId: String,
        onConnecting: () -> Unit,
        attempt: suspend (DiscoveredDevice) -> T,
    ): T {
        var lastError: Exception? = null
        var identityError: Exception? = null
        try {
            val connected =
                withTimeout(DISCOVERY_TIMEOUT_MILLIS) {
                    discovery
                        .candidates(hostDeviceId)
                        .take(MAX_CANDIDATE_ATTEMPTS)
                        .mapNotNull { candidate ->
                            onConnecting()
                            try {
                                withTimeout(CANDIDATE_TIMEOUT_MILLIS) { attempt(candidate) }
                            } catch (error: TimeoutCancellationException) {
                                lastError = notFound()
                                null
                            } catch (error: CancellationException) {
                                throw error
                            } catch (error: PairingExchangeException) {
                                throw error
                            } catch (error: ApiException) {
                                if (error.statusCode == 401 || error.statusCode == 403) throw error
                                lastError = error
                                null
                            } catch (error: TlsIdentityException) {
                                identityError = error
                                null
                            } catch (error: DeviceIdentityException) {
                                identityError = error
                                null
                            } catch (error: Exception) {
                                lastError = error
                                null
                            }
                        }
                        .firstOrNull()
                }
            return connected ?: throw identityError ?: lastError ?: notFound()
        } catch (error: TimeoutCancellationException) {
            throw identityError ?: lastError ?: error
        }
    }

    private suspend fun connectCandidate(
        candidate: DiscoveredDevice,
        pairing: Pairing,
    ): ConnectedDevice {
        val channel =
            openVerifiedChannel(
                candidate,
                pairing.certificateSha256,
                pairing.hostDeviceId,
            )
        try {
            val api = channel.publicApi.withToken(pairing.deviceToken)
            val identity = api.json("/api/me").sessionIdentity()
            if (
                identity.deviceId != pairing.identity.deviceId ||
                    identity.user.id != pairing.identity.user.id
            ) {
                throw DeviceIdentityException(identityMismatch())
            }
            val info = api.json("/api/device-info")
            validateHost(info, pairing.hostDeviceId)
            return ConnectedDevice(api, info, pairing.copy(identity = identity))
        } catch (error: Exception) {
            channel.publicApi.close()
            throw error
        }
    }

    private suspend fun openVerifiedChannel(
        candidate: DiscoveredDevice,
        certificateSha256: String,
        expectedHostDeviceId: String,
    ): VerifiedBleChannel {
        val transport = BleTransport.connect(context, candidate.bluetoothDevice)
        val api = DeviceApi(transport, certificateSha256)
        try {
            val health = api.publicJson("/health")
            if (!health.optBoolean("ok")) {
                throw DeviceIdentityException(identityMismatch())
            }
            validateHost(health, expectedHostDeviceId)
            return VerifiedBleChannel(api)
        } catch (error: Exception) {
            api.close()
            throw error
        }
    }

    private suspend fun exchangeCommissioning(
        onboardingApi: DeviceApi,
        pending: PendingCommissioning,
    ): JSONObject =
        try {
            if (pending.qr.version == 2) {
                onboardingApi.onboardingJson(
                    "/api/pairings",
                    checkNotNull(pending.qr.onboardingKey),
                    pending.requestBody(),
                )
            } else {
                onboardingApi.publicPost(pending.endpoint(), pending.requestBody())
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: ApiException) {
            throw error
        } catch (error: Exception) {
            throw PairingExchangeException(
                context.getString(R.string.core_pairing_exchange_uncertain),
                error,
            )
        }

    private suspend fun connectKnownToken(
        publicApi: DeviceApi,
        pending: PendingCommissioning,
        response: JSONObject? = null,
    ): ConnectedDevice {
        require(pending.qr.version != 3 || response?.has("device_token") != true) {
            "Version 3 pairing must use the saved client credential"
        }
        val deviceToken =
            response?.optString("device_token")?.takeIf(String::isNotEmpty)
                ?: pending.deviceToken
        val api = publicApi.withToken(deviceToken)
        val identity = api.json("/api/me").sessionIdentity()
        val pairing =
            Pairing(
                hostDeviceId = pending.qr.hostDeviceId,
                certificateSha256 = pending.qr.certificateSha256,
                deviceToken = deviceToken,
                identity = identity,
            )
        val info = api.json("/api/device-info")
        validateHost(info, pending.qr.hostDeviceId)
        return ConnectedDevice(api, info, pairing)
    }

    private fun validateHost(info: JSONObject, expectedHostDeviceId: String) {
        if (info.getString("device_id") != expectedHostDeviceId) {
            throw DeviceIdentityException(identityMismatch())
        }
        if (info.getInt("protocol_version") != API_PROTOCOL_VERSION) {
            throw DeviceIdentityException(context.getString(R.string.core_protocol_unsupported))
        }
    }

    private fun notFound() = IllegalStateException(context.getString(R.string.core_not_found))

    private fun identityMismatch() = context.getString(R.string.core_identity_mismatch)

    companion object {
        private const val API_PROTOCOL_VERSION = 4
        private const val DISCOVERY_TIMEOUT_MILLIS = 210_000L
        private const val CANDIDATE_TIMEOUT_MILLIS = 195_000L
        private const val MAX_CANDIDATE_ATTEMPTS = 5
    }
}

private fun PendingCommissioning.endpoint(): String =
    when (qr.purpose) {
        CommissioningPurpose.DEVICE -> "/api/pairings"
        CommissioningPurpose.ACTIVATION -> "/api/commissioning/claim"
        CommissioningPurpose.RECOVERY -> "/api/commissioning/recover"
        CommissioningPurpose.TRANSFER -> "/api/commissioning/transfer/accept"
    }
