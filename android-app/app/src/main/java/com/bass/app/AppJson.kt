package com.bass.app

import org.json.JSONArray
import org.json.JSONObject

internal fun JSONObject.deviceStatus(
    capabilities: Capabilities,
    requestedAtElapsedMillis: Long,
): DeviceStatus {
    val runtime = optJSONObject("runtime")
    return DeviceStatus(
        locked = optString("lockState") == "locked",
        ignitionOn = optBoolean("ignitionOn"),
        battery = optionalInt("battery"),
        signal = optionalInt("signal"),
        online = optBoolean("online", true),
        simulatedActuator =
            runtime?.optBoolean("simulated_actuators", capabilities.simulatedActuator)
                ?: capabilities.simulatedActuator,
        actuatorControlAvailable =
            runtime?.optBoolean(
                "actuator_control_available",
                capabilities.actuatorControlAvailable,
            ) ?: capabilities.actuatorControlAvailable,
        actuatorFeedback =
            runtime?.actuatorFeedback() ?: capabilities.actuatorFeedback,
        physicalStateConfirmed = runtime?.optBoolean("physical_state_confirmed") == true,
        cameraSource =
            runtime?.optString("camera_source")?.takeIf { it.isNotBlank() }
                ?: capabilities.cameraSource,
        localControls =
            runtime?.optBoolean("local_controls", capabilities.localControls)
                ?: capabilities.localControls,
        controller =
            runtime?.controllerState(requestedAtElapsedMillis) ?: ControllerState(),
    )
}

private fun JSONObject.controllerState(requestedAtElapsedMillis: Long): ControllerState {
    val raw = optJSONObject("controller_state") ?: return ControllerState()
    val lock =
        when (raw.opt("lock")) {
            "locked" -> ControllerLockState.LOCKED
            "unlocked" -> ControllerLockState.UNLOCKED
            "locking" -> ControllerLockState.LOCKING
            "unlocking" -> ControllerLockState.UNLOCKING
            "unknown" -> ControllerLockState.UNKNOWN
            else -> return ControllerState()
        }
    val ignition =
        when (raw.opt("ignition")) {
            "on" -> ControllerIgnitionState.ON
            "off" -> ControllerIgnitionState.OFF
            "unknown" -> ControllerIgnitionState.UNKNOWN
            else -> return ControllerState()
        }
    val moving =
        when (val value = raw.opt("moving")) {
            is Boolean -> value
            null, JSONObject.NULL -> null
            else -> return ControllerState()
        }
    val fresh = raw.opt("fresh") as? Boolean ?: return ControllerState()
    val ageMillis =
        when (val value = raw.opt("age_ms")) {
            is Byte, is Short, is Int, is Long ->
                (value as Number).toLong().takeIf { it >= 0L } ?: return ControllerState()
            null, JSONObject.NULL -> null
            else -> return ControllerState()
        }
    val expectedMoving =
        when (lock) {
            ControllerLockState.LOCKED, ControllerLockState.UNLOCKED -> false
            ControllerLockState.LOCKING, ControllerLockState.UNLOCKING -> true
            ControllerLockState.UNKNOWN -> null
        }
    if (moving != expectedMoving || (fresh && ageMillis == null)) return ControllerState()
    return ControllerState(
        lock = lock,
        ignition = ignition,
        moving = moving,
        fresh = fresh,
        ageMillis = ageMillis,
        requestedAtElapsedMillis = requestedAtElapsedMillis,
    )
}

internal fun JSONObject.faceStatus(): FaceStatus {
    val enrolled = optJSONArray("enrolled")
    val names =
        if (enrolled == null) emptyList()
        else {
            (0 until enrolled.length()).map { enrolled.getString(it) }
        }
    return FaceStatus(names, optInt("count"))
}

internal fun JSONArray.users(enrolledNames: List<String>): List<User> =
    objects().map { row ->
        val name = row.getString("name")
        val enrolled =
            when {
                row.has("faceEnrolled") -> row.optBoolean("faceEnrolled")
                row.has("face_enrolled") -> row.optBoolean("face_enrolled")
                else -> enrolledNames.any { it.equals(name, ignoreCase = true) }
            }
        User(
            id = row.getString("id"),
            name = name,
            faceAccess = row.optBoolean("faceAccess", true),
            faceEnrolled = enrolled,
            createdAtMillis = row.optLong("createdAt"),
            isAdmin = row.optBoolean("is_admin", row.optBoolean("isAdmin")),
            isOwner = row.optBoolean("is_owner", row.optBoolean("isOwner")),
        )
    }

internal fun JSONArray.devices(): List<PairedDevice> =
    objects().map { row ->
        PairedDevice(
            id = row.getString("id"),
            userId = row.getString("user_id"),
            userName = row.optString("user_name"),
            name = row.optString("name", "Android"),
            active = row.optBoolean("active"),
            lastSeenMillis = row.optLong("last_seen") * 1000L,
            userIsOwner = row.optBoolean("user_is_owner"),
        )
    }

internal fun JSONObject.sessionIdentity(): SessionIdentity {
    val userJson = getJSONObject("user")
    return SessionIdentity(
        user =
            SessionUser(
                id = userJson.getString("id").validatedUuid(),
                name = userJson.getString("name").validatedName(),
                isAdmin = userJson.optBoolean("is_admin", userJson.optBoolean("isAdmin")),
                isOwner = userJson.optBoolean("is_owner", userJson.optBoolean("isOwner")),
            ),
        deviceId = getString("device_id").validatedUuid(),
    )
}

internal fun JSONArray.logs(): List<LogEntry> =
    objects().map { row ->
        val type = row.optString("type")
        val ok = row.optBoolean("ok")
        val outcome = row.optString("outcome").logOutcome(ok)
        LogEntry(
            id = row.optString("id"),
            timestampMillis = row.optLong("ts"),
            type = type,
            ok = ok,
            detail = row.optString("detail"),
            title = row.optString("title").trim().ifEmpty { type.logTitle() },
            summary =
                row.optString("summary").trim().ifEmpty {
                    when (outcome) {
                        LogOutcome.SUCCESS -> "The action completed."
                        LogOutcome.DENIED -> "Permission was denied."
                        LogOutcome.CANCELLED -> "The action was cancelled."
                        LogOutcome.ERROR -> "The action failed."
                        LogOutcome.INFO -> "Event recorded."
                    }
                },
            outcome = outcome,
        )
    }

private fun String.logOutcome(ok: Boolean): LogOutcome =
    when (lowercase()) {
        "success" -> LogOutcome.SUCCESS
        "denied" -> LogOutcome.DENIED
        "cancelled" -> LogOutcome.CANCELLED
        "error" -> LogOutcome.ERROR
        "info" -> LogOutcome.INFO
        else -> if (ok) LogOutcome.SUCCESS else LogOutcome.ERROR
    }

private fun String.logTitle(): String =
    when (lowercase()) {
        "face_scan", "scan_timing" -> "Face verification"
        "enroll", "enrollment", "enroll_embedding" -> "Face enrollment"
        "access_change" -> "Access changed"
        "delete_user" -> "Account removed"
        "unlock" -> "Unlock request"
        "lock" -> "Lock request"
        "ignition", "ignition_start", "ignition_stop" -> "Ignition request"
        "settings" -> "Settings updated"
        "session_login" -> "Sign-in"
        "operation_authorization" -> "PIN authorization"
        else -> "Device event"
    }

internal fun JSONObject.settings() =
    AppSettings(
        autoRelockSeconds = optInt("autoRelockSeconds", 10),
        ignitionAutoStopSeconds = optInt("ignitionAutoStopSeconds", 20),
        promptAutoLockSeconds = optInt("promptAutoLockSeconds"),
        liveness = optBoolean("liveness", true),
        failLockout = optBoolean("failLockout", true),
        lockoutAfter = optInt("lockoutAfter", 5),
    )

internal fun AppSettings.json() =
    JSONObject().apply {
        put("autoRelockSeconds", autoRelockSeconds)
        put("ignitionAutoStopSeconds", ignitionAutoStopSeconds)
        put("promptAutoLockSeconds", promptAutoLockSeconds)
        put("liveness", liveness)
        put("failLockout", failLockout)
        put("lockoutAfter", lockoutAfter)
    }

private fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }

private fun JSONObject.optionalInt(key: String): Int? =
    if (has(key) && !isNull(key)) optInt(key) else null

internal fun JSONObject.actuatorFeedback(): String =
    when (val feedback = opt("actuator_feedback")) {
        is String -> feedback.takeIf { it in setOf("unavailable", "simulated", "available") }
            ?: "unavailable"
        true -> "available"
        else -> "unavailable"
    }
