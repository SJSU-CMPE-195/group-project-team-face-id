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
import org.json.JSONObject

data class Pairing(val deviceId: String, val key: String) {
    fun json(): String =
        JSONObject().put("version", 1).put("device_id", deviceId).put("pairing_key", key).toString()

    companion object {
        fun parse(raw: String): Pairing {
            require(raw.length <= 1024) { "Invalid QR code" }
            val json = JSONObject(raw)
            require(json.getInt("version") == 1) { "Unsupported pairing version" }
            val id = json.getString("device_id")
            require(UUID.fromString(id).toString() == id.lowercase()) { "Invalid device ID" }
            val key = json.getString("pairing_key")
            require(key.matches(Regex("[a-fA-F0-9]{64}"))) { "Invalid pairing key" }
            return Pairing(id, key)
        }
    }
}

class PairingStore(private val context: Context) {
    private val preferences = context.getSharedPreferences("pairing", Context.MODE_PRIVATE)

    private fun secret(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val existing = store.getKey("bass-pairing", null)
        if (existing is SecretKey) return existing
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(
                    "bass-pairing",
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build()
        )
        return generator.generateKey()
    }

    fun save(pairing: Pairing) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, secret())
        val encrypted = cipher.doFinal(pairing.json().toByteArray(Charsets.UTF_8))
        val saved =
            preferences
                .edit()
                .putString("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
                .putString("data", Base64.encodeToString(encrypted, Base64.NO_WRAP))
                .commit()
        check(saved) { context.getString(R.string.core_pairing_save_failed) }
    }

    fun load(): Pairing? {
        val data = preferences.getString("data", null) ?: return null
        val iv = preferences.getString("iv", null) ?: error("Pairing storage is incomplete")
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.DECRYPT_MODE,
            secret(),
            GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP)),
        )
        return Pairing.parse(
            String(cipher.doFinal(Base64.decode(data, Base64.NO_WRAP)), Charsets.UTF_8)
        )
    }

    fun clear() {
        check(preferences.edit().clear().commit()) {
            context.getString(R.string.core_pairing_clear_failed)
        }
    }
}
