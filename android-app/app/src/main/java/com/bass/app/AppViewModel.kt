package com.bass.app

import android.app.Application
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
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicBoolean

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
    private var pendingPairing: Pairing? = null
    private var pairingExchangeStarted = false
    private var pairing: Pairing? = null
    private var api: DeviceApi? = null
    private var generation = 0L
    private var sessionGeneration = 0L
    private var connectionJob: Job? = null
    private var pollJob: Job? = null
    private var reconnectJob: Job? = null
    private var promptJob: Job? = null
    private var foreground = false
    private var sessionLoginVerified = false
    private var commissioningSubmittedThisForeground = false
    private var session: ActiveSession? = null
    private var refreshing = false
    private val snapshotMutex = Mutex()
    private var snapshotVersion = 0L
    private var pendingStart: PendingStart? = null
    private var pendingEnrollment: PendingEnrollment? = null
    private var pendingEnrollmentJournalScope = 0L

    private fun loadSupportedPairings(): StoredPairings {
        val stored = store.loadPairings()
        val retiredCommissioning =
            stored.pendingCommissioning?.qr?.purpose == CommissioningPurpose.TRANSFER
        if (retiredCommissioning) {
            check(store.discardPendingCommissioning(stored.writeScope)) {
                text(R.string.core_pairing_clear_failed)
            }
        }
        if (stored.pendingTransfer != null) {
            check(store.discardPendingTransfer(stored.writeScope)) {
                text(R.string.core_pairing_clear_failed)
            }
        }
        val exportMaterial = stored.exportMaterial
        val retiredTransfer = exportMaterial?.fileName?.startsWith("bass-transfer-") == true
        if (retiredTransfer) {
            check(store.clearExportMaterial(stored.writeScope)) {
                text(R.string.core_pairing_clear_failed)
            }
        }
        return stored.copy(
            pendingCommissioning = stored.pendingCommissioning.takeUnless {
                retiredCommissioning
            },
            pendingTransfer = null,
            exportMaterial = exportMaterial.takeUnless { retiredTransfer },
        )
    }

    init {
        runCatching { loadSupportedPairings() }
            .onSuccess { stored ->
                pairing = stored.active
                pendingPairing = stored.pending
                pendingCommissioning = stored.pendingCommissioning
                val owner = stored.pending ?: stored.active
                pendingEnrollment = owner?.let { pairing ->
                    stored.pendingEnrollments.lastOrNull { it.belongsTo(pairing) }
                }
                pendingEnrollmentJournalScope = stored.enrollmentJournalScope
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

    fun onBluetoothPermissionDenied() {
        foreground = true
        mutableState.update {
            it.copy(
                phase =
                    if (pairing == null && pendingPairing == null && pendingCommissioning == null) {
                        ConnectionPhase.UNPAIRED
                    } else {
                        ConnectionPhase.OFFLINE
                    },
                busy = false,
                error = text(R.string.core_bluetooth_permission_required),
            )
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
        closeApi()
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

    private fun closeApi() {
        api?.close()
        api = null
    }

    fun selectTab(tab: Tab) {
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
        closeApi()
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
        if (parsed.purpose == CommissioningPurpose.TRANSFER) {
            mutableState.update { it.copy(error = text(R.string.core_invalid_qr)) }
            return
        }
        stopSession()
        pendingPairingQr = parsed
        pendingPairingInviteToken = invitation?.inviteToken.orEmpty()
        settingsDirty = false
        closeApi()
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
                    val connected = completion.connected
                    if (epoch != generation || !foreground) {
                        connected.api.close()
                        return@launch
                    }
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

        resetLocalPairingState()
    }

    private fun resetLocalPairingState() {
        pinProtection.cancel()
        generation++
        connectionJob?.cancel()
        connectionJob = null
        reconnectJob?.cancel()
        reconnectJob = null
        stopPrompt()
        stopSession()
        closeApi()
        pairing = null
        sessionLoginVerified = false
        commissioningSubmittedThisForeground = false
        pendingPairingQr = null
        pendingPairingInviteToken = ""
        pendingPairing = null
        pendingCommissioning = null
        pendingEnrollment = null
        pendingEnrollmentJournalScope = 0L
        settingsDirty = false
        clearNewUserDraft()
        mutableState.value = BassState()
    }

    fun retryConnection() {
        if (!foreground) return
        val stored =
            runCatching { loadSupportedPairings() }.getOrElse { error ->
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
        pendingEnrollment = (stored.pending ?: stored.active)?.let { owner ->
            stored.pendingEnrollments.lastOrNull { it.belongsTo(owner) }
        }
        pendingEnrollmentJournalScope = stored.enrollmentJournalScope
        mutableState.update {
            it.copy(
                exportMaterial = stored.exportMaterial,
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
        closeApi()
        mutableState.update(BassState::startConnection)
        connectionJob = viewModelScope.launch {
            try {
                val connected =
                    connector.connect(owner) {
                        if (epoch == generation) {
                            mutableState.update { it.copy(phase = ConnectionPhase.CONNECTING) }
                        }
                    }
                if (epoch != generation) {
                    connected.api.close()
                    return@launch
                }
                if (connected.pairing != owner) {
                    val saved =
                        withContext(Dispatchers.IO) {
                            store.save(connected.pairing, writeScope)
                        }
                    if (!saved) {
                        connected.api.close()
                        return@launch
                    }
                }
                if (epoch != generation) {
                    connected.api.close()
                    return@launch
                }
                pairing = connected.pairing
                api = connected.api
                applyConnected(connected)
                if (sessionLoginVerified) {
                    resumeConnected(connected, epoch)
                } else {
                    requestSessionLogin(connected, epoch) {
                        resumeConnected(connected, epoch)
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
        closeApi()
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
                    if (epoch != generation) {
                        connected.api.close()
                        return@launch
                    }
                    if (!promotePendingPairing(connected.pairing, writeScope)) {
                        connected.api.close()
                        return@launch
                    }
                    if (epoch != generation) {
                        connected.api.close()
                        return@launch
                    }
                    api = connected.api
                    applyConnected(connected)
                    if (sessionLoginVerified) {
                        resumeConnected(connected, epoch)
                    } else {
                        requestSessionLogin(connected, epoch) {
                            resumeConnected(connected, epoch)
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
            onCancelled = {
                if (isCurrentConnection(connected, epoch)) {
                    sessionLoginVerified = false
                    generation++
                    closeApi()
                    mutableState.update {
                        it.copy(
                            phase = ConnectionPhase.SIGN_IN_REQUIRED,
                            status = null,
                            users = emptyList(),
                            pairedDevices = emptyList(),
                            faceStatus = FaceStatus(),
                            logs = emptyList(),
                            inviteMaterial = null,
                            unlockOwner = null,
                            busy = false,
                            error = null,
                        )
                    }
                }
            },
        ) { pin ->
            val identity =
                try {
                    DeviceAuth.sessionLogin(
                        connected.api,
                        pin,
                        connected.pairing.identity,
                    )
                } catch (error: ApiException) {
                    if (error.errorCode == "invalid_credentials") {
                        throw IllegalArgumentException(text(R.string.sign_in_invalid_pin))
                    }
                    throw error
                }
            if (
                isCurrentConnection(connected, epoch) &&
                    identity.deviceId == connected.pairing.identity.deviceId
            ) {
                sessionLoginVerified = true
                onAuthenticated()
            }
        }
    }

    private fun resumeConnected(
        connected: ConnectedDevice,
        epoch: Long,
        exportMaterial: ExportMaterial? = state.value.exportMaterial,
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
                    recoverPendingEnrollment(connected.api, epoch)
                    if (!isCurrentConnection(connected, epoch)) return@launch
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
                    if (error is ApiException && error.statusCode == 429) {
                        delay(error.retryDelayMillis())
                        continue
                    }
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
        closeApi()
        val unauthorized = error is ApiException && error.statusCode == 401
        val policyDenied = error is ApiException && error.statusCode == 403
        val identityFailure = error is TlsIdentityException || error is DeviceIdentityException
        val clearProtectedState = unauthorized || identityFailure
        sessionLoginVerified = false
        mutableState.update {
            it.copy(
                phase =
                    when {
                        unauthorized -> ConnectionPhase.UNAUTHORIZED
                        policyDenied -> ConnectionPhase.ERROR
                        identityFailure -> ConnectionPhase.ERROR
                        else -> ConnectionPhase.OFFLINE
                    },
                busy = false,
                error = errorText(error, R.string.core_offline),
                unlockOwner = null,
                identity = if (clearProtectedState) null else it.identity,
                status = if (clearProtectedState) null else it.status,
                users = if (clearProtectedState) emptyList() else it.users,
                pairedDevices = if (clearProtectedState) emptyList() else it.pairedDevices,
                faceStatus = if (clearProtectedState) FaceStatus() else it.faceStatus,
                logs = if (clearProtectedState) emptyList() else it.logs,
                inviteMaterial = if (clearProtectedState) null else it.inviteMaterial,
            )
        }
        if (clearProtectedState) clearNewUserDraft()
        if (
            !unauthorized &&
                !policyDenied &&
                !identityFailure &&
                error !is BluetoothPermissionException &&
                error !is BluetoothUnavailableException &&
                foreground
        ) {
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
                if (error is ApiException && error.statusCode == 429) {
                    mutableState.update { it.copy(error = error.message) }
                } else {
                    failConnection(error, epoch)
                }
            } finally {
                refreshing = false
            }
        }
    }

    private suspend fun loadSnapshot(client: DeviceApi, epoch: Long) {
        if (!snapshotMutex.tryLock()) return
        try {
            val snapshot = ++snapshotVersion
            val savedIdentity = pairing?.identity
                ?: throw DeviceIdentityException(text(R.string.core_identity_mismatch))
            val loaded = client.readSnapshot(savedIdentity)
            if (epoch != generation || snapshot != snapshotVersion) return
            val previousUser = state.value.identity?.user
            val roleChanged =
                previousUser != null &&
                    (previousUser.isAdmin != loaded.identity.user.isAdmin ||
                        previousUser.isOwner != loaded.identity.user.isOwner)
            if (roleChanged) {
                pinProtection.cancel()
                stopSession()
                clearNewUserDraft()
            }
            mutableState.update { it.withSnapshot(loaded) }
        } finally {
            snapshotMutex.unlock()
        }
    }

    private suspend fun ExclusiveDeviceApi.command(
        path: String,
        method: String = "POST",
        body: JSONObject? = JSONObject(),
        operationGrant: String? = null,
        after: () -> Unit = {},
    ) {
        val result = json(path, method, body, operationGrant)
        check(result.optBoolean("ok", true)) {
            result.optString("error", text(R.string.core_operation_failed))
        }
        after()
    }

    fun unlock() {
        if (state.value.phase != ConnectionPhase.CONNECTED) return
        val userId = state.value.identity?.user?.id ?: return
        requestGrant(
            PinPurpose.UNLOCK,
            OperationRequest(OperationAction.SCAN_UNLOCK, userId),
            refreshAfter = false,
            manageBusy = false,
        ) { grant ->
            startDeviceScan("unlock", grant)
        }
    }

    fun verifyIgnition() {
        val userId = state.value.identity?.user?.id ?: return
        requestGrant(
            PinPurpose.IGNITION,
            OperationRequest(OperationAction.SCAN_IGNITION, userId),
            refreshAfter = false,
            manageBusy = false,
        ) { grant ->
            startDeviceScan("ignition", grant)
        }
    }

    private fun requestGrant(
        purpose: PinPurpose,
        operation: OperationRequest,
        confirmation: PinConfirmation? = null,
        onCancelled: () -> Unit = {},
        beforeGrant: suspend () -> Unit = {},
        onBeforeMutationFailure: suspend () -> Unit = {},
        refreshAfter: Boolean = true,
        manageBusy: Boolean = true,
        action: suspend ExclusiveDeviceApi.(String) -> Unit,
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
        val actor = state.value.identity?.user
        val actorId = actor?.id
        val actorIsAdmin = actor?.isAdmin
        val actorIsOwner = actor?.isOwner
        val tab = state.value.selectedTab
        val apiGeneration = client.generation
        val operationState = Any()
        val cleanupClaimed = AtomicBoolean(false)
        var callerCancelled = false
        var workStarted = false
        var mutationStarted = false
        mutableState.update { it.copy(error = null) }
        pinProtection.request(
            purpose = purpose,
            confirmation = confirmation,
            onCancelled = onCancelled,
        ) { pin ->
            val sameContext =
                foreground &&
                    generation == epoch &&
                    pairing == owner &&
                    state.value.identity?.user?.id == actorId &&
                    state.value.identity?.user?.isAdmin == actorIsAdmin &&
                    state.value.identity?.user?.isOwner == actorIsOwner &&
                    state.value.selectedTab == tab &&
                    (!operation.action.requiresActuatorControl ||
                        state.value.canControlActuators)
            check(sameContext) { text(R.string.core_operation_failed) }
            try {
                beforeGrant()
                if (manageBusy) mutableState.update { it.copy(busy = true, error = null) }
                client.exclusive {
                    val mayRequestGrant =
                        synchronized(operationState) {
                            workStarted = true
                            !callerCancelled
                        }
                    try {
                        val grantContextCurrent =
                            mayRequestGrant &&
                                foreground &&
                                generation == epoch &&
                                api === client &&
                                client.generation == apiGeneration &&
                                pairing == owner &&
                                state.value.identity?.user?.id == actorId &&
                                state.value.identity?.user?.isAdmin == actorIsAdmin &&
                                state.value.identity?.user?.isOwner == actorIsOwner &&
                                state.value.selectedTab == tab &&
                                (!operation.action.requiresActuatorControl ||
                                    state.value.canControlActuators)
                        if (!grantContextCurrent) return@exclusive
                        val body =
                            JSONObject()
                                .put("pin", String(pin))
                                .put("action", operation.action.wireValue)
                                .put("target_user_id", operation.targetUserId)
                        val response = json("/api/operation-grants", "POST", body)
                        val grant = response.getString("grant_token")
                        require(grant.matches(Regex("[A-Za-z0-9_-]{32,512}"))) {
                            "Invalid operation grant"
                        }
                        val expiresIn = response.getInt("expires_in")
                        require(expiresIn in 1..30) { "Invalid operation grant lifetime" }
                        val connectionReusable = client.isReusable
                        val mutationContextCurrent =
                            connectionReusable &&
                                foreground &&
                                generation == epoch &&
                                api === client &&
                                client.generation == apiGeneration &&
                                pairing == owner &&
                                state.value.identity?.user?.id == actorId &&
                                state.value.identity?.user?.isAdmin == actorIsAdmin &&
                                state.value.identity?.user?.isOwner == actorIsOwner &&
                                state.value.selectedTab == tab &&
                                (!operation.action.requiresActuatorControl ||
                                    state.value.canControlActuators)
                        val mayMutate =
                            synchronized(operationState) {
                                if (callerCancelled || !mutationContextCurrent) {
                                    false
                                } else {
                                    mutationStarted = true
                                    true
                                }
                            }
                        if (mayMutate) {
                            action(grant)
                        } else if (!connectionReusable) {
                            throw BleLinkUnusableException("BLE connection is not reusable")
                        }
                    } finally {
                        val workerOwnsCleanup =
                            synchronized(operationState) { !mutationStarted }
                        if (workerOwnsCleanup && cleanupClaimed.compareAndSet(false, true)) {
                            withContext(NonCancellable) { onBeforeMutationFailure() }
                        }
                    }
                }
                if (refreshAfter && epoch == generation) loadSnapshot(client, epoch)
            } catch (error: CancellationException) {
                synchronized(operationState) { callerCancelled = true }
                throw error
            } catch (error: Exception) {
                if (
                    epoch == generation &&
                        (error is TlsIdentityException ||
                            error is DeviceIdentityException ||
                            error is BleLinkUnusableException ||
                            error is ApiException && error.statusCode == 401)
                ) {
                    failConnection(error, epoch)
                }
                throw IllegalStateException(errorText(error, R.string.core_operation_failed), error)
            } finally {
                val callerOwnsCleanup =
                    synchronized(operationState) { !workStarted && !mutationStarted }
                if (callerOwnsCleanup && cleanupClaimed.compareAndSet(false, true)) {
                    withContext(NonCancellable) { onBeforeMutationFailure() }
                }
                if (manageBusy && epoch == generation) {
                    mutableState.update { it.copy(busy = false) }
                }
            }
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
            refreshAfter = false,
        ) { grant ->
            val response =
                json(
                    "/api/pairing-invites",
                    "POST",
                    JSONObject().put("user_id", userId),
                    grant,
                )
            val material = pairingInviteMaterial(response, target.id, currentPairing)
            if (pairing == currentPairing) {
                mutableState.update { it.copy(inviteMaterial = material) }
            }
        }
    }

    fun setUserRole(userId: String, isAdmin: Boolean) {
        if (state.value.identity?.user?.isOwner != true) return
        val target = state.value.users.singleOrNull { it.id == userId } ?: return
        if (target.isOwner || target.isAdmin == isAdmin) return
        requestGrant(
            PinPurpose.USER_ROLE,
            OperationRequest(OperationAction.USER_ROLE, userId),
        ) { grant ->
            command(
                "/api/users/${DeviceApi.encoded(userId)}/role",
                "PATCH",
                JSONObject().put("is_admin", isAdmin),
                grant,
            )
        }
    }

    fun revokeDevice(deviceId: String) {
        val actor = state.value.identity?.user ?: return
        val target = state.value.pairedDevices.singleOrNull { it.id == deviceId } ?: return
        if (!actor.isAdmin || !target.active || (target.userIsOwner && !actor.isOwner)) return
        val revokingThisPhone = state.value.identity?.deviceId == deviceId
        requestGrant(
            PinPurpose.USER_ADMIN,
            OperationRequest(OperationAction.DEVICE_REVOKE, deviceId),
        ) { grant ->
            command(
                "/api/devices/${DeviceApi.encoded(deviceId)}/revoke",
                operationGrant = grant,
                after = {
                    if (revokingThisPhone) {
                        store.clear()
                        resetLocalPairingState()
                    }
                },
            )
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

    private fun cameraError(message: String) {
        stopSession(restorePrompt = true)
        mutableState.update { it.copy(error = message) }
    }

    fun startEnrollment(userId: String) {
        if (state.value.identity?.user?.isAdmin != true) return
        pendingEnrollment?.takeIf { it.unresolved }?.let {
            val client = api ?: return
            val epoch = generation
            viewModelScope.launch { recoverPendingEnrollment(client, epoch) }
            return
        }
        val target = state.value.users.singleOrNull { it.id == userId } ?: return
        if (target.isOwner && state.value.identity?.user?.isOwner != true) return
        if (!state.value.capabilities.deviceCamera) return
        val owner = state.value.identity ?: return
        val pending =
            PendingEnrollment(
                requestId = DeviceAuth.requestId(),
                hostDeviceId = pairing?.hostDeviceId ?: return,
                userId = userId,
                ownerDeviceId = owner.deviceId,
                ownerUserId = owner.user.id,
            )
        var journalScope = 0L
        var journalSaved = false
        requestGrant(
            PinPurpose.ENROLL,
            OperationRequest(OperationAction.ENROLLMENT_START, userId),
            beforeGrant = {
                withContext(NonCancellable) {
                    journalScope = store.enrollmentJournalScope()
                    val saved =
                        withContext(Dispatchers.IO) {
                            store.savePendingEnrollment(pending, journalScope)
                        }
                    check(saved) { text(R.string.core_pairing_pending_save_failed) }
                    pendingEnrollment = pending
                    pendingEnrollmentJournalScope = journalScope
                    journalSaved = true
                }
            },
            onBeforeMutationFailure = {
                if (journalSaved) clearEnrollmentJournal(pending, journalScope)
            },
            refreshAfter = false,
            manageBusy = false,
        ) { grant ->
            startEnrollmentSession(pending, journalScope, grant)
        }
    }

    private suspend fun ExclusiveDeviceApi.startEnrollmentSession(
        pending: PendingEnrollment,
        journalScope: Long,
        operationGrant: String,
    ) {
        val client = api ?: error(text(R.string.core_offline))
        val active = prepareSession(CaptureFlow.ENROLL)
        try {
            val response =
                json(
                    "/api/enroll/start",
                    "POST",
                    JSONObject()
                        .put("request_id", pending.requestId)
                        .put("user_id", pending.userId)
                        .put("source", "device_camera"),
                    operationGrant,
                    expectedStatus = 202,
                )
            val saved = saveEnrollmentAcceptance(pending, response, journalScope)
            val started =
                ActiveSession(
                    id = saved.sessionId ?: error("Enrollment receipt has no session"),
                    kind = "enroll",
                    api = client,
                    generation = active.sessionGeneration,
                    flow = CaptureFlow.ENROLL,
                    requestId = saved.requestId,
                )
            completeSessionStart(
                active,
                started,
                JSONObject()
                    .put("request_id", saved.requestId)
                    .put("session_id", saved.sessionId)
                    .put("state", saved.state),
            )
        } catch (error: Exception) {
            if (error is ApiException && error.statusCode in setOf(400, 401, 403)) {
                clearEnrollmentJournal(pending, journalScope)
            }
            failSessionStart(active, error)
            throw error
        }
    }

    private suspend fun ExclusiveDeviceApi.startDeviceScan(
        purpose: String,
        operationGrant: String,
    ) {
        if (purpose !in setOf("unlock", "ignition")) return
        if (!state.value.canControlActuators) return
        if (!state.value.capabilities.deviceCamera) return
        val expectedUserId = state.value.identity?.user?.id ?: return
        val client = api ?: error(text(R.string.core_offline))
        stopPrompt()
        val flow =
            if (purpose == "ignition") CaptureFlow.VERIFY_IGNITION else CaptureFlow.VERIFY_UNLOCK
        val active = prepareSession(flow)
        try {
            val response =
                json(
                    "/api/scan/start",
                    "POST",
                    JSONObject()
                        .put("purpose", purpose)
                        .put("expected_user_id", expectedUserId)
                        .put("source", "device_camera"),
                    operationGrant,
                )
            val started =
                ActiveSession(
                    response.getString("session_id"),
                    "scan",
                    client,
                    active.sessionGeneration,
                    flow,
                )
            completeSessionStart(active, started, response)
        } catch (error: Exception) {
            failSessionStart(active, error)
            throw error
        }
    }

    private fun prepareSession(flow: CaptureFlow): PendingStart {
        check(foreground && state.value.phase == ConnectionPhase.CONNECTED) {
            text(R.string.core_offline)
        }
        check(state.value.capabilities.deviceCamera) { text(R.string.core_operation_failed) }
        sessionGeneration++
        snapshotVersion++
        val token = sessionGeneration
        val epoch = generation
        val pending = PendingStart(epoch, token)
        pendingStart = pending
        mutableState.update { it.startCapture(flow, CameraSource.DEVICE) }
        return pending
    }

    private fun completeSessionStart(
        pending: PendingStart,
        started: ActiveSession,
        result: JSONObject,
    ) {
        pendingStart = pendingStart.takeUnless { it == pending }
        if (
            pending.deviceGeneration != generation ||
                pending.sessionGeneration != sessionGeneration ||
                !foreground
        ) {
            viewModelScope.launch(NonCancellable) { started.cancelRemote() }
            return
        }
        session = started
        applySession(result, started)
        if (session != null) startPolling(started)
    }

    private fun failSessionStart(pending: PendingStart, error: Exception) {
        if (pendingStart == pending) pendingStart = null
        if (pending.sessionGeneration == sessionGeneration) {
            session = null
            mutableState.update {
                it.copy(
                    busy = false,
                    captureFlow = CaptureFlow.NONE,
                    error = errorText(error, R.string.core_operation_failed),
                )
            }
        }
    }

    private suspend fun saveEnrollmentAcceptance(
        pending: PendingEnrollment,
        acceptance: JSONObject,
        journalScope: Long,
    ): PendingEnrollment =
        persistEnrollmentJournal(pending.withAcceptance(acceptance), journalScope)

    private suspend fun saveEnrollmentReceipt(
        pending: PendingEnrollment,
        receipt: JSONObject,
        journalScope: Long,
    ): PendingEnrollment =
        persistEnrollmentJournal(pending.withReceipt(receipt), journalScope)

    private suspend fun persistEnrollmentJournal(
        updated: PendingEnrollment,
        journalScope: Long,
    ): PendingEnrollment =
        withContext(NonCancellable) {
            val saved =
                withContext(Dispatchers.IO) {
                    store.savePendingEnrollment(updated, journalScope)
                }
            check(saved) { text(R.string.core_pairing_save_failed) }
            withContext(Dispatchers.Main.immediate) {
                val currentPairing = pairing
                if (
                    currentPairing != null &&
                        updated.belongsTo(currentPairing) &&
                        pendingEnrollment?.sameRequest(updated) == true
                ) {
                    pendingEnrollment = updated
                    pendingEnrollmentJournalScope = journalScope
                }
            }
            updated
        }

    private suspend fun clearEnrollmentJournal(requestId: String) {
        val pending = pendingEnrollment?.takeIf { it.requestId == requestId } ?: return
        clearEnrollmentJournal(pending, pendingEnrollmentJournalScope)
    }

    private suspend fun clearEnrollmentJournal(
        pending: PendingEnrollment,
        journalScope: Long,
    ) {
        val cleared =
            withContext(NonCancellable + Dispatchers.IO) {
                store.clearPendingEnrollment(
                    pending,
                    journalScope,
                )
            }
        if (cleared && pendingEnrollment?.sameRequest(pending) == true) pendingEnrollment = null
    }

    private suspend fun recoverPendingEnrollment(client: DeviceApi, epoch: Long) {
        val pending = pendingEnrollment ?: return
        val currentPairing = pairing ?: return
        if (!pending.belongsTo(currentPairing)) return
        val receipt =
            try {
                client.json(
                    "/api/enroll/requests/${DeviceApi.encoded(pending.requestId)}"
                )
            } catch (error: ApiException) {
                if (error.statusCode != 404) throw error
                val unknown = pending.copy(state = PendingEnrollment.UNKNOWN)
                val saved =
                    withContext(Dispatchers.IO) {
                        store.savePendingEnrollment(unknown, pendingEnrollmentJournalScope)
                    }
                check(saved) { text(R.string.core_pairing_save_failed) }
                val currentPairing = pairing
                if (
                    epoch == generation &&
                        currentPairing != null &&
                        unknown.belongsTo(currentPairing) &&
                        pendingEnrollment?.sameRequest(unknown) == true
                ) {
                    pendingEnrollment = unknown
                    mutableState.update {
                        it.copy(error = text(R.string.core_enrollment_result_unknown))
                    }
                }
                return
            }
        val saved =
            saveEnrollmentReceipt(
                pending,
                receipt,
                pendingEnrollmentJournalScope,
            )
        if (epoch != generation || !foreground || !saved.belongsTo(pairing ?: return)) {
            return
        }
        val projection = receipt.enrollmentProjection()
        if (saved.state in setOf("accepted", "active")) {
            sessionGeneration++
            val active =
                ActiveSession(
                    id = checkNotNull(saved.sessionId),
                    kind = "enroll",
                    api = client,
                    generation = sessionGeneration,
                    flow = CaptureFlow.ENROLL,
                    requestId = saved.requestId,
                )
            session = active
            mutableState.update { it.startCapture(CaptureFlow.ENROLL, CameraSource.DEVICE) }
            applySession(projection, active)
            if (session != null) startPolling(active)
        } else {
            val transition =
                state.value.withSessionResult(
                    projection,
                    "enroll",
                    CaptureFlow.ENROLL,
                    text(R.string.session_failed_retry),
                )
            mutableState.value = transition.state
            clearEnrollmentJournal(saved.requestId)
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
                        try {
                            if (active.requestId == null) {
                                active.api.json(
                                    "/api/${active.kind}/status?session_id=" +
                                        DeviceApi.encoded(active.id)
                                )
                            } else {
                                val receipt =
                                    active.api.json(
                                        "/api/enroll/requests/${DeviceApi.encoded(active.requestId)}"
                                    )
                                saveEnrollmentReceipt(
                                    checkNotNull(pendingEnrollment),
                                    receipt,
                                    pendingEnrollmentJournalScope,
                                )
                                receipt.enrollmentProjection()
                            }
                        } catch (error: ApiException) {
                            if (error.statusCode != 429) throw error
                            delay(error.retryDelayMillis())
                            continue
                        }
                    if (session == active) {
                        applySession(result, active)
                        if (active.requestId != null && session == null) {
                            clearEnrollmentJournal(active.requestId)
                        }
                    }
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
                if (active.requestId != null && foreground) {
                    recoverPendingEnrollment(active.api, generation)
                }
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
        closeApi()
        super.onCleared()
    }
}

private fun ApiException.retryDelayMillis(): Long =
    (retryAfterSeconds ?: 1L).coerceIn(1L, 60L) * 1000L
