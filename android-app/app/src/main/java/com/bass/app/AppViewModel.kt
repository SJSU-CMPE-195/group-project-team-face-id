package com.bass.app

import android.app.Application
import android.os.SystemClock
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

class AppViewModel(application: Application) : AndroidViewModel(application) {
    private fun text(id: Int): String = getApplication<Application>().getString(id)

    private val mutableState = MutableStateFlow(BassState())
    val state = mutableState.asStateFlow()
    private val mutableNewUserDraft = MutableStateFlow(NewUserDraft())
    val newUserDraft = mutableNewUserDraft.asStateFlow()
    private val store = PairingStore(application)
    private val discovery = DeviceDiscovery(application)
    private val connector = DeviceConnector(application, discovery)
    private val commissioning = CommissioningCoordinator(store, connector)
    private val pinProtection =
        PinProtection(application, viewModelScope) { prompt ->
            mutableState.update { it.copy(pinPrompt = prompt) }
        }
    private var pairingScanGeneration: Long? = null
    private var pendingPairingQr: PairingQr? = null
    private var pendingPairingInviteToken = ""
    private var pendingCommissioning: PendingCommissioning? = null
    private var pendingTransfer: PendingTransfer? = null
    private var pendingPairing: Pairing? = null
    private var pairingExchangeStarted = false
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
    private var sessionLoginVerified = false
    private var commissioningSubmittedThisForeground = false
    private var session: ActiveSession? = null
    private var refreshing = false
    private var snapshotVersion = 0L
    private var pendingStart: PendingStart? = null

    init {
        runCatching { store.loadPairings() }
            .onSuccess { stored ->
                pairing = stored.active
                pendingPairing = stored.pending
                pendingCommissioning = stored.pendingCommissioning
                pendingTransfer = stored.pendingTransfer
                val owner = stored.pending ?: stored.active
                if (owner != null) {
                    mutableState.update {
                        it.copy(
                            phase =
                                if (stored.pending == null) it.phase else ConnectionPhase.OFFLINE,
                            identity = owner.identity,
                            error =
                                text(R.string.core_pairing_verification_pending)
                                    .takeIf { stored.pending != null },
                        )
                    }
                }
                if (stored.pendingCommissioning != null) {
                    mutableState.update {
                        it.copy(
                            phase = ConnectionPhase.OFFLINE,
                            exportMaterial = stored.exportMaterial,
                            error = text(R.string.core_commissioning_pending),
                        )
                    }
                } else if (stored.exportMaterial != null) {
                    mutableState.update { it.copy(exportMaterial = stored.exportMaterial) }
                }
                if (stored.pendingTransfer != null) {
                    mutableState.update { it.copy(transferPending = true) }
                }
            }
            .onFailure { error ->
                mutableState.update { current ->
                    current.copy(error = text(error.pairingStorageErrorResource))
                }
            }
    }

    fun onForeground() {
        foreground = true
        if (pairing != null || pendingPairing != null || pendingCommissioning != null) {
            retryConnection()
        }
    }

    fun onBackground() {
        val exchangeWasInterrupted =
            pairingExchangeStarted && pendingPairing == null && pendingCommissioning == null
        foreground = false
        sessionLoginVerified = false
        commissioningSubmittedThisForeground = false
        pinProtection.cancel()
        pendingPairingQr = null
        pendingPairingInviteToken = ""
        pairingScanGeneration = null
        pairingExchangeStarted = false
        generation++
        connectionJob?.cancel()
        reconnectJob?.cancel()
        stopPrompt()
        stopSession()
        api = null
        mutableState.update {
            it.afterBackground(
                hasPairing = pairing != null || pendingPairing != null || pendingCommissioning != null,
                interruptedExchangeError =
                    text(R.string.core_pairing_exchange_uncertain).takeIf {
                        exchangeWasInterrupted
                    },
            ).copy(inviteMaterial = null)
        }
        clearNewUserDraft()
    }

    fun selectTab(tab: Tab) {
        if (tab == Tab.LOGS && state.value.identity?.user?.isAdmin != true) return
        if (tab != state.value.selectedTab) {
            pinProtection.cancel()
            stopSession(restorePrompt = true)
            if (state.value.selectedTab == Tab.USERS) clearNewUserDraft()
        }
        mutableState.update { it.copy(selectedTab = tab) }
    }

    fun beginPairing() {
        beginPairingScan()
    }

    private fun beginPairingScan() {
        if (pendingCommissioning != null) {
            retryConnection()
            return
        }
        generation++
        connectionJob?.cancel()
        reconnectJob?.cancel()
        api = null
        stopPrompt()
        stopSession()
        pendingPairingQr = null
        pendingPairingInviteToken = ""
        pairingScanGeneration = generation
        mutableState.update(BassState::startQrCapture)
    }

    fun scanPairingQr(raw: String) = acceptPairingQr(raw)

    fun acceptPairingQr(raw: String) {
        if (!foreground || pairingScanGeneration != generation) return
        if (state.value.captureFlow != CaptureFlow.QR) return
        val invitation = runCatching { PairingInviteQr.parse(raw) }.getOrNull()
        val parsed = runCatching { invitation?.deviceQr() ?: PairingQr.parse(raw) }
            .getOrElse { error ->
                mutableState.update { current ->
                    current.copy(
                        error =
                            if (error is PairingUpgradeRequiredException)
                                text(R.string.core_pairing_upgrade_required)
                            else text(R.string.core_invalid_qr)
                    )
                }
                return
            }
        stopSession()
        pendingPairingQr = parsed
        pendingPairingInviteToken = invitation?.inviteToken.orEmpty()
        settingsDirty = false
        api = null
        mutableState.value =
            if (parsed.purpose == CommissioningPurpose.DEVICE) {
                BassState(
                    pairingCredentialsRequired = true,
                    pairingInviteReady = invitation != null,
                )
            } else {
                BassState(commissioningPrompt = CommissioningPrompt(parsed.purpose))
            }
    }

    fun beginPairingInviteScan() {
        val qr = pendingPairingQr
        if (
            qr?.purpose != CommissioningPurpose.DEVICE ||
                !state.value.pairingCredentialsRequired ||
                state.value.busy
        ) return
        mutableState.update { it.copy(captureFlow = CaptureFlow.QR, error = null) }
    }

    fun cancelPairingInviteScan() {
        if (!state.value.pairingCredentialsRequired || state.value.busy) return
        mutableState.update { it.copy(captureFlow = CaptureFlow.NONE) }
    }

    fun acceptPairingInviteQr(raw: String) {
        if (!foreground || state.value.captureFlow != CaptureFlow.QR) return
        val invite =
            runCatching { PairingInviteQr.parse(raw) }.getOrElse {
                mutableState.update { current ->
                    current.copy(error = text(R.string.pair_invite_invalid_qr))
                }
                return
            }
        acceptPairingInvite(invite)
    }

    private fun acceptPairingInvite(invite: PairingInviteQr): Boolean {
        val qr = pendingPairingQr
        if (
            qr?.purpose != CommissioningPurpose.DEVICE ||
                !state.value.pairingCredentialsRequired ||
                !foreground ||
                state.value.captureFlow != CaptureFlow.QR ||
                state.value.busy
        ) return false
        if (
            invite.hostDeviceId != qr.hostDeviceId ||
                invite.certificateSha256 != qr.certificateSha256
        ) {
            mutableState.update { it.copy(error = text(R.string.pair_invite_wrong_device)) }
            return false
        }
        return acceptPairingInviteToken(invite.inviteToken)
    }

    private fun acceptPairingInviteToken(inviteToken: String): Boolean {
        if (
            pendingPairingQr?.purpose != CommissioningPurpose.DEVICE ||
                !state.value.pairingCredentialsRequired ||
                !foreground ||
                state.value.busy ||
                !inviteToken.matches(HEX_SECRET_PATTERN)
        ) return false
        pendingPairingInviteToken = inviteToken
        mutableState.update {
            it.copy(
                pairingInviteReady = true,
                captureFlow = CaptureFlow.NONE,
                error = null,
            )
        }
        return true
    }

    fun submitPairingCredentials(pin: CharArray) {
        val qr = pendingPairingQr
        val invite = pendingPairingInviteToken
        if (
            qr == null ||
                !foreground ||
                state.value.busy ||
                !invite.matches(HEX_SECRET_PATTERN) ||
                pin.size != 6 ||
                pin.any { it !in '0'..'9' }
        ) {
            pin.fill('\u0000')
            mutableState.update { it.copy(error = text(R.string.pair_credentials_invalid)) }
            return
        }

        submitCommissioning(qr, pin, inviteToken = invite)
    }

    fun submitOwnerCommissioning(name: String, pin: CharArray) {
        val qr = pendingPairingQr
        val cleanName = name.trim()
        if (
            qr == null ||
                qr.purpose == CommissioningPurpose.DEVICE ||
                (qr.purpose != CommissioningPurpose.RECOVERY && cleanName.isEmpty()) ||
                cleanName.length > MAX_USER_NAME_LENGTH ||
                pin.size != 6 ||
                pin.any { it !in '0'..'9' }
        ) {
            pin.fill('\u0000')
            mutableState.update { it.copy(error = text(R.string.commissioning_invalid)) }
            return
        }
        submitCommissioning(qr, pin, name = cleanName)
    }

    private fun submitCommissioning(
        qr: PairingQr,
        pin: CharArray,
        name: String = "",
        inviteToken: String = "",
    ) {
        pendingCommissioning?.let {
            pin.fill('\u0000')
            retryConnection()
            return
        }
        if (!foreground || state.value.busy) {
            pin.fill('\u0000')
            return
        }
        commissioningSubmittedThisForeground = true
        val pending = commissioning.newRequest(qr, String(pin), name, inviteToken)
        pin.fill('\u0000')
        val writeScope = store.beginWriteScope()
        val saved = runCatching { store.savePendingCommissioning(pending, writeScope) }
        if (saved.getOrDefault(false).not()) {
            mutableState.update {
                it.copy(error = text(R.string.core_pairing_pending_save_failed), busy = false)
            }
            return
        }
        pendingCommissioning = pending
        runCommissioning(pending, writeScope)
    }

    private fun runCommissioning(pending: PendingCommissioning, writeScope: Long) {
        generation++
        val epoch = generation
        connectionJob?.cancel()
        reconnectJob?.cancel()
        mutableState.update {
            it.startConnection().copy(
                commissioningPrompt = null,
                pairingCredentialsRequired = false,
                busy = true,
            )
        }
        connectionJob =
            viewModelScope.launch {
                try {
                    val completion =
                        commissioning.execute(
                            pending,
                            writeScope,
                            onConnecting = {
                                if (epoch == generation) {
                                    mutableState.update {
                                        it.copy(phase = ConnectionPhase.CONNECTING)
                                    }
                                }
                            },
                            onExchangeStarted = { pairingExchangeStarted = true },
                        )
                    if (epoch != generation || !foreground) return@launch
                    val connected = completion.connected
                    pairing = connected.pairing
                    pendingPairing = null
                    pendingCommissioning = null
                    pendingPairingQr = null
                    pendingPairingInviteToken = ""
                    pairingExchangeStarted = false
                    api = connected.api
                    applyConnected(connected)
                    val canEnterWithoutLogin = commissioningSubmittedThisForeground
                    commissioningSubmittedThisForeground = false
                    if (canEnterWithoutLogin) {
                        sessionLoginVerified = true
                        resumeConnected(
                            connected,
                            epoch,
                            exportMaterial = completion.exportMaterial,
                        )
                    } else {
                        requestSessionLogin(connected, epoch) {
                            resumeConnected(
                                connected,
                                epoch,
                                exportMaterial = completion.exportMaterial,
                            )
                        }
                    }
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Exception) {
                    if (epoch == generation) {
                        val isDevicePairing =
                            pending.qr.purpose == CommissioningPurpose.DEVICE
                        val deviceCredentialRejected =
                            isDevicePairing &&
                                error is ApiException &&
                                error.errorCode in
                                    setOf(
                                        "invalid_credentials",
                                        "pin_locked",
                                        "invalid_pairing_invite",
                                    )
                        val retirePending =
                            if (isDevicePairing) deviceCredentialRejected
                            else error.isDefinitiveCommissioningFailure()
                        val inviteRejected =
                            error is ApiException &&
                                error.errorCode == "invalid_pairing_invite"
                        val pendingRetired =
                            retirePending &&
                                runCatching {
                                    store.discardPendingCommissioning(writeScope)
                                }.getOrDefault(false)
                        if (pendingRetired) {
                            pendingCommissioning = null
                        }
                        if (deviceCredentialRejected && pendingRetired) {
                            pendingPairingQr = pending.qr
                            pendingPairingInviteToken =
                                pending.inviteToken.takeUnless { inviteRejected }.orEmpty()
                        }
                        mutableState.update {
                            it.copy(
                                phase =
                                    if (pendingRetired) ConnectionPhase.UNAUTHORIZED
                                    else if (
                                        error is TlsIdentityException ||
                                            error is DeviceIdentityException
                                    ) ConnectionPhase.ERROR
                                    else ConnectionPhase.OFFLINE,
                                commissioningPrompt =
                                    CommissioningPrompt(pending.qr.purpose)
                                        .takeIf {
                                            pendingRetired &&
                                                pending.qr.purpose != CommissioningPurpose.DEVICE
                                        },
                                pairingCredentialsRequired =
                                    pendingRetired &&
                                        pending.qr.purpose == CommissioningPurpose.DEVICE,
                                pairingInviteReady =
                                    pendingPairingInviteToken.matches(HEX_SECRET_PATTERN),
                                busy = false,
                                error = errorText(error, R.string.core_commissioning_pending),
                            )
                        }
                    }
                } finally {
                    pairingExchangeStarted = false
                }
            }
    }

    fun cancelPairingCredentials() {
        commissioningSubmittedThisForeground = false
        pinProtection.cancel()
        generation++
        connectionJob?.cancel()
        connectionJob = null
        pairingExchangeStarted = false
        pendingPairingQr = null
        pendingPairingInviteToken = ""
        pairingScanGeneration = null
        mutableState.value = BassState()
    }

    fun requestForgetDevice() {
        if (state.value.busy || state.value.showForgetDeviceDialog) return
        mutableState.update {
            it.copy(showForgetDeviceDialog = true, forgetDeviceError = null, error = null)
        }
    }

    fun cancelForgetDevice() {
        if (state.value.busy) return
        if (pairing == null && pendingPairing == null && pendingCommissioning == null) {
            mutableState.value = BassState()
            return
        }
        mutableState.update {
            it.copy(showForgetDeviceDialog = false, forgetDeviceError = null)
        }
    }

    fun forgetDevice() {
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
        sessionLoginVerified = false
        commissioningSubmittedThisForeground = false
        pendingPairingQr = null
        pendingPairingInviteToken = ""
        pendingPairing = null
        pendingCommissioning = null
        pendingTransfer = null
        settingsDirty = false
        clearNewUserDraft()
        mutableState.value = BassState()
    }

    fun retryConnection() {
        if (!foreground) return
        val stored =
            runCatching { store.loadPairings() }.getOrElse { error ->
                mutableState.update {
                    it.copy(
                        phase = ConnectionPhase.ERROR,
                        busy = false,
                        error = text(error.pairingStorageErrorResource),
                    )
                }
                return
            }
        pairing = stored.active
        pendingPairing = stored.pending
        pendingCommissioning = stored.pendingCommissioning
        pendingTransfer = stored.pendingTransfer
        mutableState.update {
            it.copy(
                exportMaterial = stored.exportMaterial,
                transferPending = stored.pendingTransfer != null,
            )
        }
        if (stored.pendingCommissioning != null) {
            runCommissioning(stored.pendingCommissioning, stored.writeScope)
            return
        }
        if (stored.pending != null) {
            retryPendingConnection(stored.pending, stored.writeScope)
            return
        }
        val owner = stored.active ?: return
        pinProtection.cancel()
        generation++
        val epoch = generation
        val writeScope = stored.writeScope
        connectionJob?.cancel()
        reconnectJob?.cancel()
        stopPrompt()
        stopSession()
        api = null
        mutableState.update(BassState::startConnection)
        connectionJob = viewModelScope.launch {
            try {
                val connected =
                    connector.connect(owner) {
                        if (epoch == generation) {
                            mutableState.update { it.copy(phase = ConnectionPhase.CONNECTING) }
                        }
                    }
                if (epoch != generation) return@launch
                if (connected.pairing != owner) {
                    val saved =
                        withContext(Dispatchers.IO) {
                            store.save(connected.pairing, writeScope)
                        }
                    if (!saved) return@launch
                }
                if (epoch != generation) return@launch
                pairing = connected.pairing
                api = connected.api
                applyConnected(connected)
                if (sessionLoginVerified) {
                    resumeConnected(connected, epoch, transferWriteScope = writeScope)
                } else {
                    requestSessionLogin(connected, epoch) {
                        resumeConnected(connected, epoch, transferWriteScope = writeScope)
                    }
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                failConnection(error, epoch)
            }
        }
    }

    private fun retryPendingConnection(owner: Pairing, writeScope: Long) {
        if (!foreground) return
        pinProtection.cancel()
        generation++
        val epoch = generation
        connectionJob?.cancel()
        reconnectJob?.cancel()
        stopPrompt()
        stopSession()
        api = null
        mutableState.update(BassState::startConnection)
        connectionJob =
            viewModelScope.launch {
                try {
                    val connected =
                        connector.connect(owner) {
                            if (epoch == generation) {
                                mutableState.update { it.copy(phase = ConnectionPhase.CONNECTING) }
                            }
                        }
                    if (epoch != generation) return@launch
                    if (!promotePendingPairing(connected.pairing, writeScope)) return@launch
                    if (epoch != generation) return@launch
                    api = connected.api
                    applyConnected(connected)
                    if (sessionLoginVerified) {
                        resumeConnected(connected, epoch, transferWriteScope = writeScope)
                    } else {
                        requestSessionLogin(connected, epoch) {
                            resumeConnected(connected, epoch, transferWriteScope = writeScope)
                        }
                    }
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Exception) {
                    failConnection(error, epoch)
                }
            }
    }

    private suspend fun promotePendingPairing(owner: Pairing, writeScope: Long): Boolean =
        withContext(NonCancellable) {
            val promoted =
                withContext(Dispatchers.IO) { store.promotePending(owner, writeScope) }
            if (!promoted || !store.isWriteScopeCurrent(writeScope)) {
                false
            } else {
                pairing = owner
                pendingPairing = null
                true
            }
        }

    private fun requestSessionLogin(
        connected: ConnectedDevice,
        epoch: Long,
        onAuthenticated: () -> Unit,
    ) {
        if (!isCurrentConnection(connected, epoch)) return
        mutableState.update {
            it.copy(
                phase = ConnectionPhase.SIGN_IN_REQUIRED,
                busy = false,
                error = null,
            )
        }
        pinProtection.request(
            purpose = PinPurpose.LOGIN,
            verify = { pin ->
                try {
                    PinVerification(
                        DeviceAuth.sessionLogin(
                            connected.api,
                            pin,
                            connected.pairing.identity,
                        ).deviceId
                    )
                } catch (error: ApiException) {
                    if (error.errorCode == "invalid_credentials") {
                        throw IllegalArgumentException(text(R.string.sign_in_invalid_pin))
                    }
                    throw error
                }
            },
            onCancelled = {
                if (isCurrentConnection(connected, epoch)) {
                    sessionLoginVerified = false
                    generation++
                    api = null
                    mutableState.update {
                        it.copy(
                            phase = ConnectionPhase.SIGN_IN_REQUIRED,
                            status = null,
                            users = emptyList(),
                            faceStatus = FaceStatus(),
                            logs = emptyList(),
                            unlockOwner = null,
                            busy = false,
                            error = null,
                        )
                    }
                }
            },
        ) {
            if (isCurrentConnection(connected, epoch)) {
                sessionLoginVerified = true
                onAuthenticated()
            }
        }
    }

    private fun resumeConnected(
        connected: ConnectedDevice,
        epoch: Long,
        exportMaterial: ExportMaterial? = state.value.exportMaterial,
        transferWriteScope: Long? = null,
    ) {
        if (!isCurrentConnection(connected, epoch) || !sessionLoginVerified) return
        mutableState.update {
            it.copy(
                phase = ConnectionPhase.CONNECTING,
                pairingCredentialsRequired = false,
                commissioningPrompt = null,
                exportMaterial = exportMaterial,
                busy = true,
                error = null,
            )
        }
        connectionJob =
            viewModelScope.launch {
                try {
                    loadSnapshot(connected.api, epoch)
                    if (!isCurrentConnection(connected, epoch)) return@launch
                    mutableState.update {
                        it.copy(phase = ConnectionPhase.CONNECTED, busy = false)
                    }
                    transferWriteScope?.let { scope ->
                        pendingTransfer?.let { runPendingTransfer(it, scope) }
                    }
                    pollConnected(connected.api, epoch)
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Exception) {
                    failConnection(error, epoch)
                }
            }
    }

    private fun isCurrentConnection(connected: ConnectedDevice, epoch: Long): Boolean =
        foreground &&
            generation == epoch &&
            pairing == connected.pairing &&
            api === connected.api

    private fun applyConnected(connected: ConnectedDevice) {
        mutableState.update { it.withConnectedDevice(connected) }
    }

    private suspend fun pollConnected(client: DeviceApi, epoch: Long) {
        while (foreground && epoch == generation) {
            delay(3000)
            if (!state.value.busy) {
                try {
                    loadSnapshot(client, epoch)
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Exception) {
                    failConnection(error, epoch)
                    return
                }
            }
        }
    }

    private fun failConnection(error: Exception, epoch: Long) {
        if (generation != epoch) return
        pinProtection.cancel()
        stopSession()
        stopPrompt()
        api = null
        val unauthorized = error is ApiException && error.statusCode == 401
        val identityFailure = error is TlsIdentityException || error is DeviceIdentityException
        if (unauthorized || identityFailure) sessionLoginVerified = false
        mutableState.update {
            it.copy(
                phase =
                    when {
                        unauthorized -> ConnectionPhase.UNAUTHORIZED
                        identityFailure -> ConnectionPhase.ERROR
                        else -> ConnectionPhase.OFFLINE
                    },
                busy = false,
                error = errorText(error, R.string.core_offline),
                unlockOwner = null,
            )
        }
        if (!unauthorized && !identityFailure && foreground) {
            reconnectJob?.cancel()
            reconnectJob = viewModelScope.launch {
                delay(5000)
                if (generation == epoch && state.value.captureFlow != CaptureFlow.QR)
                    retryConnection()
            }
        }
    }

    private fun errorText(error: Exception, fallback: Int): String =
        if (error is TlsIdentityException) text(R.string.core_tls_identity_mismatch)
        else if (error is DeviceIdentityException) text(R.string.core_identity_mismatch)
        else error.message ?: text(fallback)

    fun refresh() {
        val client = api ?: return
        val epoch = generation
        if (
            refreshing ||
                !sessionLoginVerified ||
                state.value.phase != ConnectionPhase.CONNECTED
        ) return
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
        val savedIdentity = pairing?.identity
            ?: throw DeviceIdentityException(text(R.string.core_identity_mismatch))
        val loaded = client.readSnapshot(savedIdentity)
        if (epoch != generation || snapshot != snapshotVersion) return
        mutableState.update { it.withSnapshot(loaded, settingsDirty) }
    }

    private fun command(
        path: String,
        method: String = "POST",
        body: JSONObject? = JSONObject(),
        operationGrant: String? = null,
        after: () -> Unit = {},
    ) {
        val client = api ?: return
        if (state.value.busy || state.value.phase != ConnectionPhase.CONNECTED) return
        val epoch = generation
        snapshotVersion++
        mutableState.update { it.copy(busy = true, error = null) }
        viewModelScope.launch {
            try {
                val result = client.json(path, method, body, operationGrant)
                check(result.optBoolean("ok", true)) {
                    result.optString("error", text(R.string.core_operation_failed))
                }
                if (epoch == generation) {
                    after()
                    loadSnapshot(client, epoch)
                }
            } catch (error: Exception) {
                if (epoch == generation) {
                    mutableState.update {
                        it.copy(error = errorText(error, R.string.core_operation_failed))
                    }
                }
                if (error !is ApiException || error.statusCode == 401) failConnection(error, epoch)
            } finally {
                if (epoch == generation) mutableState.update { it.copy(busy = false) }
            }
        }
    }

    fun unlock() {
        if (state.value.phase != ConnectionPhase.CONNECTED) return
        val userId = state.value.identity?.user?.id ?: return
        requestGrant(
            PinPurpose.UNLOCK,
            OperationRequest(OperationAction.SCAN_UNLOCK, userId),
        ) { grant ->
            startDeviceScan("unlock", grant)
        }
    }

    fun verifyIgnition() {
        val userId = state.value.identity?.user?.id ?: return
        requestGrant(
            PinPurpose.IGNITION,
            OperationRequest(OperationAction.SCAN_IGNITION, userId),
        ) { grant ->
            startDeviceScan("ignition", grant)
        }
    }

    private fun requestGrant(
        purpose: PinPurpose,
        operation: OperationRequest,
        confirmation: PinConfirmation? = null,
        onCancelled: () -> Unit = {},
        action: (String) -> Unit,
    ) {
        val client = api
        if (
            client == null ||
                !foreground ||
                state.value.busy ||
                state.value.pinPrompt != null ||
                state.value.phase != ConnectionPhase.CONNECTED ||
                operation.action.requiresActuatorControl && !state.value.canControlActuators
        ) {
            onCancelled()
            return
        }
        val epoch = generation
        val owner = pairing
        val actorId = state.value.identity?.user?.id
        val tab = state.value.selectedTab
        mutableState.update { it.copy(error = null) }
        pinProtection.request(
            purpose = purpose,
            verify = { pin ->
                val requestedAt = SystemClock.elapsedRealtime()
                val grant = DeviceAuth.operationGrant(client, pin, operation)
                PinVerification(
                    value = grant.token,
                    expiresAtElapsedRealtime = requestedAt + grant.expiresIn * 1000L,
                )
            },
            confirmation = confirmation,
            onCancelled = onCancelled,
        ) { grant ->
            val sameContext =
                foreground &&
                    generation == epoch &&
                    pairing == owner &&
                    state.value.identity?.user?.id == actorId &&
                    state.value.selectedTab == tab &&
                    (!operation.action.requiresActuatorControl ||
                        state.value.canControlActuators)
            if (sameContext) action(grant) else onCancelled()
        }
    }

    fun submitPin(pin: CharArray) = pinProtection.submit(pin)

    fun cancelPin() = pinProtection.cancel()

    fun lock() {
        stopPrompt()
        requestGrant(PinPurpose.LOCK, OperationRequest(OperationAction.DEVICE_LOCK)) { grant ->
            command(
                "/api/lock",
                operationGrant = grant,
                after = { mutableState.update { it.copy(unlockOwner = null) } },
            )
        }
    }

    fun stopIgnition() {
        requestGrant(
            PinPurpose.STOP_IGNITION,
            OperationRequest(OperationAction.IGNITION_STOP),
        ) { grant ->
            command(
                "/api/ignition/stop",
                operationGrant = grant,
                after = { mutableState.update { it.copy(unlockOwner = null) } },
            )
        }
    }

    fun fullReset() {
        if (state.value.identity?.user?.isOwner != true) return
        requestGrant(PinPurpose.RESET, OperationRequest(OperationAction.DEVICE_RESET)) { grant ->
            command(
                "/api/full-reset",
                operationGrant = grant,
                after = { mutableState.update { it.copy(unlockOwner = null) } },
            )
        }
    }

    fun deleteUser(id: String) {
        if (state.value.identity?.user?.isAdmin != true) return
        if (state.value.users.any { it.id == id && it.isOwner }) return
        requestGrant(
            PinPurpose.USER_ADMIN,
            OperationRequest(OperationAction.USER_DELETE, id),
        ) { grant ->
            command(
                "/api/users/${DeviceApi.encoded(id)}",
                "DELETE",
                operationGrant = grant,
            )
        }
    }

    fun setUserAccess(id: String, allowed: Boolean) {
        if (state.value.identity?.user?.isAdmin != true) return
        if (state.value.users.any { it.id == id && it.isOwner }) return
        requestGrant(
            PinPurpose.USER_ADMIN,
            OperationRequest(OperationAction.USER_ACCESS, id),
        ) { grant ->
            command(
                "/api/users/${DeviceApi.encoded(id)}/access",
                "PATCH",
                JSONObject().put("allowed", allowed),
                operationGrant = grant,
            )
        }
    }

    fun updateNewUserName(name: String) {
        mutableNewUserDraft.update {
            it.copy(name = name.take(MAX_USER_NAME_LENGTH), revision = it.revision + 1)
        }
    }

    fun updateNewUserPin(pin: String) {
        mutableNewUserDraft.update {
            it.copy(
                pin = pin.filter(Char::isDigit).take(6),
                revision = it.revision + 1,
            )
        }
    }

    fun updateNewUserAdmin(isAdmin: Boolean) {
        mutableNewUserDraft.update {
            if (it.isAdmin == isAdmin) {
                it
            } else {
                it.copy(isAdmin = isAdmin, revision = it.revision + 1)
            }
        }
    }

    fun createUser() {
        val draft = newUserDraft.value
        val cleanName = draft.name.trim()
        val newPin = draft.pin.toCharArray()
        val validPin = newPin.size == 6 && newPin.all { it in '0'..'9' }
        if (
            state.value.identity?.user?.isAdmin != true ||
                cleanName.isEmpty() ||
                cleanName.length > MAX_USER_NAME_LENGTH ||
                !validPin
        ) {
            newPin.fill('\u0000')
            return
        }
        requestGrant(
            PinPurpose.USER_ADMIN,
            OperationRequest(OperationAction.USER_CREATE),
            confirmation = PinConfirmation(cleanName, newPin),
            onCancelled = { newPin.fill('\u0000') },
        ) { grant ->
            if (newUserDraft.value.revision != draft.revision) {
                newPin.fill('\u0000')
                return@requestGrant
            }
            val body =
                JSONObject()
                    .put("name", cleanName)
                    .put("pin", String(newPin))
                    .put("is_admin", draft.isAdmin)
            newPin.fill('\u0000')
            command(
                "/api/users",
                body = body,
                operationGrant = grant,
                after = { clearNewUserDraft(draft.revision) },
            )
        }
    }

    private fun clearNewUserDraft(expectedRevision: Long? = null) {
        mutableNewUserDraft.update {
            if (expectedRevision != null && it.revision != expectedRevision) {
                it
            } else {
                NewUserDraft(revision = it.revision + 1)
            }
        }
    }

    fun createPairingInvite(userId: String) {
        val currentPairing = pairing ?: return
        val sessionUser = state.value.identity?.user ?: return
        val target = state.value.users.singleOrNull { it.id == userId } ?: return
        if (!sessionUser.isAdmin || (target.isOwner && !sessionUser.isOwner)) return
        requestGrant(
            PinPurpose.USER_ADMIN,
            OperationRequest(OperationAction.PAIRING_INVITE, userId),
        ) { grant ->
            val client = api ?: return@requestGrant
            val epoch = generation
            mutableState.update { it.copy(busy = true, inviteMaterial = null) }
            viewModelScope.launch {
                try {
                    val response =
                        client.json(
                            "/api/pairing-invites",
                            "POST",
                            JSONObject().put("user_id", userId),
                            grant,
                        )
                    val material = pairingInviteMaterial(response, target.id, currentPairing)
                    if (epoch == generation) {
                        mutableState.update {
                            it.copy(inviteMaterial = material)
                        }
                    }
                } catch (error: Exception) {
                    if (epoch == generation) {
                        mutableState.update {
                            it.copy(error = errorText(error, R.string.invite_failed))
                        }
                    }
                } finally {
                    if (epoch == generation) mutableState.update { it.copy(busy = false) }
                }
            }
        }
    }

    fun clearInviteMaterial() {
        mutableState.update { it.copy(inviteMaterial = null) }
    }

    private var settingsDirty = false

    fun updateSettings(settings: AppSettings) {
        if (state.value.identity?.user?.isAdmin != true) return
        settingsDirty = true
        mutableState.update { it.copy(settings = settings) }
    }

    fun resetSettings() = updateSettings(AppSettings())

    fun saveSettings() {
        if (state.value.identity?.user?.isAdmin != true) return
        val body = state.value.settings.json()
        requestGrant(
            PinPurpose.SETTINGS,
            OperationRequest(OperationAction.SETTINGS_UPDATE),
        ) { grant ->
            command(
                "/api/settings",
                body = body,
                operationGrant = grant,
                after = { settingsDirty = false },
            )
        }
    }

    fun clearError() {
        mutableState.update { it.copy(error = null) }
    }

    fun startOwnershipTransfer(pin: CharArray) {
        val owner = state.value.identity?.user
        if (
            owner?.isOwner != true ||
                api == null ||
                state.value.busy ||
                state.value.exportMaterial != null
        ) {
            pin.fill('\u0000')
            if (state.value.exportMaterial != null) {
                mutableState.update { it.copy(error = text(R.string.export_material_pending)) }
            }
            return
        }
        pendingTransfer?.let { saved ->
            pin.fill('\u0000')
            val writeScope =
                runCatching { store.loadPairings().writeScope }.getOrElse {
                    mutableState.update {
                        state -> state.copy(error = text(R.string.core_pairing_unavailable))
                    }
                    return
                }
            runPendingTransfer(saved, writeScope)
            return
        }
        if (
            pin.size != 6 ||
                pin.any { it !in '0'..'9' } ||
                state.value.phase != ConnectionPhase.CONNECTED
        ) {
            pin.fill('\u0000')
            mutableState.update { it.copy(error = text(R.string.commissioning_invalid)) }
            return
        }
        val pending = commissioning.newTransfer(String(pin))
        pin.fill('\u0000')
        val writeScope = store.beginWriteScope()
        val saved = runCatching { store.savePendingTransfer(pending, writeScope) }
        if (!saved.getOrDefault(false)) {
            mutableState.update { it.copy(error = text(R.string.core_pairing_pending_save_failed)) }
            return
        }
        pendingTransfer = pending
        mutableState.update { it.copy(transferPending = true) }
        runPendingTransfer(pending, writeScope)
    }

    private fun runPendingTransfer(pending: PendingTransfer, writeScope: Long) {
        val client = api ?: return
        val currentPairing = pairing ?: return
        val epoch = generation
        mutableState.update { it.copy(busy = true, error = null) }
        viewModelScope.launch {
            try {
                val material =
                    commissioning.startTransfer(client, currentPairing, pending, writeScope)
                if (epoch != generation) return@launch
                pendingTransfer = null
                mutableState.update {
                    it.copy(exportMaterial = material, transferPending = false)
                }
            } catch (error: Exception) {
                if (epoch == generation) {
                    if (error.isDefinitiveTransferFailure()) {
                        runCatching { store.discardPendingTransfer(writeScope) }
                        pendingTransfer = null
                        mutableState.update { it.copy(transferPending = false) }
                    }
                    mutableState.update {
                        it.copy(error = errorText(error, R.string.transfer_failed))
                    }
                }
            } finally {
                if (epoch == generation) mutableState.update { it.copy(busy = false) }
            }
        }
    }

    fun exportMaterialSaved() {
        val writeScope = store.beginWriteScope()
        val cleared = runCatching { store.clearExportMaterial(writeScope) }
        if (cleared.getOrDefault(false)) {
            mutableState.update { it.copy(exportMaterial = null) }
        } else {
            mutableState.update { it.copy(error = text(R.string.export_failed)) }
        }
    }

    fun materialExportFailed() {
        mutableState.update { it.copy(error = text(R.string.export_write_failed)) }
    }

    fun cameraError(message: String) {
        stopSession(restorePrompt = true)
        mutableState.update { it.copy(error = message) }
    }

    fun startEnrollment(userId: String, source: CameraSource) {
        if (state.value.identity?.user?.isAdmin != true) return
        val target = state.value.users.singleOrNull { it.id == userId } ?: return
        if (target.isOwner && state.value.identity?.user?.isOwner != true) return
        if (source == CameraSource.PHONE && !state.value.capabilities.clientCamera) return
        if (source == CameraSource.DEVICE && !state.value.capabilities.deviceCamera) return
        requestGrant(
            PinPurpose.ENROLL,
            OperationRequest(OperationAction.ENROLLMENT_START, userId),
        ) { grant ->
            startSession(
                "enroll",
                CaptureFlow.ENROLL,
                source,
                JSONObject().put("user_id", userId),
                grant,
            )
        }
    }

    private fun startDeviceScan(purpose: String, operationGrant: String) {
        if (purpose !in setOf("unlock", "ignition")) return
        if (!state.value.canControlActuators) return
        if (!state.value.capabilities.deviceCamera) return
        val expectedUserId = state.value.identity?.user?.id ?: return
        stopPrompt()
        val body = JSONObject().put("purpose", purpose)
        body.put("expected_user_id", expectedUserId)
        val flow =
            if (purpose == "ignition") CaptureFlow.VERIFY_IGNITION else CaptureFlow.VERIFY_UNLOCK
        startSession("scan", flow, CameraSource.DEVICE, body, operationGrant)
    }

    private fun startSession(
        kind: String,
        flow: CaptureFlow,
        source: CameraSource,
        body: JSONObject,
        operationGrant: String,
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
        mutableState.update { it.startCapture(flow, source) }
        viewModelScope.launch {
            var created: ActiveSession? = null
            try {
                // Keep the start response so a background transition can cancel the exact created
                // session.
                val result =
                    withContext(NonCancellable) {
                        val response =
                            client.json(
                                "/api/$kind/start",
                                "POST",
                                body,
                                operationGrant,
                            )
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
                    started.cancelRemote()
                    return@launch
                }
                session = started
                applySession(result, started)
                if (session != null) startPolling(started)
            } catch (error: Exception) {
                created?.cancelRemote()
                if (token == sessionGeneration) {
                    session = null
                    mutableState.update {
                        it.copy(
                            busy = false,
                            captureFlow = CaptureFlow.NONE,
                            error = errorText(error, R.string.core_operation_failed),
                        )
                    }
                    if (
                        error is TlsIdentityException ||
                            (error is ApiException && error.statusCode == 401)
                    )
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
        val epoch = generation
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
                if (session == active) {
                    if (error is TlsIdentityException) failConnection(error, epoch)
                    else cameraError(errorText(error, R.string.core_session_disconnected))
                }
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
                if (session == active) {
                    if (error is TlsIdentityException) failConnection(error, generation)
                    else cameraError(errorText(error, R.string.core_upload_failed))
                }
            }
        }
    }

    private fun applySession(result: JSONObject, active: ActiveSession) {
        if (session != active || active.generation != sessionGeneration) return
        val transition =
            state.value.withSessionResult(
                result,
                active.kind,
                active.flow,
                text(R.string.session_failed_retry),
            )
        mutableState.value = transition.state
        if (transition.terminal) {
            session = null
            if (transition.phase == "granted" && active.flow == CaptureFlow.VERIFY_UNLOCK) {
                startPrompt()
            }
            if (transition.phase != "granted" && active.flow == CaptureFlow.VERIFY_IGNITION) {
                startPrompt()
            }
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
            if (epoch == generation && foreground && state.value.unlockOwner != null) refresh()
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
                active.cancelRemote()
            }
        }
        val canRestore =
            restorePrompt &&
                wasIgnition &&
                foreground &&
                state.value.phase == ConnectionPhase.CONNECTED
        if (canRestore) startPrompt()
    }

    override fun onCleared() {
        pinProtection.cancel()
        stopSession()
        super.onCleared()
    }
}
