package com.bass.app

import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

class ApiException(val statusCode: Int, message: String) : Exception(message)

class DeviceApi(val baseUrl: String, private val pairing: Pairing) {
    suspend fun request(path: String, method: String = "GET", body: JSONObject? = null): String =
        exchange(path, method, "application/json", body?.toString()?.toByteArray(Charsets.UTF_8))

    suspend fun json(path: String, method: String = "GET", body: JSONObject? = null): JSONObject =
        JSONObject(request(path, method, body))

    suspend fun array(path: String): JSONArray = JSONArray(request(path))

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
            )
        )
    }

    private suspend fun exchange(
        path: String,
        method: String,
        contentType: String,
        bytes: ByteArray?,
    ): String =
        withContext(Dispatchers.IO) {
            val connection = URL(baseUrl + path).openConnection() as HttpURLConnection
            try {
                connection.instanceFollowRedirects = false
                connection.connectTimeout = 5000
                connection.readTimeout = 12000
                connection.requestMethod = method
                connection.setRequestProperty("Authorization", "Bearer ${pairing.key}")
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
                    val detail = runCatching { JSONObject(raw).optString("error") }.getOrNull()
                    throw ApiException(code, detail?.takeIf { it.isNotBlank() } ?: "HTTP $code")
                }
                raw
            } finally {
                connection.disconnect()
            }
        }

    companion object {
        fun encoded(value: String): String = URLEncoder.encode(value, "UTF-8")
    }
}
