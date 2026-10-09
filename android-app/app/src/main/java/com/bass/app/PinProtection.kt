package com.bass.app

import android.content.Context
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/** Holds one PIN until its bound API job dispatches, then clears it. */
internal class PinProtection(
    private val context: Context,
    private val scope: CoroutineScope,
    private val publish: (PinPrompt?) -> Unit,
) {
    private var nextId = 0L
    private var prompt: PinPrompt? = null
    private var pendingAction: (suspend (CharArray) -> Unit)? = null
    private var onCancelled: (() -> Unit)? = null
    private var confirmation: PinConfirmation? = null
    private var pendingPin: CharArray? = null
    private var work: Job? = null

    fun request(
        purpose: PinPurpose,
        confirmation: PinConfirmation? = null,
        onCancelled: () -> Unit = {},
        action: suspend (CharArray) -> Unit,
    ) {
        if (prompt != null) {
            confirmation?.pin?.fill(CLEARED_PIN_CHARACTER)
            onCancelled()
            return
        }
        nextId++
        pendingAction = action
        this.onCancelled = onCancelled
        this.confirmation = confirmation
        update(PinPrompt(id = nextId, stage = PinStage.VERIFY, purpose = purpose))
    }

    fun cancel() {
        nextId++
        pendingAction = null
        pendingPin?.fill(CLEARED_PIN_CHARACTER)
        pendingPin = null
        confirmation?.pin?.fill(CLEARED_PIN_CHARACTER)
        confirmation = null
        onCancelled?.invoke()
        onCancelled = null
        work?.cancel()
        work = null
        prompt = null
        publish(null)
    }

    fun submit(pin: CharArray) {
        val current = prompt
        if (current == null || pendingAction == null || current.busy) {
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
        if (confirmation == null) {
            runAction(current, pin)
        } else {
            pendingPin = pin
            update(
                current.copy(
                    stage = PinStage.CONFIRM_NEW_USER,
                    confirmationName = confirmation?.name,
                    busy = false,
                    error = null,
                    inputRevision = current.inputRevision + 1,
                )
            )
        }
    }

    private fun runAction(current: PinPrompt, pin: CharArray) {
        val action = pendingAction ?: run {
            pin.fill(CLEARED_PIN_CHARACTER)
            return
        }
        update(current.copy(busy = true, error = null))
        work =
            scope.launch {
                try {
                    action(pin)
                    if (prompt?.id == current.id) complete(current.id)
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Exception) {
                    if (prompt?.id == current.id) {
                        pendingPin = null
                        showError(
                            current.copy(
                                stage = PinStage.VERIFY,
                                confirmationName = null,
                            ),
                            error.message ?: context.getString(R.string.core_operation_failed),
                        )
                    }
                } finally {
                    pin.fill(CLEARED_PIN_CHARACTER)
                }
            }
        work?.invokeOnCompletion { pin.fill(CLEARED_PIN_CHARACTER) }
    }

    private fun complete(id: Long) {
        if (prompt?.id != id) return
        pendingAction = null
        pendingPin = null
        onCancelled = null
        confirmation?.pin?.fill(CLEARED_PIN_CHARACTER)
        confirmation = null
        work = null
        prompt = null
        publish(null)
    }

    private fun confirmNewUserPin(current: PinPrompt, pin: CharArray) {
        val expected = confirmation?.pin
        val adminPin = pendingPin
        when {
            expected == null || adminPin == null -> {
                pin.fill(CLEARED_PIN_CHARACTER)
                cancel()
            }
            !pin.contentEquals(expected) -> {
                pin.fill(CLEARED_PIN_CHARACTER)
                showError(
                    current,
                    context.getString(
                        R.string.pin_new_user_mismatch,
                        confirmation?.name.orEmpty(),
                    ),
                )
            }
            else -> {
                pin.fill(CLEARED_PIN_CHARACTER)
                pendingPin = null
                runAction(current, adminPin)
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
