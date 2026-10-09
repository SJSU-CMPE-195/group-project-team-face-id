package com.bass.app

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

private const val STORAGE_VERSION = 3
private const val MAX_SAVED_PAIRING_BYTES = 4096
private const val MAX_PENDING_ENROLLMENT_BYTES = 16_384
private const val MAX_ENROLLMENT_JOURNAL_BYTES = 65_536
private const val MAX_ENROLLMENT_JOURNAL_RECORDS = 16
private val SHA256_PATTERN = Regex("[a-f0-9]{64}")
private val SECRET_PATTERN = Regex("[A-Za-z0-9_-]{32,512}")

internal data class PendingEnrollment(
    val requestId: String,
    val hostDeviceId: String,
    val userId: String,
    val ownerDeviceId: String,
    val ownerUserId: String,
    val sessionId: String? = null,
    val state: String = AWAITING_RECEIPT,
    val receipt: String? = null,
) {
    init {
        requestId.validatedUuid()
        hostDeviceId.validatedUuid()
        userId.validatedUuid()
        ownerDeviceId.validatedUuid()
        ownerUserId.validatedUuid()
        sessionId?.validatedEnrollmentSessionId()
        require(state in STATES) { "Invalid enrollment request state" }
        require(receipt == null || receipt.length <= MAX_PENDING_ENROLLMENT_BYTES) {
            "Enrollment receipt is too large"
        }
    }

    fun json(): String =
        JSONObject()
            .put("version", 1)
            .put("request_id", requestId)
            .put("host_device_id", hostDeviceId)
            .put("user_id", userId)
            .put("owner_device_id", ownerDeviceId)
            .put("owner_user_id", ownerUserId)
            .put("session_id", sessionId)
            .put("state", state)
            .put("receipt", receipt)
            .toString()

    fun withReceipt(value: JSONObject): PendingEnrollment {
        require(value.getString("request_id") == requestId) {
            "Enrollment receipt does not match request"
        }
        val receiptSessionId = value.getString("session_id").validatedEnrollmentSessionId()
        require(sessionId == null || sessionId == receiptSessionId) {
            "Enrollment receipt does not match session"
        }
        val receiptState = value.getString("state")
        require(receiptState in SERVER_STATES) { "Invalid enrollment receipt state" }
        return copy(
            sessionId = receiptSessionId,
            state = receiptState,
            receipt = value.toString(),
        )
    }

    fun withAcceptance(value: JSONObject): PendingEnrollment {
        require(value.getString("request_id") == requestId) {
            "Enrollment acceptance does not match request"
        }
        val acceptedSessionId = value.getString("session_id").validatedEnrollmentSessionId()
        require(sessionId == null || sessionId == acceptedSessionId) {
            "Enrollment acceptance does not match session"
        }
        require(
            value.getString("status_url") ==
                "/api/enroll/requests/${DeviceApi.encoded(requestId)}"
        ) { "Enrollment status URL does not match request" }
        return copy(
            sessionId = acceptedSessionId,
            state = "accepted",
            receipt = value.toString(),
        )
    }

    fun belongsTo(pairing: Pairing): Boolean =
        hostDeviceId == pairing.hostDeviceId &&
            ownerDeviceId == pairing.identity.deviceId &&
            ownerUserId == pairing.identity.user.id

    fun sameRequest(other: PendingEnrollment): Boolean =
        hostDeviceId == other.hostDeviceId &&
            ownerDeviceId == other.ownerDeviceId &&
            ownerUserId == other.ownerUserId &&
            requestId == other.requestId

    val unresolved: Boolean
        get() = state in setOf(AWAITING_RECEIPT, UNKNOWN, "accepted", "active")

    companion object {
        const val AWAITING_RECEIPT = "awaiting_receipt"
        const val UNKNOWN = "unknown"
        private val SERVER_STATES =
            setOf("accepted", "active", "completed", "failed", "cancelled", "interrupted")
        private val STATES = SERVER_STATES + setOf(AWAITING_RECEIPT, UNKNOWN)

        fun parse(raw: String): PendingEnrollment {
            require(raw.length <= MAX_PENDING_ENROLLMENT_BYTES) {
                "Invalid pending enrollment"
            }
            val json = JSONObject(raw)
            require(json.getInt("version") == 1) { "Unsupported pending enrollment" }
            return PendingEnrollment(
                requestId = json.getString("request_id"),
                hostDeviceId = json.getString("host_device_id"),
                userId = json.getString("user_id"),
                ownerDeviceId = json.getString("owner_device_id"),
                ownerUserId = json.getString("owner_user_id"),
                sessionId = json.optString("session_id").takeIf(String::isNotEmpty),
                state = json.getString("state"),
                receipt = json.optString("receipt").takeIf(String::isNotEmpty),
            )
        }
    }
}

data class Pairing(
    val hostDeviceId: String,
    val certificateSha256: String,
    val deviceToken: String,
    val identity: SessionIdentity,
) {
    fun json(): String =
        JSONObject()
            .put("version", STORAGE_VERSION)
            .put("host_device_id", hostDeviceId)
            .put("tls_certificate_sha256", certificateSha256)
            .put("device_token", deviceToken)
            .put("device_id", identity.deviceId)
            .put(
                "user",
                JSONObject()
                    .put("id", identity.user.id)
                    .put("name", identity.user.name)
                    .put("is_admin", identity.user.isAdmin)
                    .put("is_owner", identity.user.isOwner),
            )
            .toString()

    companion object {
        fun parse(raw: String): Pairing {
            require(raw.length <= MAX_SAVED_PAIRING_BYTES) { "Invalid saved pairing" }
            val json = JSONObject(raw)
            if (json.optInt("version", -1) != STORAGE_VERSION) {
                throw PairingUpgradeRequiredException()
            }
            val userJson = json.getJSONObject("user")
            val identity =
                SessionIdentity(
                    user =
                        SessionUser(
                            id = userJson.getString("id").validatedUuid(),
                            name = userJson.getString("name").validatedName(),
                            isAdmin = userJson.getBoolean("is_admin"),
                            isOwner = userJson.optBoolean("is_owner"),
                        ),
                    deviceId = json.getString("device_id").validatedUuid(),
                )
            val token = json.getString("device_token")
            require(token.matches(SECRET_PATTERN)) { "Invalid device token" }
            val certificateSha256 = json.getString("tls_certificate_sha256")
            require(certificateSha256.matches(SHA256_PATTERN)) {
                "Invalid TLS certificate fingerprint"
            }
            return Pairing(
                hostDeviceId = json.getString("host_device_id").validatedUuid(),
                certificateSha256 = certificateSha256,
                deviceToken = token,
                identity = identity,
            )
        }
    }
}

class PairingStore(private val context: Context) {
    private val preferences = context.getSharedPreferences("pairing", Context.MODE_PRIVATE)
    private val mutationLock = Any()
    private var writeScope = 0L
    private var enrollmentJournalScope = 0L

    fun beginWriteScope(): Long = synchronized(mutationLock) { ++writeScope }

    fun isWriteScopeCurrent(expectedWriteScope: Long): Boolean =
        synchronized(mutationLock) { writeScope == expectedWriteScope }

    fun enrollmentJournalScope(): Long = synchronized(mutationLock) { enrollmentJournalScope }

    internal fun loadPairings(): StoredPairings =
        synchronized(mutationLock) {
            StoredPairings(
                active = decrypted(KEY_IV, KEY_DATA),
                pending = decrypted(KEY_PENDING_IV, KEY_PENDING_DATA),
                pendingCommissioning = decryptedText(
                    KEY_COMMISSIONING_IV,
                    KEY_COMMISSIONING_DATA,
                )?.let(PendingCommissioning::parse),
                pendingTransfer = decryptedText(KEY_TRANSFER_IV, KEY_TRANSFER_DATA)
                    ?.let(PendingTransfer::parse),
                pendingEnrollments = decryptedText(
                    KEY_ENROLLMENT_IV,
                    KEY_ENROLLMENT_DATA,
                )?.let(::parseEnrollmentJournal).orEmpty(),
                enrollmentJournalScope = enrollmentJournalScope,
                exportMaterial = decryptedText(KEY_EXPORT_IV, KEY_EXPORT_DATA)?.let {
                    val json = JSONObject(it)
                    ExportMaterial(json.getString("file_name"), json.getString("content"))
                },
                writeScope = writeScope,
            )
        }

    fun save(pairing: Pairing, expectedWriteScope: Long): Boolean =
        synchronized(mutationLock) {
            if (writeScope != expectedWriteScope) return@synchronized false
            val encrypted = encrypted(pairing)
            val saved =
                preferences
                    .edit()
                    .putString(KEY_IV, encrypted.iv)
                    .putString(KEY_DATA, encrypted.data)
                    .commit()
            check(saved) { context.getString(R.string.core_pairing_save_failed) }
            true
        }

    fun savePending(pairing: Pairing, expectedWriteScope: Long): Boolean =
        synchronized(mutationLock) {
            if (writeScope != expectedWriteScope) return@synchronized false
            val encrypted = encrypted(pairing)
            val saved =
                preferences
                    .edit()
                    .putString(KEY_PENDING_IV, encrypted.iv)
                    .putString(KEY_PENDING_DATA, encrypted.data)
                    .commit()
            check(saved) { context.getString(R.string.core_pairing_save_failed) }
            true
        }

    fun savePendingCommissioning(
        pending: PendingCommissioning,
        expectedWriteScope: Long,
    ): Boolean =
        synchronized(mutationLock) {
            if (writeScope != expectedWriteScope) return@synchronized false
            val encrypted = encrypted(pending.json())
            val saved =
                preferences.edit()
                    .putString(KEY_COMMISSIONING_IV, encrypted.iv)
                    .putString(KEY_COMMISSIONING_DATA, encrypted.data)
                    .commit()
            check(saved) { context.getString(R.string.core_pairing_save_failed) }
            true
        }

    fun completeCommissioning(
        pairing: Pairing,
        pending: PendingCommissioning,
        exportMaterial: ExportMaterial?,
        expectedWriteScope: Long,
    ): Boolean =
        synchronized(mutationLock) {
            if (writeScope != expectedWriteScope) return@synchronized false
            val stored =
                decryptedText(KEY_COMMISSIONING_IV, KEY_COMMISSIONING_DATA)
                    ?.let(PendingCommissioning::parse)
            check(stored == pending) { "Pending commissioning request does not match" }
            val encryptedPairing = encrypted(pairing.json())
            val editor =
                preferences.edit()
                    .putString(KEY_IV, encryptedPairing.iv)
                    .putString(KEY_DATA, encryptedPairing.data)
                    .remove(KEY_PENDING_IV)
                    .remove(KEY_PENDING_DATA)
                    .remove(KEY_COMMISSIONING_IV)
                    .remove(KEY_COMMISSIONING_DATA)
            if (exportMaterial == null) {
                editor.remove(KEY_EXPORT_IV).remove(KEY_EXPORT_DATA)
            } else {
                val raw =
                    JSONObject()
                        .put("file_name", exportMaterial.fileName)
                        .put("content", exportMaterial.content)
                        .toString()
                val encryptedExport = encrypted(raw)
                editor.putString(KEY_EXPORT_IV, encryptedExport.iv)
                    .putString(KEY_EXPORT_DATA, encryptedExport.data)
            }
            check(editor.commit()) { context.getString(R.string.core_pairing_save_failed) }
            true
        }

    fun discardPendingCommissioning(expectedWriteScope: Long): Boolean =
        synchronized(mutationLock) {
            if (writeScope != expectedWriteScope) return@synchronized false
            check(
                preferences.edit()
                    .remove(KEY_COMMISSIONING_IV)
                    .remove(KEY_COMMISSIONING_DATA)
                    .commit()
            ) { context.getString(R.string.core_pairing_clear_failed) }
            true
        }

    fun saveExportMaterial(material: ExportMaterial, expectedWriteScope: Long): Boolean =
        synchronized(mutationLock) {
            if (writeScope != expectedWriteScope) return@synchronized false
            val raw =
                JSONObject()
                    .put("file_name", material.fileName)
                    .put("content", material.content)
                    .toString()
            val encrypted = encrypted(raw)
            check(
                preferences.edit()
                    .putString(KEY_EXPORT_IV, encrypted.iv)
                    .putString(KEY_EXPORT_DATA, encrypted.data)
                    .commit()
            ) { context.getString(R.string.core_pairing_save_failed) }
            true
        }

    fun clearExportMaterial(expectedWriteScope: Long): Boolean =
        synchronized(mutationLock) {
            if (writeScope != expectedWriteScope) return@synchronized false
            check(
                preferences.edit().remove(KEY_EXPORT_IV).remove(KEY_EXPORT_DATA).commit()
            ) { context.getString(R.string.core_pairing_clear_failed) }
            true
        }

    fun discardPendingTransfer(expectedWriteScope: Long): Boolean =
        synchronized(mutationLock) {
            if (writeScope != expectedWriteScope) return@synchronized false
            check(
                preferences.edit().remove(KEY_TRANSFER_IV).remove(KEY_TRANSFER_DATA).commit()
            ) { context.getString(R.string.core_pairing_clear_failed) }
            true
        }

    internal fun savePendingEnrollment(
        pending: PendingEnrollment,
        expectedJournalScope: Long,
    ): Boolean =
        synchronized(mutationLock) {
            if (enrollmentJournalScope != expectedJournalScope) return@synchronized false
            val records =
                parseEnrollmentJournalOrEmpty()
                    .filterNot(pending::sameRequest)
                    .plus(pending)
            require(records.size <= MAX_ENROLLMENT_JOURNAL_RECORDS) {
                "Too many pending enrollment requests"
            }
            val encrypted = encrypted(enrollmentJournalJson(records))
            check(
                preferences.edit()
                    .putString(KEY_ENROLLMENT_IV, encrypted.iv)
                    .putString(KEY_ENROLLMENT_DATA, encrypted.data)
                    .commit()
            ) { context.getString(R.string.core_pairing_save_failed) }
            true
        }

    internal fun clearPendingEnrollment(
        pending: PendingEnrollment,
        expectedJournalScope: Long,
    ): Boolean =
        synchronized(mutationLock) {
            if (enrollmentJournalScope != expectedJournalScope) return@synchronized false
            val records = parseEnrollmentJournalOrEmpty()
            val remaining = records.filterNot(pending::sameRequest)
            if (remaining.size == records.size) return@synchronized false
            val editor = preferences.edit()
            if (remaining.isEmpty()) {
                editor.remove(KEY_ENROLLMENT_IV).remove(KEY_ENROLLMENT_DATA)
            } else {
                val encrypted = encrypted(enrollmentJournalJson(remaining))
                editor.putString(KEY_ENROLLMENT_IV, encrypted.iv)
                    .putString(KEY_ENROLLMENT_DATA, encrypted.data)
            }
            check(editor.commit()) { context.getString(R.string.core_pairing_clear_failed) }
            true
        }

    private fun parseEnrollmentJournalOrEmpty(): List<PendingEnrollment> =
        decryptedText(KEY_ENROLLMENT_IV, KEY_ENROLLMENT_DATA)
            ?.let(::parseEnrollmentJournal)
            .orEmpty()

    suspend fun savePendingRecoverably(
        pairing: Pairing,
        expectedWriteScope: Long,
    ): Boolean =
        withContext(Dispatchers.IO) {
            savePending(pairing, expectedWriteScope) &&
                isWriteScopeCurrent(expectedWriteScope)
        }

    fun promotePending(pairing: Pairing, expectedWriteScope: Long): Boolean =
        synchronized(mutationLock) {
            if (writeScope != expectedWriteScope) return@synchronized false
            val pending = checkNotNull(decrypted(KEY_PENDING_IV, KEY_PENDING_DATA)) {
                "Pending pairing is missing"
            }
            check(pending.sameCredential(pairing)) { "Pending pairing does not match" }
            val encrypted = encrypted(pairing)
            val saved =
                preferences
                    .edit()
                    .putString(KEY_IV, encrypted.iv)
                    .putString(KEY_DATA, encrypted.data)
                    .remove(KEY_PENDING_IV)
                    .remove(KEY_PENDING_DATA)
                    .commit()
            check(saved) { context.getString(R.string.core_pairing_save_failed) }
            true
        }

    private fun encrypted(pairing: Pairing): EncryptedPairing = encrypted(pairing.json())

    private fun encrypted(raw: String): EncryptedPairing {
        val cipher = Cipher.getInstance(CIPHER_TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secret())
        val plaintext = raw.toByteArray(Charsets.UTF_8)
        val encrypted = try {
            cipher.doFinal(plaintext)
        } finally {
            plaintext.fill(0)
        }
        return EncryptedPairing(
            iv = Base64.encodeToString(cipher.iv, Base64.NO_WRAP),
            data = Base64.encodeToString(encrypted, Base64.NO_WRAP),
        )
    }

    private fun decrypted(ivKey: String, dataKey: String): Pairing? =
        decryptedText(ivKey, dataKey)?.let(Pairing::parse)

    private fun decryptedText(ivKey: String, dataKey: String): String? {
        val data = preferences.getString(dataKey, null) ?: return null
        val iv = preferences.getString(ivKey, null) ?: error("Pairing storage is incomplete")
        val cipher = Cipher.getInstance(CIPHER_TRANSFORMATION)
        cipher.init(
            Cipher.DECRYPT_MODE,
            secret(),
            GCMParameterSpec(GCM_TAG_BITS, Base64.decode(iv, Base64.NO_WRAP)),
        )
        val plaintext = cipher.doFinal(Base64.decode(data, Base64.NO_WRAP))
        return try {
            String(plaintext, Charsets.UTF_8)
        } finally {
            plaintext.fill(0)
        }
    }

    fun clear() {
        synchronized(mutationLock) {
            writeScope++
            enrollmentJournalScope++
            check(preferences.edit().clear().commit()) {
                context.getString(R.string.core_pairing_clear_failed)
            }
        }
    }

    private fun secret(): SecretKey {
        val store = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        val existing = store.getKey(KEYSTORE_ALIAS, null)
        if (existing is SecretKey) return existing
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                    KEYSTORE_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build()
        )
        return generator.generateKey()
    }

    companion object {
        private const val KEY_IV = "iv"
        private const val KEY_DATA = "data"
        private const val KEY_PENDING_IV = "pending_iv"
        private const val KEY_PENDING_DATA = "pending_data"
        private const val KEY_COMMISSIONING_IV = "commissioning_iv"
        private const val KEY_COMMISSIONING_DATA = "commissioning_data"
        private const val KEY_EXPORT_IV = "export_iv"
        private const val KEY_EXPORT_DATA = "export_data"
        private const val KEY_TRANSFER_IV = "transfer_iv"
        private const val KEY_TRANSFER_DATA = "transfer_data"
        private const val KEY_ENROLLMENT_IV = "enrollment_iv"
        private const val KEY_ENROLLMENT_DATA = "enrollment_data"
        private const val KEYSTORE_ALIAS = "bass-pairing"
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val CIPHER_TRANSFORMATION = "AES/GCM/NoPadding"
        private const val GCM_TAG_BITS = 128
    }
}

private data class EncryptedPairing(val iv: String, val data: String)

internal data class StoredPairings(
    val active: Pairing?,
    val pending: Pairing?,
    val pendingCommissioning: PendingCommissioning?,
    val pendingTransfer: PendingTransfer?,
    val pendingEnrollments: List<PendingEnrollment>,
    val enrollmentJournalScope: Long,
    val exportMaterial: ExportMaterial?,
    val writeScope: Long,
)

private fun enrollmentJournalJson(records: List<PendingEnrollment>): String {
    val raw = JSONObject()
        .put("version", 1)
        .put("records", org.json.JSONArray(records.map { JSONObject(it.json()) }))
        .toString()
    require(raw.length <= MAX_ENROLLMENT_JOURNAL_BYTES) {
        "Enrollment journal is too large"
    }
    return raw
}

private fun parseEnrollmentJournal(raw: String): List<PendingEnrollment> {
    require(raw.length <= MAX_ENROLLMENT_JOURNAL_BYTES) { "Invalid enrollment journal" }
    val json = JSONObject(raw)
    require(json.getInt("version") == 1) { "Unsupported enrollment journal" }
    val records = json.getJSONArray("records")
    require(records.length() <= MAX_ENROLLMENT_JOURNAL_RECORDS) {
        "Too many pending enrollment requests"
    }
    return (0 until records.length()).map { index ->
        PendingEnrollment.parse(records.getJSONObject(index).toString())
    }
}

private fun Pairing.sameCredential(other: Pairing): Boolean =
    hostDeviceId == other.hostDeviceId &&
        certificateSha256 == other.certificateSha256 &&
        deviceToken == other.deviceToken &&
        identity.deviceId == other.identity.deviceId &&
        identity.user.id == other.identity.user.id

internal fun String.validatedUuid(): String {
    require(UUID.fromString(this).toString() == this) { "Invalid identifier" }
    return this
}

private fun String.validatedEnrollmentSessionId(): String {
    require(startsWith("enroll_")) { "Invalid enrollment session identifier" }
    removePrefix("enroll_").validatedUuid()
    return this
}

internal fun String.validatedName(): String {
    val clean = trim()
    require(clean.isNotEmpty() && clean.length <= 128) { "Invalid account name" }
    return clean
}
