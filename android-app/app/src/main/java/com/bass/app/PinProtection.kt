package com.bass.app

import android.content.Context
import android.os.SystemClock
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/** Holds one remote PIN challenge and releases only its bound verified result. */
internal class PinProtection(
    private val context: Context,
    private val scope: CoroutineScope,
    private val publish: (PinPrompt?) -> Unit,
) {
    private var nextId = 0L
    private var prompt: PinPrompt? = null
    private var verifier: (suspend (CharArray) -> PinVerification)? = null
    private var pendingAction: ((String) -> Unit)? = null
    private var onCancelled: (() -> Unit)? = null
    private var confirmation: PinConfirmation? = null
    private var verifiedGrant: PinVerification? = null
    private var work: Job? = null

    fun request(
        purpose: PinPurpose,
        verify: suspend (CharArray) -> PinVerification,
        confirmation: PinConfirmation? = null,
        onCancelled: () -> Unit = {},
        onVerified: (String) -> Unit,
    ) {
        if (prompt != null) {
            confirmation?.pin?.fill(CLEARED_PIN_CHARACTER)
            onCancelled()
            return
        }
        nextId++
        verifier = verify
        pendingAction = onVerified
        this.onCancelled = onCancelled
        this.confirmation = confirmation
        update(PinPrompt(id = nextId, stage = PinStage.VERIFY, purpose = purpose))
    }

    fun cancel() {
        nextId++
        verifier = null
        pendingAction = null
        confirmation?.pin?.fill(CLEARED_PIN_CHARACTER)
        confirmation = null
        verifiedGrant = null
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

        if (current.stage == PinStage.CONFIRM_NEW_USER) {
            confirmNewUserPin(current, pin)
            return
        }

        update(current.copy(busy = true, error = null))
        work =
            scope.launch {
                try {
                    val grant = verify(pin)
                    if (prompt?.id == current.id) {
                        val expected = confirmation
                        if (expected == null) {
                            complete(current.id, grant.value)
                        } else {
                            verifiedGrant = grant
                            update(
                                current.copy(
                                    stage = PinStage.CONFIRM_NEW_USER,
                                    confirmationName = expected.name,
                                    busy = false,
                                    inputRevision = current.inputRevision + 1,
                                )
                            )
                        }
                    }
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
        val confirmedPin = confirmation?.pin
        verifier = null
        pendingAction = null
        onCancelled = null
        confirmation = null
        verifiedGrant = null
        prompt = null
        publish(null)
        try {
            action?.invoke(grant)
        } finally {
            confirmedPin?.fill(CLEARED_PIN_CHARACTER)
        }
    }

    private fun confirmNewUserPin(current: PinPrompt, pin: CharArray) {
        val expected = confirmation?.pin
        val grant = verifiedGrant
        when {
            expected == null || grant == null -> {
                pin.fill(CLEARED_PIN_CHARACTER)
                cancel()
            }
            !pin.contentEquals(expected) -> {
                pin.fill(CLEARED_PIN_CHARACTER)
                showError(
                    current,
                    context.getString(R.string.pin_new_user_mismatch, confirmation?.name.orEmpty()),
                )
            }
            grant.expiresAtElapsedRealtime != null &&
                SystemClock.elapsedRealtime() >= grant.expiresAtElapsedRealtime -> {
                pin.fill(CLEARED_PIN_CHARACTER)
                verifiedGrant = null
                update(
                    current.copy(
                        stage = PinStage.VERIFY,
                        confirmationName = null,
                        busy = false,
                        error = context.getString(R.string.pin_approval_expired),
                        inputRevision = current.inputRevision + 1,
                    )
                )
            }
            else -> {
                pin.fill(CLEARED_PIN_CHARACTER)
                complete(current.id, grant.value)
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

    private fun update(value: PinPrompt) {
        prompt = value
        publish(value)
    }

    companion object {
        private const val PIN_DIGITS = 6
        private const val CLEARED_PIN_CHARACTER = '\u0000'
    }
}

internal data class PinConfirmation(val name: String, val pin: CharArray)

internal data class PinVerification(
    val value: String,
    val expiresAtElapsedRealtime: Long? = null,
)
