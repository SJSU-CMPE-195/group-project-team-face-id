package com.bass.app

import android.annotation.SuppressLint
import android.content.Context
import android.os.SystemClock
import android.provider.Settings
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.nio.ByteBuffer
import java.security.KeyStore
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.PBEKeySpec

sealed interface PinCheck {
    object Success : PinCheck

    data class Wrong(val remainingAttempts: Int) : PinCheck

    data class Locked(val remainingSeconds: Int) : PinCheck
}

class PreparedPin
private constructor(
    private val salt: ByteArray,
    private val verifier: ByteArray,
) {
    internal fun copySalt(): ByteArray = salt.copyOf()

    internal fun copyVerifier(): ByteArray = verifier.copyOf()

    companion object {
        internal fun create(salt: ByteArray, verifier: ByteArray): PreparedPin =
            PreparedPin(salt.copyOf(), verifier.copyOf())
    }
}

class PinStore(context: Context) {
    private val applicationContext = context.applicationContext
    private val preferences =
        applicationContext.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)

    fun isConfigured(): Boolean = storedRecord() != null

    fun prepare(pin: CharArray): PreparedPin {
        validatePin(pin)
        val salt = ByteArray(SALT_BYTES).also(secureRandom::nextBytes)
        val verifier = deriveVerifier(pin, salt)
        return try {
            PreparedPin.create(salt, verifier)
        } finally {
            salt.fill(0)
            verifier.fill(0)
        }
    }

    fun save(prepared: PreparedPin) {
        synchronized(processLock) {
            check(loadState() == null) { "BASS PIN is already configured" }
            val state =
                PinState(
                    salt = prepared.copySalt(),
                    verifier = prepared.copyVerifier(),
                )
            writeState(state)
        }
    }

    @SuppressLint("UseKtx")
    fun clear() {
        synchronized(processLock) {
            check(preferences.edit().clear().commit()) { "Unable to clear BASS PIN state" }
        }
    }

    fun verify(pin: CharArray): PinCheck {
        validatePin(pin)
        return synchronized(processLock) {
            val storedState = loadState() ?: error("BASS PIN is not configured")
            val reanchoredState = reanchorLockAfterBoot(storedState)
            if (reanchoredState !== storedState) writeState(reanchoredState)

            val remainingLockMillis = remainingLockMillis(reanchoredState)
            if (remainingLockMillis > 0L) {
                return@synchronized PinCheck.Locked(remainingSeconds(remainingLockMillis))
            }

            val state = reanchoredState.withExpiredLockCleared()
            val candidate = deriveVerifier(pin, state.salt)
            val matches =
                try {
                    MessageDigest.isEqual(state.verifier, candidate)
                } finally {
                    candidate.fill(0)
                }

            if (matches) {
                if (state.failedAttempts > 0 || reanchoredState.hasLock) {
                    writeState(state.copy(failedAttempts = 0))
                }
                return@synchronized PinCheck.Success
            }

            val failedAttempts = state.failedAttempts + 1
            if (failedAttempts < MAX_FAILED_ATTEMPTS) {
                writeState(state.copy(failedAttempts = failedAttempts))
                return@synchronized PinCheck.Wrong(MAX_FAILED_ATTEMPTS - failedAttempts)
            }

            val lockStartedElapsedMillis = SystemClock.elapsedRealtime()
            val lockedState =
                state.copy(
                    failedAttempts = 0,
                    lockoutUntilWallMillis = System.currentTimeMillis() + COOLDOWN_MILLIS,
                    lockStartedElapsedMillis = lockStartedElapsedMillis,
                    lockBootCount = currentBootCount(),
                )
            writeState(lockedState)
            PinCheck.Locked(COOLDOWN_SECONDS)
        }
    }

    private fun reanchorLockAfterBoot(state: PinState): PinState {
        if (!state.hasLock) return state

        val elapsedNow = SystemClock.elapsedRealtime()
        val bootCount = currentBootCount()
        val sameKnownBoot = state.lockBootCount >= 0 && state.lockBootCount == bootCount
        val sameUnknownBoot =
            state.lockBootCount < 0 && bootCount < 0 && elapsedNow >= state.lockStartedElapsedMillis
        if (sameKnownBoot || sameUnknownBoot) return state

        val wallNow = System.currentTimeMillis()
        val wallRemaining = state.lockoutUntilWallMillis - wallNow
        if (wallRemaining <= 0L) return state

        return state.copy(
            lockoutUntilWallMillis = wallNow + COOLDOWN_MILLIS,
            lockStartedElapsedMillis = elapsedNow.coerceAtLeast(1L),
            lockBootCount = bootCount,
        )
    }

    private fun remainingLockMillis(state: PinState): Long {
        if (!state.hasLock) return 0L

        val elapsedNow = SystemClock.elapsedRealtime()
        val bootCount = currentBootCount()
        val sameKnownBoot = state.lockBootCount >= 0 && state.lockBootCount == bootCount
        val sameUnknownBoot =
            state.lockBootCount < 0 && bootCount < 0 && elapsedNow >= state.lockStartedElapsedMillis
        if (sameKnownBoot || sameUnknownBoot) {
            val elapsed = elapsedNow - state.lockStartedElapsedMillis
            return (COOLDOWN_MILLIS - elapsed).coerceAtLeast(0L)
        }

        val wallRemaining = state.lockoutUntilWallMillis - System.currentTimeMillis()
        return wallRemaining.coerceIn(0L, COOLDOWN_MILLIS)
    }

    private fun currentBootCount(): Int =
        Settings.Global.getInt(
            applicationContext.contentResolver,
            Settings.Global.BOOT_COUNT,
            UNKNOWN_BOOT_COUNT,
        )

    private fun loadState(): PinState? {
        val record = storedRecord() ?: return null
        val cipher = Cipher.getInstance(CIPHER_TRANSFORMATION)
        cipher.init(
            Cipher.DECRYPT_MODE,
            secret(),
            GCMParameterSpec(GCM_TAG_BITS, record.iv),
        )
        cipher.updateAAD(ASSOCIATED_DATA)
        val plaintext = cipher.doFinal(record.data)
        return try {
            PinState.decode(plaintext)
        } finally {
            plaintext.fill(0)
        }
    }

    private fun writeState(state: PinState) {
        val plaintext = state.encode()
        val cipher = Cipher.getInstance(CIPHER_TRANSFORMATION)
        val encrypted =
            try {
                cipher.init(Cipher.ENCRYPT_MODE, secret())
                cipher.updateAAD(ASSOCIATED_DATA)
                cipher.doFinal(plaintext)
            } finally {
                plaintext.fill(0)
            }

        val saved =
            preferences
                .edit()
                .putString(KEY_IV, Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
                .putString(KEY_DATA, Base64.encodeToString(encrypted, Base64.NO_WRAP))
                .commit()
        check(saved) { "Unable to save BASS PIN state" }
    }

    private fun storedRecord(): EncryptedRecord? {
        val values = preferences.all
        if (values.isEmpty()) return null

        val encodedIv = values[KEY_IV] as? String ?: error("BASS PIN storage is incomplete")
        val encodedData = values[KEY_DATA] as? String ?: error("BASS PIN storage is incomplete")
        val iv = decodeBase64(encodedIv)
        val data = decodeBase64(encodedData)
        check(iv.size == GCM_IV_BYTES && data.size >= GCM_TAG_BYTES) {
            "BASS PIN storage is corrupt"
        }
        return EncryptedRecord(iv, data)
    }

    private fun secret(): SecretKey {
        val store = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        val existing = store.getKey(KEYSTORE_ALIAS, null)
        if (existing != null) {
            check(existing is SecretKey) { "BASS PIN key has an invalid type" }
            return existing
        }

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        val specification =
            KeyGenParameterSpec.Builder(
                    KEYSTORE_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(AES_KEY_BITS)
                .build()
        generator.init(specification)
        return generator.generateKey()
    }

    private data class EncryptedRecord(val iv: ByteArray, val data: ByteArray)

    private data class PinState(
        val salt: ByteArray,
        val verifier: ByteArray,
        val failedAttempts: Int = 0,
        val lockoutUntilWallMillis: Long = 0L,
        val lockStartedElapsedMillis: Long = 0L,
        val lockBootCount: Int = UNKNOWN_BOOT_COUNT,
    ) {
        val hasLock: Boolean
            get() = lockoutUntilWallMillis > 0L

        fun withExpiredLockCleared(): PinState {
            if (!hasLock) return this
            return copy(
                failedAttempts = 0,
                lockoutUntilWallMillis = 0L,
                lockStartedElapsedMillis = 0L,
                lockBootCount = UNKNOWN_BOOT_COUNT,
            )
        }

        fun encode(): ByteArray =
            ByteBuffer.allocate(STATE_BYTES)
                .putInt(STORAGE_VERSION)
                .put(salt)
                .put(verifier)
                .putInt(failedAttempts)
                .putLong(lockoutUntilWallMillis)
                .putLong(lockStartedElapsedMillis)
                .putInt(lockBootCount)
                .array()

        companion object {
            fun decode(encoded: ByteArray): PinState {
                check(encoded.size == STATE_BYTES) { "BASS PIN storage has an invalid size" }
                val buffer = ByteBuffer.wrap(encoded)
                check(buffer.int == STORAGE_VERSION) { "Unsupported BASS PIN storage version" }
                val salt = ByteArray(SALT_BYTES).also { buffer.get(it) }
                val verifier = ByteArray(VERIFIER_BYTES).also { buffer.get(it) }
                val failedAttempts = buffer.int
                val lockoutUntilWallMillis = buffer.long
                val lockStartedElapsedMillis = buffer.long
                val lockBootCount = buffer.int

                check(failedAttempts in 0 until MAX_FAILED_ATTEMPTS) {
                    "BASS PIN failure state is corrupt"
                }
                val hasCompleteLock =
                    lockoutUntilWallMillis > 0L &&
                        lockStartedElapsedMillis > 0L &&
                        lockBootCount >= UNKNOWN_BOOT_COUNT &&
                        failedAttempts == 0
                val hasNoLock =
                    lockoutUntilWallMillis == 0L &&
                        lockStartedElapsedMillis == 0L &&
                        lockBootCount == UNKNOWN_BOOT_COUNT
                check(hasCompleteLock || hasNoLock) { "BASS PIN cooldown state is corrupt" }

                return PinState(
                    salt = salt,
                    verifier = verifier,
                    failedAttempts = failedAttempts,
                    lockoutUntilWallMillis = lockoutUntilWallMillis,
                    lockStartedElapsedMillis = lockStartedElapsedMillis,
                    lockBootCount = lockBootCount,
                )
            }
        }
    }

    companion object {
        private const val PREFERENCES_NAME = "bass_pin"
        private const val KEY_IV = "iv"
        private const val KEY_DATA = "data"
        private const val KEYSTORE_ALIAS = "bass-pin"
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val CIPHER_TRANSFORMATION = "AES/GCM/NoPadding"
        private const val STORAGE_VERSION = 1
        private const val PIN_DIGITS = 6
        private const val PBKDF2_ITERATIONS = 600_000
        private const val SALT_BYTES = 16
        private const val VERIFIER_BYTES = 32
        private const val VERIFIER_BITS = VERIFIER_BYTES * 8
        private const val AES_KEY_BITS = 256
        private const val GCM_IV_BYTES = 12
        private const val GCM_TAG_BITS = 128
        private const val GCM_TAG_BYTES = GCM_TAG_BITS / 8
        private const val MAX_FAILED_ATTEMPTS = 5
        private const val COOLDOWN_SECONDS = 60
        private const val COOLDOWN_MILLIS = COOLDOWN_SECONDS * 1_000L
        private const val UNKNOWN_BOOT_COUNT = -1
        private const val STATE_BYTES =
            Int.SIZE_BYTES +
                SALT_BYTES +
                VERIFIER_BYTES +
                Int.SIZE_BYTES +
                Long.SIZE_BYTES +
                Long.SIZE_BYTES +
                Int.SIZE_BYTES
        private val ASSOCIATED_DATA = "bass-pin-v1".toByteArray(Charsets.UTF_8)
        private val secureRandom = SecureRandom()
        private val processLock = Any()

        private fun validatePin(pin: CharArray) {
            val isSixAsciiDigits = pin.size == PIN_DIGITS && pin.all { it in '0'..'9' }
            require(isSixAsciiDigits) { "BASS PIN must contain exactly six digits" }
        }

        private fun deriveVerifier(pin: CharArray, salt: ByteArray): ByteArray {
            val specification = PBEKeySpec(pin, salt, PBKDF2_ITERATIONS, VERIFIER_BITS)
            return try {
                SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
                    .generateSecret(specification)
                    .encoded
            } finally {
                specification.clearPassword()
            }
        }

        private fun decodeBase64(encoded: String): ByteArray =
            try {
                Base64.decode(encoded, Base64.NO_WRAP)
            } catch (error: IllegalArgumentException) {
                throw IllegalStateException("BASS PIN storage is corrupt", error)
            }

        private fun remainingSeconds(remainingMillis: Long): Int =
            ((remainingMillis + 999L) / 1_000L).coerceAtMost(COOLDOWN_SECONDS.toLong()).toInt()
    }
}
