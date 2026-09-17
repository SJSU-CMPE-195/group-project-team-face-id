package com.bass.app

import android.content.Context
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Owns one PIN challenge and releases only its original pending action. */
class PinProtection(
    private val context: Context,
    private val scope: CoroutineScope,
    private val store: PinStore,
    private val publish: (PinPrompt?) -> Unit,
) {
    private var nextId = 0L
    private var prompt: PinPrompt? = null
    private var pendingAction: (() -> Unit)? = null
    private var firstPin: CharArray? = null
    private var work: Job? = null
    private var cooldown: Job? = null

    fun isConfigured(): Boolean = store.isConfigured()

    fun request(purpose: PinPurpose, onVerified: () -> Unit) {
        if (prompt != null) return
        val stage = if (store.isConfigured()) PinStage.VERIFY else PinStage.CREATE
        nextId++
        pendingAction = onVerified
        update(PinPrompt(id = nextId, stage = stage, purpose = purpose))
    }

    fun cancel() {
        nextId++
        pendingAction = null
        clearFirstPin()
        work?.cancel()
        work = null
        cooldown?.cancel()
        cooldown = null
        prompt = null
        publish(null)
    }

    fun submit(pin: CharArray) {
        val current = prompt
        if (current == null || current.busy || current.lockedSeconds > 0) {
            pin.fill('\u0000')
            return
        }
        val valid = pin.size == 6 && pin.all { it in '0'..'9' }
        if (!valid) {
            pin.fill('\u0000')
            showError(current, context.getString(R.string.pin_invalid))
            return
        }
        if (current.stage == PinStage.CREATE) {
            clearFirstPin()
            firstPin = pin.copyOf()
            pin.fill('\u0000')
            update(current.copy(stage = PinStage.CONFIRM, error = null))
            return
        }
        if (current.stage == PinStage.CONFIRM) {
            val matches = firstPin?.contentEquals(pin) == true
            clearFirstPin()
            if (!matches) {
                pin.fill('\u0000')
                showError(
                    current.copy(stage = PinStage.CREATE),
                    context.getString(R.string.pin_mismatch),
                )
                return
            }
        }

        update(current.copy(busy = true, error = null))
        work = scope.launch {
            try {
                if (current.stage == PinStage.CONFIRM) {
                    val prepared = withContext(Dispatchers.Default) { store.prepare(pin) }
                    if (prompt?.id != current.id) return@launch
                    // Persist only after confirmation and while the same action is pending.
                    store.save(prepared)
                    complete(current.id)
                } else {
                    val result = withContext(Dispatchers.Default) { store.verify(pin) }
                    if (prompt?.id != current.id) return@launch
                    when (result) {
                        PinCheck.Success -> complete(current.id)
                        is PinCheck.Wrong ->
                            showError(
                                current,
                                context.getString(R.string.pin_wrong, result.remainingAttempts),
                            )
                        is PinCheck.Locked -> startCooldown(current, result.remainingSeconds)
                    }
                }
            } catch (error: CancellationException) {
                throw error
            } catch (_: Exception) {
                if (prompt?.id == current.id) {
                    showError(current, context.getString(R.string.pin_storage_error))
                }
            } finally {
                pin.fill('\u0000')
            }
        }
        work?.invokeOnCompletion { pin.fill('\u0000') }
    }

    private fun complete(id: Long) {
        if (prompt?.id != id) return
        val action = pendingAction
        pendingAction = null
        clearFirstPin()
        prompt = null
        publish(null)
        action?.invoke()
    }

    private fun startCooldown(current: PinPrompt, seconds: Int) {
        cooldown?.cancel()
        cooldown = scope.launch {
            for (remaining in seconds downTo 1) {
                if (prompt?.id != current.id) return@launch
                update(
                    current.copy(
                        busy = false,
                        error = null,
                        lockedSeconds = remaining,
                        inputRevision = current.inputRevision + 1,
                    )
                )
                delay(1000)
            }
            if (prompt?.id == current.id) {
                update(current.copy(busy = false, inputRevision = current.inputRevision + 1))
            }
        }
    }

    private fun showError(current: PinPrompt, message: String) {
        update(
            current.copy(
                busy = false,
                error = message,
                inputRevision = current.inputRevision + 1,
            )
        )
    }

    private fun clearFirstPin() {
        firstPin?.fill('\u0000')
        firstPin = null
    }

    private fun update(value: PinPrompt) {
        prompt = value
        publish(value)
    }
}
