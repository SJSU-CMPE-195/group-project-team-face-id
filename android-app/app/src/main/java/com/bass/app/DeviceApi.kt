package com.bass.app

import java.net.URL
import java.net.URLEncoder
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.UUID
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

class ApiException(
    val statusCode: Int,
    val errorCode: String?,
    message: String,
) : Exception(message)
class TlsIdentityException(cause: Throwable) :
    Exception("Secure device identity could not be verified.", cause)

class DeviceApi(
    val baseUrl: String,
    certificateSha256: String,
    private val deviceToken: String? = null,
    private val connectTimeoutMillis: Int = 5000,
    private val readTimeoutMillis: Int = 12000,
) {
    private val certificatePin = certificateSha256.hexBytes()
    private val sslSocketFactory =
        SSLContext.getInstance("TLS").apply {
            init(null, arrayOf<TrustManager>(PinnedTrustManager(certificatePin)), SecureRandom())
        }.socketFactory
    private val hostnameVerifier = HostnameVerifier { _, session ->
        val leaf = session.peerCertificates.firstOrNull() as? X509Certificate
        leaf != null && leaf.matches(certificatePin)
    }

    init {
        require(URL(baseUrl).protocol == "https") { "BASS requires HTTPS" }
        require(certificateSha256.matches(Regex("[a-f0-9]{64}"))) {
            "Invalid TLS certificate fingerprint"
        }
        require(deviceToken == null || deviceToken.matches(Regex("[A-Za-z0-9_-]{32,512}"))) {
            "Invalid device token"
        }
        require(connectTimeoutMillis > 0 && readTimeoutMillis > 0) { "Timeouts must be positive" }
    }

    suspend fun json(
        path: String,
        method: String = "GET",
        body: JSONObject? = null,
        operationGrant: String? = null,
    ): JSONObject = JSONObject(request(path, method, body, operationGrant))

    suspend fun array(path: String): JSONArray = JSONArray(request(path))

    suspend fun publicJson(path: String): JSONObject =
        JSONObject(exchange(path, "GET", "application/json", null, null, null))

    suspend fun publicPost(path: String, body: JSONObject): JSONObject =
        JSONObject(
            exchange(
                path,
                "POST",
                "application/json",
                body.toString().toByteArray(Charsets.UTF_8),
                null,
                null,
            )
        )

    suspend fun onboardingJson(
        path: String,
        onboardingKey: String,
        body: JSONObject,
    ): JSONObject {
        require(onboardingKey.matches(Regex("[A-Za-z0-9_-]{32,512}"))) {
            "Invalid onboarding key"
        }
        return JSONObject(
            exchange(
                path,
                "POST",
                "application/json",
                body.toString().toByteArray(Charsets.UTF_8),
                "Onboarding $onboardingKey",
                null,
            )
        )
    }

    suspend fun sample(kind: String, sessionId: String, jpeg: ByteArray): JSONObject {
        val boundary = "bass-${UUID.randomUUID()}"
        val head =
            "--$boundary\r\nContent-Disposition: form-data; name=\"session_id\"\r\n\r\n$sessionId\r\n--$boundary\r\nContent-Disposition: form-data; name=\"image\"; filename=\"frame.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n"
                .toByteArray()
        val tail = "\r\n--$boundary--\r\n".toByteArray()
        return JSONObject(
            exchange(
                "/api/$kind/sample",
                "POST",
                "multipart/form-data; boundary=$boundary",
                head + jpeg + tail,
                bearerAuthorization(),
                null,
            )
        )
    }

    private suspend fun request(
        path: String,
        method: String = "GET",
        body: JSONObject? = null,
        operationGrant: String? = null,
    ): String =
        exchange(
            path,
            method,
            "application/json",
            body?.toString()?.toByteArray(Charsets.UTF_8),
            bearerAuthorization(),
            operationGrant,
        )

    private fun bearerAuthorization(): String {
        val token = checkNotNull(deviceToken) { "Authenticated API requires a device token" }
        return "Bearer $token"
    }

    private suspend fun exchange(
        path: String,
        method: String,
        contentType: String,
        bytes: ByteArray?,
        authorization: String?,
        operationGrant: String?,
    ): String =
        withContext(Dispatchers.IO) {
            require(path.startsWith("/")) { "API path must be absolute" }
            require(operationGrant == null || operationGrant.matches(Regex("[A-Za-z0-9_-]{32,512}"))) {
                "Invalid operation grant"
            }
            val connection = URL(baseUrl + path).openConnection() as? HttpsURLConnection
                ?: error("BASS requires HTTPS")
            try {
                connection.sslSocketFactory = sslSocketFactory
                connection.hostnameVerifier = hostnameVerifier
                connection.instanceFollowRedirects = false
                connection.connectTimeout = connectTimeoutMillis
                connection.readTimeout = readTimeoutMillis
                connection.requestMethod = method
                authorization?.let { connection.setRequestProperty("Authorization", it) }
                operationGrant?.let {
                    connection.setRequestProperty("X-BASS-Operation-Grant", it)
                }
                connection.setRequestProperty("Accept", "application/json")
                connection.setRequestProperty("Cache-Control", "no-store")
                if (bytes != null) {
                    connection.doOutput = true
                    connection.setRequestProperty("Content-Type", contentType)
                    connection.setFixedLengthStreamingMode(bytes.size)
                    connection.outputStream.use { it.write(bytes) }
                }
                val code = connection.responseCode
                val input = if (code in 200..299) connection.inputStream else connection.errorStream
                val raw = input?.bufferedReader()?.use { it.readText() }.orEmpty()
                if (code !in 200..299) {
                    val errorBody = runCatching { JSONObject(raw) }.getOrNull()
                    val errorCode = errorBody?.optString("code")?.takeIf { it.isNotBlank() }
                    val detail = errorBody?.optString("error")?.takeIf { it.isNotBlank() }
                    throw ApiException(code, errorCode, detail ?: "HTTP $code")
                }
                raw
            } catch (error: Exception) {
                if (error.hasPinnedCertificateFailure()) throw TlsIdentityException(error)
                throw error
            } finally {
                connection.disconnect()
            }
        }

    companion object {
        fun encoded(value: String): String = URLEncoder.encode(value, "UTF-8")
    }
}

private class PinnedCertificateException(message: String, cause: Throwable? = null) :
    CertificateException(message, cause)

private class PinnedTrustManager(private val pin: ByteArray) : X509TrustManager {
    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        throw PinnedCertificateException("Client certificates are not accepted")
    }

    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        val leaf = chain?.firstOrNull()
            ?: throw PinnedCertificateException("Server certificate is missing")
        try {
            leaf.checkValidity()
            leaf.verify(leaf.publicKey)
        } catch (error: Exception) {
            throw PinnedCertificateException("Server certificate is invalid", error)
        }
        if (leaf.basicConstraints != -1) {
            throw PinnedCertificateException("Server certificate must not be a CA")
        }
        if (!leaf.matches(pin)) {
            throw PinnedCertificateException("Server certificate does not match pairing")
        }
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
}

private fun X509Certificate.matches(pin: ByteArray): Boolean =
    MessageDigest.isEqual(MessageDigest.getInstance("SHA-256").digest(encoded), pin)

private fun String.hexBytes(): ByteArray =
    chunked(2).map { it.toInt(16).toByte() }.toByteArray()

private fun Throwable.hasPinnedCertificateFailure(): Boolean =
    generateSequence(this) { it.cause }.any { it is PinnedCertificateException }
