package com.bass.app

import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import org.json.JSONObject

internal data class DeviceSnapshot(
    val identity: SessionIdentity,
    val status: JSONObject,
    val faces: FaceStatus,
    val users: List<User>,
    val pairedDevices: List<PairedDevice>,
    val logs: List<LogEntry>,
)

internal data class SessionTransition(
    val state: BassState,
    val phase: String,
    val terminal: Boolean,
)

internal val Throwable.pairingStorageErrorResource: Int
    get() =
        if (this is PairingUpgradeRequiredException) R.string.core_pairing_upgrade_required
        else R.string.core_pairing_unavailable

internal data class PendingStart(val deviceGeneration: Long, val sessionGeneration: Long)

internal data class ActiveSession(
    val id: String,
    val kind: String,
    val api: DeviceApi,
    val generation: Long,
    val flow: CaptureFlow,
    val requestId: String? = null,
)

internal suspend fun ActiveSession.cancelRemote() =
    withContext(NonCancellable) {
        runCatching {
            api.cancelFirst {
                json(
                    "/api/$kind/cancel",
                    "POST",
                    JSONObject().put("session_id", id),
                )
            }
        }
        Unit
    }

internal fun BassState.afterBackground(
    hasPairing: Boolean,
    interruptedExchangeError: String?,
): BassState =
    copy(
        phase =
            if (hasPairing) ConnectionPhase.SIGN_IN_REQUIRED
            else ConnectionPhase.UNPAIRED,
        busy = false,
        unlockOwner = null,
        pairingCredentialsRequired = false,
        showForgetDeviceDialog = false,
        forgetDeviceError = null,
        error = interruptedExchangeError ?: error,
    )

internal fun BassState.startQrCapture(): BassState =
    copy(
        phase = ConnectionPhase.UNPAIRED,
        captureFlow = CaptureFlow.QR,
        busy = false,
        error = null,
    )

internal fun BassState.startConnection(
    needsPairingCredentials: Boolean = false,
): BassState =
    copy(
        phase = ConnectionPhase.DISCOVERING,
        pairingCredentialsRequired = needsPairingCredentials,
        busy = needsPairingCredentials,
        error = null,
        unlockOwner = null,
    )

internal fun BassState.withPendingPairing(identity: SessionIdentity, message: String): BassState =
    copy(
        phase = ConnectionPhase.OFFLINE,
        pairingCredentialsRequired = false,
        identity = identity,
        busy = false,
        error = message,
    )

internal fun BassState.withConnectedDevice(connected: ConnectedDevice): BassState {
    val json = connected.info.getJSONObject("capabilities")
    val simulated = json.optBoolean("simulated_actuators")
    return copy(
        identity = connected.pairing.identity,
        device =
            DeviceInfo(
                connected.pairing.hostDeviceId,
                connected.info.optString("name", "BASS"),
                connected.api.baseUrl,
                connected.info.getInt("protocol_version"),
            ),
        capabilities =
            Capabilities(
                deviceCamera =
                    json.optBoolean(
                        "device_camera",
                        json.optBoolean("pi_camera"),
                    ),
                cameraSource = json.optString("camera_source", "unknown"),
                localControls = json.optBoolean("local_controls", false),
                simulatedActuator = simulated,
                livenessAvailable = json.optBoolean("liveness_available"),
                actuatorControlAvailable =
                    json.optBoolean("actuator_control_available", simulated),
                actuatorFeedback = json.actuatorFeedback(),
            ),
    )
}

internal suspend fun DeviceApi.readSnapshot(
    expectedIdentity: SessionIdentity,
): DeviceSnapshot {
    val identity = json("/api/me").sessionIdentity()
    if (
        identity.deviceId != expectedIdentity.deviceId ||
            identity.user.id != expectedIdentity.user.id
    ) {
        throw DeviceIdentityException("Device identity mismatch")
    }
    val status = json("/api/status")
    val faces = json("/api/face-status").faceStatus()
    return DeviceSnapshot(
        identity = identity,
        status = status,
        faces = faces,
        users = array("/api/users").users(faces.enrolledNames),
        pairedDevices =
            if (identity.user.isAdmin) array("/api/devices").devices() else emptyList(),
        logs = array("/api/logs").logs(),
    )
}

internal fun BassState.withSnapshot(snapshot: DeviceSnapshot): BassState {
    val runtime = snapshot.status.optJSONObject("runtime")
    val owner = runtime?.optString("unlock_owner")?.takeIf { it.isNotBlank() && it != "null" }
    return copy(
        status = snapshot.status.deviceStatus(capabilities),
        users = snapshot.users,
        pairedDevices = snapshot.pairedDevices,
        faceStatus = snapshot.faces,
        logs = snapshot.logs,
        unlockOwner = owner,
        identity = snapshot.identity,
        inviteMaterial = inviteMaterial.takeIf { snapshot.identity.user.isAdmin },
    )
}

internal fun BassState.startCapture(
    flow: CaptureFlow,
    source: CameraSource,
): BassState =
    copy(
        busy = true,
        captureFlow = flow,
        cameraSource = source,
        error = null,
        activeSessionProgress = 0,
        activeSessionTotal = null,
        activeSessionMessage = null,
    )

internal fun BassState.withSessionResult(
    result: JSONObject,
    kind: String,
    flow: CaptureFlow,
    failureMessage: String,
): SessionTransition {
    val phase = result.optString("state")
    val message = result.optString("message").takeIf { it.isNotBlank() }
    val terminal =
        phase in
            setOf(
                "completed",
                "granted",
                "denied",
                "cancelled",
                "error",
                "timeout",
                "expired",
                "failed",
                "interrupted",
            )
    val failed = phase in setOf("denied", "error", "timeout", "expired", "failed", "interrupted")
    val window = result.optJSONObject("window")
    return SessionTransition(
        state =
            copy(
                activeSessionProgress =
                    if (kind == "enroll") result.optInt("count") else window?.optInt("matches"),
                activeSessionTotal =
                    if (kind == "enroll") {
                        result.optInt("samples_needed", 10)
                    } else {
                        window?.optInt("needed")
                    },
                activeSessionMessage = message ?: phase,
                error = if (failed) message ?: failureMessage else error,
                unlockOwner =
                    if (phase == "granted" && flow == CaptureFlow.VERIFY_UNLOCK) {
                        identity?.user?.name
                    } else {
                        unlockOwner
                    },
                busy = !terminal,
                captureFlow = if (terminal) CaptureFlow.NONE else captureFlow,
            ),
        phase = phase,
        terminal = terminal,
    )
}

internal fun JSONObject.enrollmentProjection(): JSONObject =
    optJSONObject("result")
        ?: optJSONObject("session")
        ?: JSONObject()
            .put("session_id", getString("session_id"))
            .put("request_id", getString("request_id"))
            .put("state", getString("state"))
