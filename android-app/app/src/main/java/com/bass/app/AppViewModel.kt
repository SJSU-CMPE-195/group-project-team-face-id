package com.bass.app

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

class AppViewModel(application: Application) : AndroidViewModel(application) {
    private fun text(id: Int): String = getApplication<Application>().getString(id)

    private val mutableState = MutableStateFlow(BassState())
    val state = mutableState.asStateFlow()
    private val store = PairingStore(application)
    private val pinStore = PinStore(application)
    private val discovery = DeviceDiscovery(application)
    private val pinProtection =
        PinProtection(application, viewModelScope, pinStore) { prompt ->
            mutableState.update { it.copy(pinPrompt = prompt) }
        }
    private var pairingScanGeneration: Long? = null
    private var pairing: Pairing? = null
    private var api: DeviceApi? = null
    private var generation = 0L
    private var sessionGeneration = 0L
    private var connectionJob: Job? = null
    private var pollJob: Job? = null
    private var frameJob: Job? = null
    private var reconnectJob: Job? = null
    private var promptJob: Job? = null
    private var foreground = false
    private var session: ActiveSession? = null
    private var refreshing = false
    private var snapshotVersion = 0L
    private var pendingStart: PendingStart? = null

    private data class PendingStart(val deviceGeneration: Long, val sessionGeneration: Long)

    private data class ActiveSession(
        val id: String,
        val kind: String,
        val api: DeviceApi,
        val generation: Long,
        val flow: CaptureFlow,
    )

    init {
        runCatching { store.load() }
            .onSuccess { pairing = it }
            .onFailure {
                mutableState.update { current ->
                    current.copy(error = text(R.string.core_pairing_unavailable))
                }
            }
    }

    fun onForeground() {
        foreground = true
        if (pairing != null) retryConnection()
    }

    fun onBackground() {
        foreground = false
        pinProtection.cancel()
        generation++
        connectionJob?.cancel()
        reconnectJob?.cancel()
        stopPrompt()
        stopSession()
        api = null
        mutableState.update {
            it.copy(
                phase = if (pairing == null) ConnectionPhase.UNPAIRED else ConnectionPhase.OFFLINE,
                busy = false,
                unlockOwner = null,
                showForgetDeviceDialog = false,
                forgetDeviceError = null,
            )
        }
    }

    fun selectTab(tab: Tab) {
        if (tab != state.value.selectedTab) {
            pinProtection.cancel()
            stopSession(restorePrompt = true)
        }
        mutableState.update { it.copy(selectedTab = tab) }
    }

    fun beginPairing() {
        requestPin(PinPurpose.PAIR, ::beginPairingScan)
    }

    private fun beginPairingScan() {
        generation++
        connectionJob?.cancel()
        reconnectJob?.cancel()
        api = null
        stopPrompt()
        stopSession()
        pairingScanGeneration = generation
        mutableState.update {
            it.copy(
                phase = ConnectionPhase.UNPAIRED,
                captureFlow = CaptureFlow.QR,
                busy = false,
                error = null,
            )
        }
    }

    fun scanPairingQr(raw: String) = acceptPairingQr(raw)

    fun acceptPairingQr(raw: String) {
        if (!foreground || pairingScanGeneration != generation) return
        if (state.value.captureFlow != CaptureFlow.QR) return
        val parsed = runCatching {
            Pairing.parse(raw)
        }
            .getOrElse {
                mutableState.update { current ->
                    current.copy(error = text(R.string.core_invalid_qr))
                }
                return
            }
        stopSession()
        pairing = parsed
        settingsDirty = false
        api = null
        mutableState.value = BassState(phase = ConnectionPhase.DISCOVERING)
        retryConnection()
    }

    fun requestForgetDevice() {
        if (state.value.busy || state.value.showForgetDeviceDialog) return
        mutableState.update {
            it.copy(showForgetDeviceDialog = true, forgetDeviceError = null, error = null)
        }
    }

    fun cancelForgetDevice() {
        if (state.value.busy) return
        if (pairing == null) {
            mutableState.value = BassState()
            return
        }
        mutableState.update {
            it.copy(showForgetDeviceDialog = false, forgetDeviceError = null)
        }
    }

    fun forgetDevice(resetPin: Boolean) {
        if (!state.value.showForgetDeviceDialog || state.value.busy) return
        mutableState.update { it.copy(busy = true, forgetDeviceError = null, error = null) }

        pinProtection.cancel()
        generation++
        connectionJob?.cancel()
        connectionJob = null
        reconnectJob?.cancel()
        reconnectJob = null
        stopPrompt()
        stopSession()
        api = null

        val pairingCleared = runCatching { store.clear() }
        if (pairingCleared.isFailure) {
            mutableState.update {
                it.copy(
                    phase = ConnectionPhase.ERROR,
                    busy = false,
                    forgetDeviceError = text(R.string.forget_device_clear_failed),
                )
            }
            return
        }

        pairing = null
        settingsDirty = false
        if (resetPin) {
            val pinCleared = runCatching { pinStore.clear() }
            if (pinCleared.isFailure) {
                mutableState.value =
                    BassState(
                        showForgetDeviceDialog = true,
                        forgetDeviceError = text(R.string.forget_pin_clear_failed),
                        phase = ConnectionPhase.ERROR,
                    )
                return
            }
        }

        mutableState.value = BassState()
    }

    fun retryConnection() {
        val owner = pairing ?: return
        if (!foreground) return
        val configured = runCatching { pinProtection.isConfigured() }
        if (configured.isFailure) {
            mutableState.update { it.copy(error = text(R.string.pin_storage_error)) }
            return
        }
        if (!configured.getOrThrow()) {
            mutableState.update { it.copy(phase = ConnectionPhase.OFFLINE) }
            requestPin(PinPurpose.SETUP, ::retryConnection)
            return
        }
        pinProtection.cancel()
        generation++
        val epoch = generation
        connectionJob?.cancel()
        reconnectJob?.cancel()
        stopPrompt()
        stopSession()
        api = null
        mutableState.update {
            it.copy(
                phase = ConnectionPhase.DISCOVERING,
                busy = false,
                error = null,
                unlockOwner = null,
            )
        }
        connectionJob = viewModelScope.launch {
            try {
                val found = discovery.find(owner.deviceId)
                if (epoch != generation) return@launch
                mutableState.update { it.copy(phase = ConnectionPhase.CONNECTING) }
                val (client, info) = connectAddress(found, owner)
                if (epoch != generation) return@launch
                val capabilities = info.getJSONObject("capabilities")
                store.save(owner)
                api = client
                mutableState.update {
                    it.copy(
                        device =
                            DeviceInfo(
                                owner.deviceId,
                                info.optString("name", "BASS"),
                                client.baseUrl,
                            ),
                        capabilities =
                            Capabilities(
                                clientCamera = capabilities.optBoolean("client_camera"),
                                deviceCamera =
                                    capabilities.optBoolean(
                                        "device_camera",
                                        capabilities.optBoolean("pi_camera"),
                                    ),
                                simulatedActuator =
                                    capabilities.optBoolean("simulated_actuators"),
                            ),
                    )
                }
                loadSnapshot(client, epoch)
                if (epoch != generation) return@launch
                mutableState.update { it.copy(phase = ConnectionPhase.CONNECTED) }
                while (foreground && epoch == generation) {
                    delay(3000)
                    if (!state.value.busy) loadSnapshot(client, epoch)
                }
            } catch (error: TimeoutCancellationException) {
                failConnection(IllegalStateException(text(R.string.core_not_found)), epoch)
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                failConnection(error, epoch)
            }
        }
    }

    private suspend fun connectAddress(
        found: DiscoveredDevice,
        owner: Pairing,
    ): Pair<DeviceApi, JSONObject> {
        var lastError: Exception = IllegalStateException(text(R.string.core_no_address))
        for (url in found.urls) {
            val client = DeviceApi(url, owner)
            try {
                val info = client.json("/api/device-info")
                check(info.getString("device_id") == owner.deviceId) {
                    text(R.string.core_identity_mismatch)
                }
                check(info.getInt("protocol_version") == 1) {
                    text(R.string.core_protocol_unsupported)
                }
                return client to info
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                if (error is ApiException && error.statusCode == 401) throw error
                lastError = error
            }
        }
        throw lastError
    }

    private fun failConnection(error: Exception, epoch: Long) {
        if (generation != epoch) return
        pinProtection.cancel()
        stopSession()
        stopPrompt()
        api = null
        val unauthorized = error is ApiException && error.statusCode == 401
        mutableState.update {
            it.copy(
                phase = if (unauthorized) ConnectionPhase.UNAUTHORIZED else ConnectionPhase.OFFLINE,
                busy = false,
                error = error.message ?: text(R.string.core_offline),
                unlockOwner = null,
            )
        }
        if (!unauthorized && foreground) {
            reconnectJob?.cancel()
            reconnectJob = viewModelScope.launch {
                delay(5000)
                if (generation == epoch && state.value.captureFlow != CaptureFlow.QR)
                    retryConnection()
            }
        }
    }

    fun refresh() {
        val client = api ?: return
        val epoch = generation
        if (refreshing) return
        refreshing = true
        viewModelScope.launch {
            try {
                loadSnapshot(client, epoch)
            } catch (error: Exception) {
                failConnection(error, epoch)
            } finally {
                refreshing = false
            }
        }
    }

    private suspend fun loadSnapshot(client: DeviceApi, epoch: Long) {
        val snapshot = ++snapshotVersion
        val status = client.json("/api/status")
        val faces = client.json("/api/face-status").faceStatus()
        val users = client.array("/api/users").users(faces.enrolledNames)
        val logs = client.array("/api/logs").logs()
        val settings = client.json("/api/settings").settings()
        if (epoch != generation || snapshot != snapshotVersion) return
        val runtime = status.optJSONObject("runtime")
        val owner = runtime?.optString("unlock_owner")?.takeIf { it.isNotBlank() && it != "null" }
        mutableState.update { current ->
            current.copy(
                status = status.deviceStatus(current.capabilities.simulatedActuator),
                users = users,
                faceStatus = faces,
                logs = logs,
                settings = if (settingsDirty) current.settings else settings,
                unlockOwner = owner,
            )
        }
    }

    private fun command(
        path: String,
        method: String = "POST",
        body: JSONObject? = JSONObject(),
        after: () -> Unit = {},
    ) {
        val client = api ?: return
        if (state.value.busy || state.value.phase != ConnectionPhase.CONNECTED) return
        val epoch = generation
        snapshotVersion++
        mutableState.update { it.copy(busy = true, error = null) }
        viewModelScope.launch {
            try {
                val result = client.json(path, method, body)
                check(result.optBoolean("ok", true)) {
                    result.optString("error", text(R.string.core_operation_failed))
                }
                if (epoch == generation) {
                    after()
                    loadSnapshot(client, epoch)
                }
            } catch (error: Exception) {
                if (epoch == generation) mutableState.update { it.copy(error = error.message) }
                if (error !is ApiException || error.statusCode == 401) failConnection(error, epoch)
            } finally {
                if (epoch == generation) mutableState.update { it.copy(busy = false) }
            }
        }
    }

    fun unlock() {
        if (state.value.phase != ConnectionPhase.CONNECTED) return
        requestPin(PinPurpose.UNLOCK) {
            startDeviceScan("unlock")
        }
    }

    fun verifyIgnition() = startDeviceScan("ignition")

    private fun requestPin(purpose: PinPurpose, action: () -> Unit) {
        if (!foreground || state.value.busy || state.value.pinPrompt != null) return
        val epoch = generation
        val owner = pairing
        val tab = state.value.selectedTab
        mutableState.update { it.copy(error = null) }
        runCatching {
            pinProtection.request(purpose) {
                val sameContext =
                    foreground &&
                        generation == epoch &&
                        pairing == owner &&
                        state.value.selectedTab == tab
                if (sameContext) action()
            }
        }
            .onFailure {
                mutableState.update { it.copy(error = text(R.string.pin_storage_error)) }
            }
    }

    fun submitPin(pin: CharArray) = pinProtection.submit(pin)

    fun cancelPin() = pinProtection.cancel()

    fun lock() {
        stopPrompt()
        command("/api/lock", after = { mutableState.update { it.copy(unlockOwner = null) } })
    }

    fun stopIgnition() =
        command(
            "/api/ignition/stop",
            after = { mutableState.update { it.copy(unlockOwner = null) } },
        )

    fun fullReset() =
        command("/api/full-reset", after = { mutableState.update { it.copy(unlockOwner = null) } })

    fun deleteUser(id: String) = command("/api/users/${DeviceApi.encoded(id)}", "DELETE")

    fun setUserAccess(id: String, allowed: Boolean) =
        command(
            "/api/users/${DeviceApi.encoded(id)}/access",
            "PATCH",
            JSONObject().put("allowed", allowed),
        )

    private var settingsDirty = false

    fun updateSettings(settings: AppSettings) {
        settingsDirty = true
        mutableState.update { it.copy(settings = settings) }
    }

    fun resetSettings() = updateSettings(AppSettings())

    fun saveSettings() {
        val body = state.value.settings.json()
        command("/api/settings", body = body, after = { settingsDirty = false })
    }

    fun clearError() {
        mutableState.update { it.copy(error = null) }
    }

    fun cameraError(message: String) {
        stopSession(restorePrompt = true)
        mutableState.update { it.copy(error = message) }
    }

    fun addAndStartEnrollment(name: String, source: CameraSource) {
        val clean = name.trim()
        if (clean.isEmpty()) return
        if (state.value.phase != ConnectionPhase.CONNECTED) return
        if (source == CameraSource.PHONE) {
            if (!state.value.capabilities.clientCamera) return
            requestPin(PinPurpose.ENROLL) { startEnrollment(clean, source) }
            return
        }
        startEnrollment(clean, source)
    }

    private fun startEnrollment(name: String, source: CameraSource) {
        startSession("enroll", CaptureFlow.ENROLL, source, JSONObject().put("name", name))
    }

    private fun startDeviceScan(purpose: String) {
        if (purpose !in setOf("unlock", "ignition")) return
        if (!state.value.capabilities.deviceCamera) return
        val expectedUser =
            if (purpose == "ignition") state.value.unlockOwner ?: return else null
        stopPrompt()
        val body = JSONObject().put("purpose", purpose)
        if (expectedUser != null) body.put("expected_user", expectedUser)
        val flow =
            if (purpose == "ignition") CaptureFlow.VERIFY_IGNITION else CaptureFlow.VERIFY_UNLOCK
        startSession("scan", flow, CameraSource.DEVICE, body)
    }

    private fun startSession(
        kind: String,
        flow: CaptureFlow,
        source: CameraSource,
        body: JSONObject,
    ) {
        val client = api ?: return
        if (!foreground || state.value.pinPrompt != null) return
        if (state.value.busy || state.value.phase != ConnectionPhase.CONNECTED) return
        val supported = when (source) {
            CameraSource.PHONE -> state.value.capabilities.clientCamera
            CameraSource.DEVICE -> state.value.capabilities.deviceCamera
        }
        if (!supported) return
        sessionGeneration++
        snapshotVersion++
        val token = sessionGeneration
        val epoch = generation
        val pending = PendingStart(epoch, token)
        pendingStart = pending
        body.put(
            "source",
            when (source) {
                CameraSource.PHONE -> "client_camera"
                CameraSource.DEVICE -> "device_camera"
            },
        )
        mutableState.update {
            it.copy(
                busy = true,
                captureFlow = flow,
                cameraSource = source,
                error = null,
                activeSessionProgress = 0,
                activeSessionTotal = null,
                activeSessionMessage = null,
            )
        }
        viewModelScope.launch {
            var created: ActiveSession? = null
            try {
                if (kind == "enroll") {
                    val name = body.getString("name")
                    val exists = state.value.users.any { it.name.equals(name, ignoreCase = true) }
                    if (!exists) {
                        // A created directory entry survives cancelled enrollment and is available
                        // for retry.
                        val added =
                            withContext(NonCancellable) {
                                client.json("/api/users", "POST", JSONObject().put("name", name))
                            }
                        if (epoch == generation) {
                            snapshotVersion++
                            val user = JSONArray().put(added).users(emptyList()).single()
                            mutableState.update { current ->
                                val others = current.users.filter { it.id != user.id }
                                current.copy(users = listOf(user) + others)
                            }
                        }
                    }
                    if (epoch != generation || token != sessionGeneration || !foreground)
                        return@launch
                }
                // Keep the start response so a background transition can cancel the exact created
                // session.
                val result =
                    withContext(NonCancellable) {
                        val response = client.json("/api/$kind/start", "POST", body)
                        created =
                            ActiveSession(
                                response.getString("session_id"),
                                kind,
                                client,
                                token,
                                flow,
                            )
                        response
                    }
                val started = checkNotNull(created)
                if (epoch != generation || token != sessionGeneration || !foreground) {
                    cancelRemote(started)
                    return@launch
                }
                session = started
                applySession(result, started)
                if (session != null) startPolling(started)
            } catch (error: Exception) {
                created?.let { cancelRemote(it) }
                if (token == sessionGeneration) {
                    session = null
                    mutableState.update {
                        it.copy(busy = false, captureFlow = CaptureFlow.NONE, error = error.message)
                    }
                    if (error is ApiException && error.statusCode == 401)
                        failConnection(error, epoch)
                    else refresh()
                }
            } finally {
                if (pendingStart == pending) {
                    pendingStart = null
                    val cancelledHere =
                        epoch == generation && state.value.captureFlow == CaptureFlow.NONE
                    if (cancelledHere) mutableState.update { it.copy(busy = false) }
                }
            }
        }
    }

    private fun startPolling(active: ActiveSession) {
        pollJob?.cancel()
        pollJob = viewModelScope.launch {
            try {
                while (session == active) {
                    delay(700)
                    val result =
                        active.api.json(
                            "/api/${active.kind}/status?session_id=${DeviceApi.encoded(active.id)}"
                        )
                    if (session == active) applySession(result, active)
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                if (session == active)
                    cameraError(error.message ?: text(R.string.core_session_disconnected))
            }
        }
    }

    fun submitEnrollmentSample(bytes: ByteArray) = submitFrame(bytes)

    fun submitFrame(bytes: ByteArray) {
        val active = session ?: return
        if (active.kind != "enroll") return
        if (
            state.value.cameraSource != CameraSource.PHONE ||
                frameJob?.isActive == true ||
                !foreground
        )
            return
        frameJob = viewModelScope.launch {
            try {
                val result = active.api.sample(active.kind, active.id, bytes)
                if (session != active) return@launch
                applySession(result, active)
                val enough = result.optInt("count") >= result.optInt("samples_needed", 10)
                if (active.kind == "enroll" && enough && session == active) {
                    val finished =
                        active.api.json(
                            "/api/enroll/finish",
                            "POST",
                            JSONObject().put("session_id", active.id),
                        )
                    if (session == active) applySession(finished, active)
                }
            } catch (error: Exception) {
                if (session == active)
                    cameraError(error.message ?: text(R.string.core_upload_failed))
            }
        }
    }

    private fun applySession(result: JSONObject, active: ActiveSession) {
        if (session != active || active.generation != sessionGeneration) return
        val phase = result.optString("state")
        val message = result.optString("message").takeIf { it.isNotBlank() }
        val terminal =
            phase in
                setOf("completed", "granted", "denied", "cancelled", "error", "timeout", "expired")
        val failed = phase in setOf("denied", "error", "timeout", "expired")
        val window = result.optJSONObject("window")
        mutableState.update {
            it.copy(
                activeSessionProgress =
                    if (active.kind == "enroll") result.optInt("count")
                    else window?.optInt("matches"),
                activeSessionTotal =
                    if (active.kind == "enroll") result.optInt("samples_needed", 10)
                    else window?.optInt("needed"),
                activeSessionMessage = message ?: phase,
                error =
                    if (failed) message ?: text(R.string.session_failed_retry)
                    else it.error,
                unlockOwner =
                    if (phase == "granted" && active.flow == CaptureFlow.VERIFY_UNLOCK)
                        result.optString("user").takeIf { name -> name.isNotBlank() }
                    else it.unlockOwner,
                busy = !terminal,
                captureFlow = if (terminal) CaptureFlow.NONE else it.captureFlow,
            )
        }
        if (terminal) {
            session = null
            if (phase == "granted" && active.flow == CaptureFlow.VERIFY_UNLOCK) startPrompt()
            if (phase != "granted" && active.flow == CaptureFlow.VERIFY_IGNITION) startPrompt()
            refresh()
        }
    }

    private fun startPrompt() {
        stopPrompt()
        val seconds = state.value.settings.promptAutoLockSeconds
        if (seconds <= 0) return
        val epoch = generation
        promptJob = viewModelScope.launch {
            for (remaining in seconds downTo 1) {
                mutableState.update { it.copy(promptCountdown = remaining) }
                delay(1000)
            }
            mutableState.update { it.copy(promptCountdown = null) }
            if (epoch == generation && foreground && state.value.unlockOwner != null) lock()
        }
    }

    private fun stopPrompt() {
        promptJob?.cancel()
        promptJob = null
        mutableState.update { it.copy(promptCountdown = null) }
    }

    fun cancelActiveSession() = stopSession(restorePrompt = true)

    private fun stopSession(restorePrompt: Boolean = false) {
        pairingScanGeneration = null
        sessionGeneration++
        val active = session
        val wasIgnition = state.value.captureFlow == CaptureFlow.VERIFY_IGNITION
        session = null
        pollJob?.cancel()
        mutableState.update {
            val captureWasActive = it.captureFlow != CaptureFlow.NONE
            val startStillPending = pendingStart?.deviceGeneration == generation
            it.copy(
                busy = if (captureWasActive) startStillPending else it.busy,
                captureFlow = CaptureFlow.NONE,
            )
        }
        if (active != null) {
            viewModelScope.launch(NonCancellable) {
                cancelRemote(active)
            }
        }
        val canRestore =
            restorePrompt &&
                wasIgnition &&
                foreground &&
                state.value.phase == ConnectionPhase.CONNECTED
        if (canRestore) startPrompt()
    }

    private suspend fun cancelRemote(active: ActiveSession) =
        withContext(NonCancellable) {
            runCatching {
                active.api.json(
                    "/api/${active.kind}/cancel",
                    "POST",
                    JSONObject().put("session_id", active.id),
                )
            }
            Unit
        }

    override fun onCleared() {
        pinProtection.cancel()
        stopSession()
        super.onCleared()
    }
}
