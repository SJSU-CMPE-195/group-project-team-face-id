package com.bass.app

import android.os.Build
import java.security.SecureRandom
import java.util.UUID
import org.json.JSONObject

enum class OperationAction(val wireValue: String) {
    SCAN_UNLOCK("scan.unlock"),
    SCAN_IGNITION("scan.ignition"),
    USER_CREATE("user.create"),
    USER_DELETE("user.delete"),
    USER_ACCESS("user.access"),
    ENROLLMENT_START("enrollment.start"),
    SETTINGS_UPDATE("settings.update"),
    PAIRING_INVITE("pairing.invite"),
    DEVICE_LOCK("device.lock"),
    IGNITION_STOP("ignition.stop"),
    DEVICE_RESET("device.reset"),
}

internal val OperationAction.requiresActuatorControl: Boolean
    get() = when (this) {
        OperationAction.SCAN_UNLOCK,
        OperationAction.SCAN_IGNITION,
        OperationAction.DEVICE_LOCK,
        OperationAction.IGNITION_STOP,
        OperationAction.DEVICE_RESET -> true
        else -> false
    }

data class OperationRequest(
    val action: OperationAction,
    val targetUserId: String = "",
)

data class OperationGrant(
    val token: String,
    val expiresIn: Int,
)

object DeviceAuth {
    fun deviceName(): String =
        "${Build.MANUFACTURER} ${Build.MODEL}".trim().take(128).ifEmpty { "Android" }

    fun requestId(): String = UUID.randomUUID().toString()

    fun randomSecret(): String =
        ByteArray(32).also(SecureRandom()::nextBytes).joinToString("") {
            (it.toInt() and 0xff).toString(16).padStart(2, '0')
        }

    suspend fun operationGrant(
        api: DeviceApi,
        pin: CharArray,
        request: OperationRequest,
    ): OperationGrant {
        require(pin.size == 6 && pin.all { it in '0'..'9' }) { "PIN must contain six digits" }
        val body =
            JSONObject()
                .put("pin", String(pin))
                .put("action", request.action.wireValue)
                .put("target_user_id", request.targetUserId)
        val response = api.json("/api/operation-grants", "POST", body)
        val token = response.getString("grant_token")
        require(token.matches(Regex("[A-Za-z0-9_-]{32,512}"))) { "Invalid operation grant" }
        val expiresIn = response.getInt("expires_in")
        require(expiresIn in 1..30) { "Invalid operation grant lifetime" }
        return OperationGrant(token, expiresIn)
    }

    suspend fun sessionLogin(
        api: DeviceApi,
        pin: CharArray,
        expected: SessionIdentity,
    ): SessionIdentity {
        require(pin.size == 6 && pin.all { it in '0'..'9' }) { "PIN must contain six digits" }
        val identity =
            api.json(
                "/api/session-login",
                "POST",
                JSONObject().put("pin", String(pin)),
            ).sessionIdentity()
        if (identity.deviceId != expected.deviceId || identity.user.id != expected.user.id) {
            throw DeviceIdentityException("Device identity mismatch")
        }
        return identity
    }
}
