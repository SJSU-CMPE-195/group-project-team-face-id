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

class DeviceConnector(
    private val context: Context,
    private val discovery: DeviceDiscovery,
) {
    suspend fun commission(
        pending: PendingCommissioning,
        onConnecting: () -> Unit,
        onExchangeStarted: () -> Unit,
    ): ConnectedDevice {
        val url =
            find(pending.qr.hostDeviceId, onConnecting) { candidate ->
                selectOnboardingUrl(candidate, pending.qr)
            }
        val authenticated = runCatching { connectKnownToken(url, pending) }
        authenticated.getOrNull()?.let { return it }
        val firstError = authenticated.exceptionOrNull()
        if (firstError is TlsIdentityException || firstError is DeviceIdentityException) {
            throw firstError
        }
        if (pending.qr.version == 3 && pending.qr.purpose != CommissioningPurpose.DEVICE) {
            val status =
                DeviceApi(url, pending.qr.certificateSha256)
                    .publicJson("/api/commissioning/status")
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
        val response = exchangeCommissioning(url, pending)
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
        return connectKnownToken(url, pending, response)
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
        var tlsError: TlsIdentityException? = null
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
                                tlsError = error
                                null
                            } catch (error: Exception) {
                                lastError = error
                                null
                            }
                        }
                        .firstOrNull()
                }
            return connected ?: throw tlsError ?: lastError ?: notFound()
        } catch (error: TimeoutCancellationException) {
            throw tlsError ?: lastError ?: error
        }
    }

    private suspend fun connectCandidate(
        candidate: DiscoveredDevice,
        pairing: Pairing,
    ): ConnectedDevice {
        var lastError: Exception = noAddress()
        var tlsError: TlsIdentityException? = null
        for (url in candidate.urls) {
            val probe =
                DeviceApi(
                    url,
                    pairing.certificateSha256,
                    pairing.deviceToken,
                    PROBE_CONNECT_TIMEOUT_MILLIS,
                    PROBE_READ_TIMEOUT_MILLIS,
                )
            try {
                val info = probe.json("/api/device-info")
                validateHost(info, pairing.hostDeviceId)
                val identity = probe.json("/api/me").sessionIdentity()
                if (
                    identity.deviceId != pairing.identity.deviceId ||
                        identity.user.id != pairing.identity.user.id
                ) {
                    throw DeviceIdentityException(identityMismatch())
                }
                val api = DeviceApi(url, pairing.certificateSha256, pairing.deviceToken)
                return ConnectedDevice(api, info, pairing.copy(identity = identity))
            } catch (error: CancellationException) {
                throw error
            } catch (error: PairingExchangeException) {
                throw error
            } catch (error: Exception) {
                if (error is ApiException && error.statusCode in setOf(401, 403)) throw error
                if (error is TlsIdentityException) tlsError = error else lastError = error
            }
        }
        throw tlsError ?: lastError
    }

    private suspend fun selectOnboardingUrl(
        candidate: DiscoveredDevice,
        qr: PairingQr,
    ): String {
        var lastError: Exception = noAddress()
        var tlsError: TlsIdentityException? = null
        for (url in candidate.urls) {
            val probe =
                DeviceApi(
                    url,
                    qr.certificateSha256,
                    connectTimeoutMillis = PROBE_CONNECT_TIMEOUT_MILLIS,
                    readTimeoutMillis = PROBE_READ_TIMEOUT_MILLIS,
                )
            try {
                val health = probe.publicJson("/health")
                if (!health.optBoolean("ok")) {
                    throw DeviceIdentityException(identityMismatch())
                }
                validateHost(health, qr.hostDeviceId)
                return url
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                if (error is TlsIdentityException) tlsError = error else lastError = error
            }
        }
        throw tlsError ?: lastError
    }

    private suspend fun exchangeCommissioning(
        url: String,
        pending: PendingCommissioning,
    ): JSONObject {
        val onboardingApi = DeviceApi(url, pending.qr.certificateSha256)
        return try {
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
    }

    private suspend fun connectKnownToken(
        url: String,
        pending: PendingCommissioning,
        response: JSONObject? = null,
    ): ConnectedDevice {
        val api = DeviceApi(url, pending.qr.certificateSha256, pending.deviceToken)
        val identity = response?.sessionIdentity() ?: api.json("/api/me").sessionIdentity()
        require(pending.qr.version != 3 || response?.has("device_token") != true) {
            "Version 3 pairing must use the saved client credential"
        }
        val pairing =
            Pairing(
                hostDeviceId = pending.qr.hostDeviceId,
                certificateSha256 = pending.qr.certificateSha256,
                deviceToken =
                    response?.optString("device_token")?.takeIf { it.isNotEmpty() }
                        ?: pending.deviceToken,
                identity = identity,
            )
        val authenticatedApi =
            if (pairing.deviceToken == pending.deviceToken) api
            else DeviceApi(url, pending.qr.certificateSha256, pairing.deviceToken)
        val verified = authenticatedApi.json("/api/me").sessionIdentity()
        if (verified.deviceId != identity.deviceId || verified.user.id != identity.user.id) {
            throw DeviceIdentityException(identityMismatch())
        }
        val info = authenticatedApi.json("/api/device-info")
        validateHost(info, pending.qr.hostDeviceId)
        return ConnectedDevice(authenticatedApi, info, pairing.copy(identity = verified))
    }

    private fun validateHost(info: JSONObject, expectedHostDeviceId: String) {
        if (info.getString("device_id") != expectedHostDeviceId) {
            throw DeviceIdentityException(identityMismatch())
        }
        if (info.getInt("protocol_version") != 3) {
            throw DeviceIdentityException(context.getString(R.string.core_protocol_unsupported))
        }
    }

    private fun notFound() = IllegalStateException(context.getString(R.string.core_not_found))

    private fun noAddress() = IllegalStateException(context.getString(R.string.core_no_address))

    private fun identityMismatch() = context.getString(R.string.core_identity_mismatch)

    companion object {
        private const val DISCOVERY_TIMEOUT_MILLIS = 15_000L
        private const val CANDIDATE_TIMEOUT_MILLIS = 5_000L
        private const val MAX_CANDIDATE_ATTEMPTS = 5
        private const val PROBE_CONNECT_TIMEOUT_MILLIS = 1_000
        private const val PROBE_READ_TIMEOUT_MILLIS = 1_500
    }
}

private fun PendingCommissioning.endpoint(): String =
    when (qr.purpose) {
        CommissioningPurpose.DEVICE -> "/api/pairings"
        CommissioningPurpose.ACTIVATION -> "/api/commissioning/claim"
        CommissioningPurpose.RECOVERY -> "/api/commissioning/recover"
        CommissioningPurpose.TRANSFER -> "/api/commissioning/transfer/accept"
    }
