package com.bass.app

import org.json.JSONArray
import org.json.JSONObject

internal fun JSONObject.deviceStatus(simulated: Boolean) =
    DeviceStatus(
        locked = optString("lockState") == "locked",
        ignitionOn = optBoolean("ignitionOn"),
        battery = optionalInt("battery"),
        signal = optionalInt("signal"),
        online = optBoolean("online", true),
        simulatedActuator = simulated,
    )

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
