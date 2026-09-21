package com.bass.app

import org.json.JSONArray
import org.json.JSONObject

internal fun JSONObject.deviceStatus(capabilities: Capabilities): DeviceStatus {
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
        val enrolled = enrolledNames.any { it.equals(name, ignoreCase = true) }
        User(
            id = row.getString("id"),
            name = name,
            faceAccess = row.optBoolean("faceAccess", true),
            enrolled = enrolled,
            createdAtMillis = row.optLong("createdAt"),
            isAdmin = row.optBoolean("is_admin", row.optBoolean("isAdmin")),
            isOwner = row.optBoolean("is_owner", row.optBoolean("isOwner")),
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
        LogEntry(
            id = row.optString("id"),
            timestampMillis = row.optLong("ts"),
            type = row.optString("type"),
            ok = row.optBoolean("ok"),
            detail = row.optString("detail"),
        )
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
