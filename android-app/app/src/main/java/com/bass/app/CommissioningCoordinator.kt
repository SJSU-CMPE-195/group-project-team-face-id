package com.bass.app

import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import org.json.JSONObject

internal data class CommissioningCompletion(
    val connected: ConnectedDevice,
    val exportMaterial: ExportMaterial?,
)

internal class CommissioningCoordinator(
    private val store: PairingStore,
    private val connector: DeviceConnector,
) {
    fun newRequest(
        qr: PairingQr,
        pin: String,
        name: String = "",
        inviteToken: String = "",
    ): PendingCommissioning =
        PendingCommissioning(
            qr = qr,
            requestId = DeviceAuth.requestId(),
            deviceToken = DeviceAuth.randomSecret(),
            deviceName = DeviceAuth.deviceName(),
            pin = pin,
            name = name,
            inviteToken = inviteToken,
            newRecoverySecret =
                if (qr.purpose == CommissioningPurpose.DEVICE) ""
                else DeviceAuth.randomSecret(),
        )

    suspend fun execute(
        pending: PendingCommissioning,
        writeScope: Long,
        onConnecting: () -> Unit,
        onExchangeStarted: () -> Unit,
    ): CommissioningCompletion {
        val connected =
            connector.commission(pending, onConnecting, onExchangeStarted)
        val export = pending.recoveryExport()
        val completed =
            withContext(NonCancellable) {
                store.completeCommissioning(connected.pairing, pending, export, writeScope)
            }
        check(completed) { "Commissioning write was superseded" }
        return CommissioningCompletion(connected, export)
    }

    fun newTransfer(pin: String): PendingTransfer =
        PendingTransfer(DeviceAuth.requestId(), pin, DeviceAuth.randomSecret())

    suspend fun startTransfer(
        api: DeviceApi,
        pairing: Pairing,
        pending: PendingTransfer,
        writeScope: Long,
    ): ExportMaterial {
        val response =
            api.json(
                "/api/ownership/transfer",
                "POST",
                JSONObject()
                    .put("request_id", pending.requestId)
                    .put("pin", pending.pin)
                    .put("transfer_secret", pending.transferSecret),
            )
        require(response.getString("request_id") == pending.requestId) {
            "Transfer response does not match request"
        }
        require(
            response.optBoolean("ok") &&
                response.getInt("generation") >= 0 &&
                response.getInt("expires_in") in 1..300
        ) { "Invalid transfer response" }
        val material =
            ExportMaterial(
                "bass-transfer-${pairing.hostDeviceId}.json",
                PairingQr(
                    hostDeviceId = pairing.hostDeviceId,
                    certificateSha256 = pairing.certificateSha256,
                    purpose = CommissioningPurpose.TRANSFER,
                    credentialSecret = pending.transferSecret,
                ).json().toString(2),
            )
        val completed =
            withContext(NonCancellable) {
                store.completeTransfer(pending, material, writeScope)
            }
        check(completed) { "Transfer write was superseded" }
        return material
    }
}

internal fun Throwable.isDefinitiveCommissioningFailure(): Boolean =
    this is ApiException &&
        errorCode !in RETRYABLE_COMMISSIONING_ERRORS &&
        statusCode in setOf(401, 403)

internal fun Throwable.isDefinitiveTransferFailure(): Boolean =
    this is ApiException &&
        errorCode !in RETRYABLE_COMMISSIONING_ERRORS &&
        statusCode in setOf(401, 403)

private val RETRYABLE_COMMISSIONING_ERRORS =
    setOf(
        "pairing_window_required",
        "recovery_window_required",
        "request_id_reused",
        "runtime_reload_failed",
        "runtime_drain_failed",
    )

internal fun pairingInviteMaterial(
    response: JSONObject,
    targetUserId: String,
    expectedPairing: Pairing,
): ExportMaterial {
    val token = response.getString("invite_token")
    require(token.matches(HEX_SECRET_PATTERN)) { "Invalid invite token" }
    val expiresIn = response.getInt("expires_in")
    require(expiresIn in 1..300) { "Invalid invite lifetime" }
    val rawPayload =
        when (val payload = response.get("qr_payload")) {
            is JSONObject -> payload.toString()
            is String -> payload
            else -> error("Invalid pairing invite payload")
        }
    val invite = PairingInviteQr.parse(rawPayload)
    require(
        invite.inviteToken == token &&
            invite.hostDeviceId == expectedPairing.hostDeviceId &&
            invite.certificateSha256 == expectedPairing.certificateSha256
    ) { "Pairing invite identity mismatch" }
    return ExportMaterial(
        "bass-invite-$targetUserId.json",
        JSONObject(rawPayload).put("expires_in", expiresIn).toString(2),
    )
}

private fun PendingCommissioning.recoveryExport(): ExportMaterial? {
    if (newRecoverySecret.isEmpty()) return null
    val recoveryQr =
        PairingQr(
            hostDeviceId = qr.hostDeviceId,
            certificateSha256 = qr.certificateSha256,
            purpose = CommissioningPurpose.RECOVERY,
            credentialSecret = newRecoverySecret,
        )
    return ExportMaterial(
        fileName = "bass-recovery-${qr.hostDeviceId}.json",
        content = recoveryQr.json().toString(2),
    )
}
