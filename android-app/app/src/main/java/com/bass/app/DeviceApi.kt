package com.bass.app

import java.io.Closeable
import java.io.EOFException
import java.net.InetAddress
import java.net.Proxy
import java.net.Socket
import java.net.URL
import java.net.URLEncoder
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory
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
    val retryAfterSeconds: Long? = null,
) : Exception(message)

class TlsIdentityException(cause: Throwable) :
    Exception("Secure device identity could not be verified.", cause)

class DeviceApi private constructor(
    val baseUrl: String,
    private val lifecycle: DeviceApiLifecycle,
    private val certificateSha256: String,
    private val deviceToken: String?,
    private val connectTimeoutMillis: Int,
    private val readTimeoutMillis: Int,
) : Closeable {
    constructor(
        baseUrl: String,
        certificateSha256: String,
        deviceToken: String? = null,
        connectTimeoutMillis: Int = DEFAULT_CONNECT_TIMEOUT_MILLIS,
        readTimeoutMillis: Int = DEFAULT_READ_TIMEOUT_MILLIS,
    ) : this(
        baseUrl,
        DeviceApiLifecycle(null),
        certificateSha256,
        deviceToken,
        connectTimeoutMillis,
        readTimeoutMillis,
    )

    constructor(
        transport: BleTransport,
        certificateSha256: String,
        deviceToken: String? = null,
        connectTimeoutMillis: Int = BLE_CONNECT_TIMEOUT_MILLIS,
        readTimeoutMillis: Int = DEFAULT_READ_TIMEOUT_MILLIS,
    ) : this(
        transport.endpointLabel,
        DeviceApiLifecycle(transport),
        certificateSha256,
        deviceToken,
        connectTimeoutMillis,
        readTimeoutMillis,
    )

    private val certificatePin = certificateSha256.hexBytes()
    private val transport: BleTransport?
        get() = lifecycle.transport
    private val sslSocketFactory =
        MinimumTlsSocketFactory(
            SSLContext.getInstance("TLS").apply {
                init(
                    null,
                    arrayOf<TrustManager>(PinnedTrustManager(certificatePin)),
                    SecureRandom(),
                )
            }.socketFactory,
        )
    private val hostnameVerifier = HostnameVerifier { _, session ->
        val leaf = session.peerCertificates.firstOrNull() as? X509Certificate
        leaf != null && leaf.matches(certificatePin)
    }
    private val scheduler: ApiScheduler
        get() = lifecycle.scheduler

    val generation: Long
        get() = transport?.generation ?: LEGACY_GENERATION

    val isReusable: Boolean
        get() = lifecycle.isReusable

    init {
        if (transport == null) require(URL(baseUrl).protocol == "https") { "BASS requires HTTPS" }
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
        expectedStatus: Int? = null,
    ): JSONObject =
        scheduler.submit {
            JSONObject(requestDirect(path, method, body, operationGrant, expectedStatus))
        }

    suspend fun array(path: String): JSONArray =
        scheduler.submit { JSONArray(requestDirect(path)) }

    suspend fun publicJson(path: String): JSONObject =
        scheduler.submit {
            JSONObject(
                exchangeDirect(path, "GET", "application/json", null, null, null, null),
            )
        }

    suspend fun publicPost(
        path: String,
        body: JSONObject,
        expectedStatus: Int? = null,
    ): JSONObject =
        scheduler.submit {
            JSONObject(
                exchangeDirect(
                    path,
                    "POST",
                    "application/json",
                    body.toString().toByteArray(Charsets.UTF_8),
                    null,
                    null,
                    expectedStatus,
                ),
            )
        }

    suspend fun onboardingJson(
        path: String,
        onboardingKey: String,
        body: JSONObject,
    ): JSONObject {
        require(onboardingKey.matches(Regex("[A-Za-z0-9_-]{32,512}"))) {
            "Invalid onboarding key"
        }
        return scheduler.submit {
            JSONObject(
                exchangeDirect(
                    path,
                    "POST",
                    "application/json",
                    body.toString().toByteArray(Charsets.UTF_8),
                    "Onboarding $onboardingKey",
                    null,
                    null,
                ),
            )
        }
    }

    fun withToken(deviceToken: String): DeviceApi =
        DeviceApi(
            baseUrl,
            lifecycle,
            certificateSha256,
            deviceToken,
            connectTimeoutMillis,
            readTimeoutMillis,
        )

    suspend fun <T> exclusive(block: suspend ExclusiveDeviceApi.() -> T): T =
        scheduler.submit { ExclusiveDeviceApi(this).block() }

    suspend fun <T> cancelFirst(block: suspend ExclusiveDeviceApi.() -> T): T =
        scheduler.submit(cancelPriority = true) { ExclusiveDeviceApi(this).block() }

    override fun close() {
        lifecycle.close()
    }

    internal suspend fun jsonDirect(
        path: String,
        method: String = "GET",
        body: JSONObject? = null,
        operationGrant: String? = null,
        expectedStatus: Int? = null,
    ): JSONObject = JSONObject(requestDirect(path, method, body, operationGrant, expectedStatus))

    internal suspend fun arrayDirect(path: String): JSONArray = JSONArray(requestDirect(path))

    private suspend fun requestDirect(
        path: String,
        method: String = "GET",
        body: JSONObject? = null,
        operationGrant: String? = null,
        expectedStatus: Int? = null,
    ): String =
        exchangeDirect(
            path,
            method,
            "application/json",
            body?.toString()?.toByteArray(Charsets.UTF_8),
            bearerAuthorization(),
            operationGrant,
            expectedStatus,
        )

    private fun bearerAuthorization(): String {
        val token = checkNotNull(deviceToken) { "Authenticated API requires a device token" }
        return "Bearer $token"
    }

    private suspend fun exchangeDirect(
        path: String,
        method: String,
        contentType: String,
        bytes: ByteArray?,
        authorization: String?,
        operationGrant: String?,
        expectedStatus: Int?,
    ): String {
        require(path.startsWith("/")) { "API path must be absolute" }
        require(operationGrant == null || operationGrant.matches(Regex("[A-Za-z0-9_-]{32,512}"))) {
            "Invalid operation grant"
        }
        val activeTransport = transport
        val response =
            try {
                if (activeTransport != null) {
                    activeTransport.exchange {
                        executeHttp(
                            URL(this.baseUrl + path),
                            method,
                            contentType,
                            bytes,
                            authorization,
                            operationGrant,
                            ::commitResponse,
                        )
                    }
                } else {
                    executeHttp(
                        URL(baseUrl + path),
                        method,
                        contentType,
                        bytes,
                        authorization,
                        operationGrant,
                    ) { it }
                }
            } catch (error: Exception) {
                if (error.hasPinnedCertificateFailure()) throw TlsIdentityException(error)
                throw error
            }
        val accepted = expectedStatus?.let { response.statusCode == it } ?: (response.statusCode in 200..299)
        if (!accepted) {
            val errorBody = runCatching { JSONObject(response.body) }.getOrNull()
            val errorCode = errorBody?.optString("code")?.takeIf { it.isNotBlank() }
            val detail = errorBody?.optString("error")?.takeIf { it.isNotBlank() }
            throw ApiException(
                response.statusCode,
                errorCode,
                detail ?: "HTTP ${response.statusCode}",
                response.retryAfterSeconds,
            )
        }
        return response.body
    }

    private suspend fun executeHttp(
        url: URL,
        method: String,
        contentType: String,
        bytes: ByteArray?,
        authorization: String?,
        operationGrant: String?,
        commitResponse: (RawHttpResponse) -> RawHttpResponse,
    ): RawHttpResponse =
        withContext(Dispatchers.IO) {
            val connection = url.openConnection(Proxy.NO_PROXY) as? HttpsURLConnection
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
                connection.setRequestProperty("Accept-Encoding", "identity")
                connection.setRequestProperty("Cache-Control", "no-store")
                connection.setRequestProperty("Connection", "close")
                if (bytes != null) {
                    connection.doOutput = true
                    connection.setRequestProperty("Content-Type", contentType)
                    connection.setFixedLengthStreamingMode(bytes.size)
                    connection.outputStream.use { it.write(bytes) }
                }
                val statusCode = connection.responseCode
                val responseBytes = readExactlyFramedBody(connection, statusCode)
                commitResponse(
                    RawHttpResponse(
                        statusCode,
                        responseBytes.toString(Charsets.UTF_8),
                        connection.getHeaderField("Retry-After")?.trim()?.toLongOrNull(),
                    ),
                )
            } finally {
                connection.disconnect()
            }
        }

    companion object {
        private const val DEFAULT_CONNECT_TIMEOUT_MILLIS = 5_000
        private const val BLE_CONNECT_TIMEOUT_MILLIS = 30_000
        private const val DEFAULT_READ_TIMEOUT_MILLIS = 60_000
        private const val MAX_JSON_RESPONSE_BYTES = 1024 * 1024
        private const val LEGACY_GENERATION = 0L

        fun encoded(value: String): String = URLEncoder.encode(value, "UTF-8")

        private fun readExactlyFramedBody(
            connection: HttpsURLConnection,
            statusCode: Int,
        ): ByteArray {
            require(connection.getHeaderField("Transfer-Encoding") == null) {
                "Chunked HTTP responses are not supported over BLE"
            }
            val contentEncoding = connection.getHeaderField("Content-Encoding")
            require(contentEncoding == null || contentEncoding.equals("identity", ignoreCase = true)) {
                "Compressed HTTP responses are not supported over BLE"
            }
            val contentLengthHeader = connection.getHeaderField("Content-Length")?.trim()
                ?: throw BleTransportException("HTTP response is missing Content-Length")
            if (!contentLengthHeader.matches(Regex("[0-9]+"))) {
                throw BleTransportException("HTTP response has invalid Content-Length")
            }
            val contentLength = contentLengthHeader.toLongOrNull()
                ?: throw BleTransportException("HTTP response has invalid Content-Length")
            if (contentLength > MAX_JSON_RESPONSE_BYTES) {
                throw BleTransportException("HTTP response exceeds the JSON size limit")
            }
            val expected = contentLength.toInt()
            if (expected == 0) return ByteArray(0)
            val input =
                if (statusCode in 200..299) connection.inputStream else connection.errorStream
                    ?: throw BleTransportException("HTTP response body is missing")
            return input.use {
                val result = ByteArray(expected)
                var offset = 0
                while (offset < expected) {
                    val count = it.read(result, offset, expected - offset)
                    if (count < 0) throw EOFException("HTTP response body was truncated")
                    if (count > 0) offset += count
                }
                result
            }
        }
    }
}

class ExclusiveDeviceApi internal constructor(private val api: DeviceApi) {
    val generation: Long
        get() = api.generation

    suspend fun json(
        path: String,
        method: String = "GET",
        body: JSONObject? = null,
        operationGrant: String? = null,
        expectedStatus: Int? = null,
    ): JSONObject = api.jsonDirect(path, method, body, operationGrant, expectedStatus)

    suspend fun array(path: String): JSONArray = api.arrayDirect(path)
}

private class DeviceApiLifecycle(val transport: BleTransport?) : Closeable {
    private val closed = AtomicBoolean(false)
    val scheduler =
        ApiScheduler(transport?.generation ?: 0L) {
            !closed.get() && (transport?.isReusable ?: true)
        }

    val isReusable: Boolean
        get() = !closed.get() && (transport?.isReusable ?: true)

    override fun close() {
        if (!closed.compareAndSet(false, true)) return
        scheduler.close()
        transport?.close()
    }
}

private data class RawHttpResponse(
    val statusCode: Int,
    val body: String,
    val retryAfterSeconds: Long?,
)

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

private class MinimumTlsSocketFactory(private val delegate: SSLSocketFactory) : SSLSocketFactory() {
    override fun getDefaultCipherSuites(): Array<String> = delegate.defaultCipherSuites

    override fun getSupportedCipherSuites(): Array<String> = delegate.supportedCipherSuites

    override fun createSocket(): Socket = configure(delegate.createSocket())

    override fun createSocket(socket: Socket, host: String, port: Int, autoClose: Boolean): Socket =
        configure(delegate.createSocket(socket, host, port, autoClose))

    override fun createSocket(host: String, port: Int): Socket =
        configure(delegate.createSocket(host, port))

    override fun createSocket(
        host: String,
        port: Int,
        localHost: InetAddress,
        localPort: Int,
    ): Socket = configure(delegate.createSocket(host, port, localHost, localPort))

    override fun createSocket(host: InetAddress, port: Int): Socket =
        configure(delegate.createSocket(host, port))

    override fun createSocket(
        address: InetAddress,
        port: Int,
        localAddress: InetAddress,
        localPort: Int,
    ): Socket = configure(delegate.createSocket(address, port, localAddress, localPort))

    private fun configure(socket: Socket): Socket {
        val sslSocket = socket as? SSLSocket ?: return socket
        val allowed = sslSocket.supportedProtocols.filter { it == "TLSv1.2" || it == "TLSv1.3" }
        if (allowed.isEmpty()) throw PinnedCertificateException("TLS 1.2 or newer is required")
        sslSocket.enabledProtocols = allowed.toTypedArray()
        return sslSocket
    }
}

private fun X509Certificate.matches(pin: ByteArray): Boolean =
    MessageDigest.isEqual(MessageDigest.getInstance("SHA-256").digest(encoded), pin)

private fun String.hexBytes(): ByteArray =
    chunked(2).map { it.toInt(16).toByte() }.toByteArray()

private fun Throwable.hasPinnedCertificateFailure(): Boolean =
    generateSequence(this) { it.cause }.any { it is PinnedCertificateException }
