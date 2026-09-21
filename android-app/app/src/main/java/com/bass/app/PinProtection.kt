package com.bass.app

import android.content.Context
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/** Holds one remote PIN challenge and releases only its bound verified result. */
class PinProtection(
    private val context: Context,
    private val scope: CoroutineScope,
    private val publish: (PinPrompt?) -> Unit,
) {
    private var nextId = 0L
    private var prompt: PinPrompt? = null
    private var verifier: (suspend (CharArray) -> String)? = null
    private var pendingAction: ((String) -> Unit)? = null
    private var onCancelled: (() -> Unit)? = null
    private var work: Job? = null

    fun request(
        purpose: PinPurpose,
        verify: suspend (CharArray) -> String,
        onCancelled: () -> Unit = {},
        onVerified: (String) -> Unit,
    ) {
        if (prompt != null) {
            onCancelled()
            return
        }
        nextId++
        verifier = verify
        pendingAction = onVerified
        this.onCancelled = onCancelled
        update(PinPrompt(id = nextId, stage = PinStage.VERIFY, purpose = purpose))
    }

    fun cancel() {
        nextId++
        verifier = null
        pendingAction = null
        onCancelled?.invoke()
        onCancelled = null
        work?.cancel()
        work = null
        prompt = null
        publish(null)
    }

    fun submit(pin: CharArray) {
        val current = prompt
        val verify = verifier
        if (current == null || verify == null || current.busy) {
            pin.fill(CLEARED_PIN_CHARACTER)
            return
        }
        if (pin.size != PIN_DIGITS || pin.any { it !in '0'..'9' }) {
            pin.fill(CLEARED_PIN_CHARACTER)
            showError(current, context.getString(R.string.pin_invalid))
            return
        }

        update(current.copy(busy = true, error = null))
        work =
            scope.launch {
                try {
                    val grant = verify(pin)
                    if (prompt?.id == current.id) complete(current.id, grant)
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Exception) {
                    if (prompt?.id == current.id) {
                        showError(
                            current,
                            error.message ?: context.getString(R.string.core_operation_failed),
                        )
                    }
                } finally {
                    pin.fill(CLEARED_PIN_CHARACTER)
                }
            }
        work?.invokeOnCompletion { pin.fill(CLEARED_PIN_CHARACTER) }
    }

    private fun complete(id: Long, grant: String) {
        if (prompt?.id != id) return
        val action = pendingAction
        verifier = null
        pendingAction = null
        onCancelled = null
        prompt = null
        publish(null)
        action?.invoke(grant)
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

    private fun update(value: PinPrompt) {
        prompt = value
        publish(value)
    }

    companion object {
        private const val PIN_DIGITS = 6
        private const val CLEARED_PIN_CHARACTER = '\u0000'
    }
}
