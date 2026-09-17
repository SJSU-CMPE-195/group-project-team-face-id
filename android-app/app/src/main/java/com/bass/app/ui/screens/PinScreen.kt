package com.bass.app.ui.screens

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.asPaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.bass.app.PinPrompt
import com.bass.app.PinPurpose
import com.bass.app.PinStage
import com.bass.app.R

private const val PIN_LENGTH = 6
private const val CLEARED_PIN_CHARACTER = '\u0000'

@Composable
fun PinScreen(
    prompt: PinPrompt,
    onSubmit: (CharArray) -> Unit,
    onCancel: () -> Unit,
) {
    var pin by
        remember(prompt.id, prompt.stage, prompt.inputRevision) {
            mutableStateOf(CharArray(0))
        }
    val inputEnabled = !prompt.busy && prompt.lockedSeconds <= 0
    fun cancel() {
        pin.fill(CLEARED_PIN_CHARACTER)
        pin = CharArray(0)
        onCancel()
    }

    DisposableEffect(prompt.id, prompt.stage, prompt.inputRevision) {
        onDispose { pin.fill(CLEARED_PIN_CHARACTER) }
    }
    BackHandler(onBack = ::cancel)

    Surface(
        modifier = Modifier.fillMaxSize(),
        color = MaterialTheme.colorScheme.background,
    ) {
        Column(
            modifier =
                Modifier.fillMaxSize()
                    .verticalScroll(rememberScrollState())
                    .padding(WindowInsets.safeDrawing.asPaddingValues())
                    .padding(horizontal = 24.dp, vertical = 20.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Column(
                modifier = Modifier.fillMaxWidth().widthIn(max = 360.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                Text(
                    text = "BASS",
                    color = MaterialTheme.colorScheme.primary,
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Black,
                )
                Text(
                    text = stringResource(prompt.titleResource()),
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.Bold,
                    textAlign = TextAlign.Center,
                )
                Text(
                    text = stringResource(prompt.descriptionResource()),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.bodyMedium,
                    textAlign = TextAlign.Center,
                )

                PinDots(enteredDigits = pin.size)

                prompt.error
                    ?.takeIf { it.isNotBlank() }
                    ?.let { error ->
                        Text(
                            text = error,
                            modifier =
                                Modifier.semantics {
                                    liveRegion = LiveRegionMode.Polite
                                },
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall,
                            textAlign = TextAlign.Center,
                        )
                    }
                if (prompt.lockedSeconds > 0) {
                    Text(
                        text =
                            pluralStringResource(
                                R.plurals.pin_locked_seconds,
                                prompt.lockedSeconds,
                                prompt.lockedSeconds,
                            ),
                        modifier =
                            Modifier.semantics {
                                liveRegion = LiveRegionMode.Polite
                            },
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                        textAlign = TextAlign.Center,
                    )
                }
                if (prompt.busy) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(28.dp),
                        strokeWidth = 3.dp,
                    )
                } else {
                    Spacer(Modifier.height(28.dp))
                }

                NumberPad(
                    enabled = inputEnabled,
                    submitEnabled = inputEnabled && pin.size == PIN_LENGTH,
                    onDigit = { digit ->
                        if (pin.size < PIN_LENGTH) {
                            val updatedPin = pin.copyOf(pin.size + 1)
                            updatedPin[updatedPin.lastIndex] = digit
                            pin.fill(CLEARED_PIN_CHARACTER)
                            pin = updatedPin
                        }
                    },
                    onDelete = {
                        if (pin.isNotEmpty()) {
                            val updatedPin = pin.copyOf(pin.size - 1)
                            pin.fill(CLEARED_PIN_CHARACTER)
                            pin = updatedPin
                        }
                    },
                    onSubmit = {
                        val submittedPin = pin.copyOf()
                        pin.fill(CLEARED_PIN_CHARACTER)
                        pin = CharArray(0)
                        onSubmit(submittedPin)
                    },
                )

                OutlinedButton(
                    modifier = Modifier.fillMaxWidth(),
                    onClick = ::cancel,
                ) {
                    Text(stringResource(R.string.action_cancel))
                }
            }
        }
    }
}

@Composable
private fun PinDots(enteredDigits: Int) {
    val description =
        stringResource(
            R.string.pin_digits_entered,
            enteredDigits,
            PIN_LENGTH,
        )
    Row(
        modifier =
            Modifier.clearAndSetSemantics {
                contentDescription = description
            },
        horizontalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        repeat(PIN_LENGTH) { index ->
            val color =
                if (index < enteredDigits) {
                    MaterialTheme.colorScheme.primary
                } else {
                    MaterialTheme.colorScheme.surfaceVariant
                }
            Box(modifier = Modifier.size(16.dp).background(color = color, shape = CircleShape))
        }
    }
}

@Composable
private fun NumberPad(
    enabled: Boolean,
    submitEnabled: Boolean,
    onDigit: (Char) -> Unit,
    onDelete: () -> Unit,
    onSubmit: () -> Unit,
) {
    val deleteDescription = stringResource(R.string.pin_delete)
    Column(
        modifier = Modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        listOf("123", "456", "789").forEach { digits ->
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                digits.forEach { digit ->
                    OutlinedButton(
                        modifier = Modifier.weight(1f).heightIn(min = 56.dp),
                        enabled = enabled,
                        onClick = { onDigit(digit) },
                    ) {
                        Text(
                            text = digit.toString(),
                            style = MaterialTheme.typography.titleLarge,
                        )
                    }
                }
            }
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedButton(
                modifier =
                    Modifier.weight(1f).heightIn(min = 56.dp).semantics {
                        contentDescription = deleteDescription
                    },
                enabled = enabled,
                onClick = onDelete,
            ) {
                Text(
                    text = "⌫",
                    style = MaterialTheme.typography.titleLarge,
                )
            }
            OutlinedButton(
                modifier = Modifier.weight(1f).heightIn(min = 56.dp),
                enabled = enabled,
                onClick = { onDigit('0') },
            ) {
                Text(
                    text = "0",
                    style = MaterialTheme.typography.titleLarge,
                )
            }
            Button(
                modifier = Modifier.weight(1f).heightIn(min = 56.dp),
                enabled = submitEnabled,
                onClick = onSubmit,
            ) {
                Text(stringResource(R.string.pin_submit))
            }
        }
    }
}

private fun PinPrompt.titleResource(): Int =
    when (stage) {
        PinStage.CREATE -> R.string.pin_create_title
        PinStage.CONFIRM -> R.string.pin_confirm_title
        PinStage.VERIFY -> R.string.pin_verify_title
    }

private fun PinPrompt.descriptionResource(): Int =
    when {
        stage == PinStage.CREATE -> R.string.pin_create_description
        stage == PinStage.CONFIRM -> R.string.pin_confirm_description
        purpose == PinPurpose.PAIR -> R.string.pin_pair_description
        purpose == PinPurpose.ENROLL -> R.string.pin_enroll_description
        purpose == PinPurpose.UNLOCK -> R.string.pin_unlock_description
        else -> R.string.pin_setup_description
    }
