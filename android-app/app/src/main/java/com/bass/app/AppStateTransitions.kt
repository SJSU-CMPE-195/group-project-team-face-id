package com.bass.app

import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import org.json.JSONObject

internal data class DeviceSnapshot(
    val identity: SessionIdentity,
    val status: JSONObject,
    val faces: FaceStatus,
    val users: List<User>,
    val settings: AppSettings,
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
)

internal suspend fun ActiveSession.cancelRemote() =
    withContext(NonCancellable) {
        runCatching {
            api.json(
                "/api/$kind/cancel",
                "POST",
                JSONObject().put("session_id", id),
            )
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
                clientCamera = json.optBoolean("client_camera"),
                deviceCamera =
                    json.optBoolean(
                        "device_camera",
                        json.optBoolean("pi_camera"),
                    ),
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
        settings = json("/api/settings").settings(),
        logs = if (identity.user.isAdmin) array("/api/logs").logs() else emptyList(),
    )
}

internal fun BassState.withSnapshot(
    snapshot: DeviceSnapshot,
    keepEditedSettings: Boolean,
): BassState {
    val runtime = snapshot.status.optJSONObject("runtime")
    val owner = runtime?.optString("unlock_owner")?.takeIf { it.isNotBlank() && it != "null" }
    return copy(
        status = snapshot.status.deviceStatus(capabilities),
        users = snapshot.users,
        faceStatus = snapshot.faces,
        logs = snapshot.logs,
        settings = if (keepEditedSettings) settings else snapshot.settings,
        unlockOwner = owner,
        identity = snapshot.identity,
        selectedTab =
            if (!snapshot.identity.user.isAdmin && selectedTab == Tab.LOGS) {
                Tab.CONSOLE
            } else {
                selectedTab
            },
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
        phase in setOf("completed", "granted", "denied", "cancelled", "error", "timeout", "expired")
    val failed = phase in setOf("denied", "error", "timeout", "expired")
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
