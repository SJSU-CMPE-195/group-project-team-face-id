package com.bass.app

import java.io.InputStream
import org.json.JSONObject

private const val MAX_QR_BYTES = 1024
private const val MAX_PENDING_COMMISSIONING_BYTES = 8192
private val SHA256_PATTERN = Regex("[a-f0-9]{64}")
internal val HEX_SECRET_PATTERN = Regex("[a-f0-9]{64}")
private val LEGACY_SECRET_PATTERN = Regex("[A-Za-z0-9_-]{32,512}")

class PairingUpgradeRequiredException : IllegalArgumentException()

data class PairingInviteQr(
    val hostDeviceId: String,
    val certificateSha256: String,
    val inviteToken: String,
) {
    init {
        hostDeviceId.validatedUuid()
        require(certificateSha256.matches(SHA256_PATTERN)) {
            "Invalid TLS certificate fingerprint"
        }
        require(inviteToken.matches(HEX_SECRET_PATTERN)) { "Invalid pairing invite" }
    }

    fun deviceQr() =
        PairingQr(
            hostDeviceId = hostDeviceId,
            certificateSha256 = certificateSha256,
            purpose = CommissioningPurpose.DEVICE,
        )

    companion object {
        fun parse(raw: String): PairingInviteQr {
            require(raw.length <= MAX_QR_BYTES) { "Invalid pairing invite" }
            val json = JSONObject(raw)
            require(
                json.getInt("version") == 3 &&
                    json.getString("purpose") == "pairing_invite"
            ) { "Invalid pairing invite" }
            require(
                setOf(
                    "pin",
                    "pairing_key",
                    "activation_secret",
                    "recovery_secret",
                    "transfer_secret",
                    "device_token",
                ).none(json::has)
            ) { "Pairing invite contains an unexpected credential" }
            return PairingInviteQr(
                hostDeviceId = json.getString("device_id"),
                certificateSha256 = json.getString("tls_certificate_sha256"),
                inviteToken = json.getString("invite_token"),
            )
        }
    }
}

internal fun InputStream.readSecurityMaterial(maxChars: Int = MAX_PENDING_COMMISSIONING_BYTES): String =
    bufferedReader().use { reader ->
        val result = StringBuilder()
        val buffer = CharArray(1024)
        while (true) {
            val count = reader.read(buffer)
            if (count < 0) break
            require(result.length + count <= maxChars) { "Security material is too large" }
            result.append(buffer, 0, count)
        }
        result.toString()
    }

data class PairingQr(
    val hostDeviceId: String,
    val certificateSha256: String,
    val purpose: CommissioningPurpose,
    val credentialSecret: String? = null,
    val onboardingKey: String? = null,
    val version: Int = 3,
) {
    init {
        require(version in 2..3) { "Unsupported pairing version" }
        if (version == 2) {
            require(
                purpose == CommissioningPurpose.DEVICE &&
                    onboardingKey?.matches(LEGACY_SECRET_PATTERN) == true &&
                    credentialSecret == null
            ) { "Invalid legacy pairing QR" }
        } else {
            require(onboardingKey == null) { "Version 3 QR cannot contain a pairing key" }
            if (purpose == CommissioningPurpose.DEVICE) {
                require(credentialSecret == null) { "Public device QR cannot contain a secret" }
            } else {
                require(credentialSecret?.matches(HEX_SECRET_PATTERN) == true) {
                    "Invalid commissioning secret"
                }
            }
        }
    }

    fun json(): JSONObject =
        JSONObject()
            .put("version", version)
            .put("device_id", hostDeviceId)
            .put("tls_certificate_sha256", certificateSha256)
            .put("purpose", purpose.wireValue)
            .apply {
                onboardingKey?.let { put("pairing_key", it) }
                credentialSecret?.let {
                    put(
                        when (purpose) {
                            CommissioningPurpose.ACTIVATION -> "activation_secret"
                            CommissioningPurpose.RECOVERY -> "recovery_secret"
                            CommissioningPurpose.TRANSFER -> "transfer_secret"
                            CommissioningPurpose.DEVICE -> error("Device QR cannot contain a secret")
                        },
                        it,
                    )
                }
            }

    companion object {
        fun parse(raw: String): PairingQr {
            require(raw.length <= MAX_QR_BYTES) { "Invalid QR code" }
            val json = JSONObject(raw)
            val version = json.optInt("version", -1)
            if (version == 1) throw PairingUpgradeRequiredException()
            val hostDeviceId = json.getString("device_id").validatedUuid()
            val certificateSha256 = json.getString("tls_certificate_sha256")
            require(certificateSha256.matches(SHA256_PATTERN)) {
                "Invalid TLS certificate fingerprint"
            }
            if (version == 2) {
                val onboardingKey = json.getString("pairing_key")
                require(onboardingKey.matches(LEGACY_SECRET_PATTERN)) { "Invalid onboarding key" }
                return PairingQr(
                    hostDeviceId = hostDeviceId,
                    certificateSha256 = certificateSha256,
                    purpose = CommissioningPurpose.DEVICE,
                    onboardingKey = onboardingKey,
                    version = 2,
                )
            }
            require(version == 3) { "Unsupported pairing version" }
            val purpose =
                CommissioningPurpose.entries.singleOrNull {
                    it.wireValue == json.getString("purpose")
                } ?: error("Unsupported QR purpose")
            val secretKey =
                when (purpose) {
                    CommissioningPurpose.DEVICE -> null
                    CommissioningPurpose.ACTIVATION -> "activation_secret"
                    CommissioningPurpose.RECOVERY -> "recovery_secret"
                    CommissioningPurpose.TRANSFER -> "transfer_secret"
                }
            val secretFields =
                setOf("activation_secret", "recovery_secret", "transfer_secret", "pairing_key")
            val allowedSecretFields = setOfNotNull(secretKey)
            require(secretFields.none { json.has(it) && it !in allowedSecretFields }) {
                "QR contains a credential for the wrong purpose"
            }
            val secret = secretKey?.let(json::getString)
            require(secret == null || secret.matches(HEX_SECRET_PATTERN)) {
                "Invalid commissioning secret"
            }
            return PairingQr(hostDeviceId, certificateSha256, purpose, secret, version = 3)
        }
    }
}

data class PendingCommissioning(
    val qr: PairingQr,
    val requestId: String,
    val deviceToken: String,
    val deviceName: String,
    val pin: String,
    val name: String = "",
    val inviteToken: String = "",
    val newRecoverySecret: String = "",
) {
    init {
        requestId.validatedUuid()
        require(deviceToken.matches(HEX_SECRET_PATTERN)) { "Invalid device token" }
        require(pin.matches(Regex("[0-9]{6}"))) { "Invalid PIN" }
        require(deviceName.isNotBlank() && deviceName.length <= 128) { "Invalid device name" }
        require(name.length <= 128 && inviteToken.length <= 512) { "Invalid commissioning input" }
        require(newRecoverySecret.isEmpty() || newRecoverySecret.matches(HEX_SECRET_PATTERN)) {
            "Invalid recovery secret"
        }
        when (qr.purpose) {
            CommissioningPurpose.DEVICE -> {
                require(name.isEmpty() && newRecoverySecret.isEmpty()) {
                    "Normal pairing cannot change ownership"
                }
                require(inviteToken.matches(HEX_SECRET_PATTERN)) { "Invalid pairing invite" }
            }
            CommissioningPurpose.ACTIVATION,
            CommissioningPurpose.TRANSFER,
            -> {
                require(name.isNotBlank() && inviteToken.isEmpty()) {
                    "Owner name is required"
                }
                require(newRecoverySecret.matches(HEX_SECRET_PATTERN)) {
                    "Recovery secret is required"
                }
            }
            CommissioningPurpose.RECOVERY -> {
                require(name.isEmpty() && inviteToken.isEmpty()) {
                    "Recovery request contains unrelated credentials"
                }
                require(newRecoverySecret.matches(HEX_SECRET_PATTERN)) {
                    "Recovery secret is required"
                }
            }
        }
    }

    fun json(): String =
        JSONObject()
            .put("version", 1)
            .put("qr", qr.json())
            .put("request_id", requestId)
            .put("device_token", deviceToken)
            .put("device_name", deviceName)
            .put("pin", pin)
            .put("name", name)
            .put("invite_token", inviteToken)
            .put("new_recovery_secret", newRecoverySecret)
            .toString()

    fun requestBody(): JSONObject =
        JSONObject()
            .put("request_id", requestId)
            .put("pin", pin)
            .put("device_name", deviceName)
            .put("device_token", deviceToken)
            .apply {
                when (qr.purpose) {
                    CommissioningPurpose.DEVICE -> put("invite_token", inviteToken)
                    CommissioningPurpose.ACTIVATION -> {
                        put("activation_secret", checkNotNull(qr.credentialSecret))
                        put("name", name)
                        put("recovery_secret", newRecoverySecret)
                    }
                    CommissioningPurpose.RECOVERY -> {
                        put("recovery_secret", checkNotNull(qr.credentialSecret))
                        put("new_recovery_secret", newRecoverySecret)
                    }
                    CommissioningPurpose.TRANSFER -> {
                        put("transfer_secret", checkNotNull(qr.credentialSecret))
                        put("name", name)
                        put("recovery_secret", newRecoverySecret)
                    }
                }
            }

    companion object {
        fun parse(raw: String): PendingCommissioning {
            require(raw.length <= MAX_PENDING_COMMISSIONING_BYTES) { "Invalid pending request" }
            val json = JSONObject(raw)
            require(json.getInt("version") == 1) { "Unsupported pending request" }
            return PendingCommissioning(
                qr = PairingQr.parse(json.getJSONObject("qr").toString()),
                requestId = json.getString("request_id"),
                deviceToken = json.getString("device_token"),
                deviceName = json.getString("device_name"),
                pin = json.getString("pin"),
                name = json.optString("name"),
                inviteToken = json.optString("invite_token"),
                newRecoverySecret = json.optString("new_recovery_secret"),
            )
        }
    }
}

data class PendingTransfer(
    val requestId: String,
    val pin: String,
    val transferSecret: String,
) {
    init {
        requestId.validatedUuid()
        require(pin.matches(Regex("[0-9]{6}"))) { "Invalid PIN" }
        require(transferSecret.matches(HEX_SECRET_PATTERN)) { "Invalid transfer secret" }
    }

    fun json(): String =
        JSONObject()
            .put("request_id", requestId)
            .put("pin", pin)
            .put("transfer_secret", transferSecret)
            .toString()

    companion object {
        fun parse(raw: String): PendingTransfer {
            val json = JSONObject(raw)
            return PendingTransfer(
                json.getString("request_id"),
                json.getString("pin"),
                json.getString("transfer_secret"),
            )
        }
    }
}
