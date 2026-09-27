package com.bass.app

const val MAX_USER_NAME_LENGTH = 100

enum class Tab {
    CONSOLE,
    USERS,
    LOGS,
    SETTINGS,
}

enum class ConnectionPhase {
    UNPAIRED,
    DISCOVERING,
    CONNECTING,
    SIGN_IN_REQUIRED,
    CONNECTED,
    UNAUTHORIZED,
    OFFLINE,
    ERROR,
}

enum class CaptureFlow {
    NONE,
    QR,
    ENROLL,
    VERIFY_UNLOCK,
    VERIFY_IGNITION,
}

enum class CameraSource {
    PHONE,
    DEVICE,
}

enum class PinStage {
    VERIFY,
    CONFIRM_NEW_USER,
}

enum class CommissioningPurpose(val wireValue: String) {
    DEVICE("device"),
    ACTIVATION("activation"),
    RECOVERY("recovery"),
    TRANSFER("transfer"),
}

data class CommissioningPrompt(
    val purpose: CommissioningPurpose,
    val busy: Boolean = false,
)

data class ExportMaterial(
    val fileName: String,
    val content: String,
)

enum class PinPurpose {
    LOGIN,
    ENROLL,
    UNLOCK,
    IGNITION,
    USER_ADMIN,
    SETTINGS,
    LOCK,
    STOP_IGNITION,
    RESET,
}

data class PinPrompt(
    val id: Long,
    val stage: PinStage,
    val purpose: PinPurpose,
    val confirmationName: String? = null,
    val busy: Boolean = false,
    val error: String? = null,
    val lockedSeconds: Int = 0,
    val inputRevision: Int = 0,
)

data class NewUserDraft(
    val name: String = "",
    val pin: String = "",
    val isAdmin: Boolean = false,
    val revision: Long = 0,
)

data class DeviceInfo(
    val id: String,
    val name: String,
    val address: String,
    val protocolVersion: Int = 3,
)

data class DeviceStatus(
    val locked: Boolean = true,
    val ignitionOn: Boolean = false,
    val battery: Int? = null,
    val signal: Int? = null,
    val online: Boolean = true,
    val simulatedActuator: Boolean = false,
    val actuatorControlAvailable: Boolean = false,
    val actuatorFeedback: String = "unavailable",
    val physicalStateConfirmed: Boolean = false,
)

data class Capabilities(
    val clientCamera: Boolean = false,
    val deviceCamera: Boolean = false,
    val simulatedActuator: Boolean = false,
    val livenessAvailable: Boolean = false,
    val actuatorControlAvailable: Boolean = false,
    val actuatorFeedback: String = "unavailable",
)

data class User(
    val id: String,
    val name: String,
    val faceAccess: Boolean,
    val enrolled: Boolean,
    val createdAtMillis: Long,
    val isAdmin: Boolean = false,
    val isOwner: Boolean = false,
)

data class SessionUser(
    val id: String,
    val name: String,
    val isAdmin: Boolean,
    val isOwner: Boolean = false,
)

data class SessionIdentity(
    val user: SessionUser,
    val deviceId: String,
)

data class FaceStatus(val enrolledNames: List<String> = emptyList(), val count: Int = 0)

data class LogEntry(
    val id: String,
    val timestampMillis: Long,
    val type: String,
    val ok: Boolean,
    val detail: String,
)

data class AppSettings(
    val autoRelockSeconds: Int = 10,
    val ignitionAutoStopSeconds: Int = 20,
    val promptAutoLockSeconds: Int = 0,
    val liveness: Boolean = true,
    val failLockout: Boolean = true,
    val lockoutAfter: Int = 5,
)

data class BassState(
    val pinPrompt: PinPrompt? = null,
    val showForgetDeviceDialog: Boolean = false,
    val forgetDeviceError: String? = null,
    val phase: ConnectionPhase = ConnectionPhase.UNPAIRED,
    val pairingCredentialsRequired: Boolean = false,
    val pairingInviteReady: Boolean = false,
    val commissioningPrompt: CommissioningPrompt? = null,
    val exportMaterial: ExportMaterial? = null,
    val inviteMaterial: ExportMaterial? = null,
    val transferPending: Boolean = false,
    val identity: SessionIdentity? = null,
    val device: DeviceInfo? = null,
    val status: DeviceStatus? = null,
    val capabilities: Capabilities = Capabilities(),
    val users: List<User> = emptyList(),
    val faceStatus: FaceStatus = FaceStatus(),
    val logs: List<LogEntry> = emptyList(),
    val settings: AppSettings = AppSettings(),
    val busy: Boolean = false,
    val captureFlow: CaptureFlow = CaptureFlow.NONE,
    val cameraSource: CameraSource = CameraSource.PHONE,
    val activeSessionProgress: Int? = null,
    val activeSessionTotal: Int? = null,
    val activeSessionMessage: String? = null,
    val unlockOwner: String? = null,
    val promptCountdown: Int? = null,
    val error: String? = null,
    val selectedTab: Tab = Tab.CONSOLE,
)

internal val BassState.canControlActuators: Boolean
    get() = status?.actuatorControlAvailable ?: capabilities.actuatorControlAvailable
