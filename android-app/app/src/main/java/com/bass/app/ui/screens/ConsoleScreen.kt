package com.bass.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.bass.app.AppViewModel
import com.bass.app.BassState
import com.bass.app.CaptureFlow
import com.bass.app.R
import com.bass.app.ui.components.BassCard
import com.bass.app.ui.components.ChipTone
import com.bass.app.ui.components.SectionTitle
import com.bass.app.ui.components.StatusChip

@Composable
fun ConsoleScreen(
    state: BassState,
    viewModel: AppViewModel,
    modifier: Modifier = Modifier,
) {
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item {
            FaceVerificationCard(
                state = state,
                onStart = viewModel::unlock,
                onCancel = viewModel::cancelActiveSession,
            )
        }

        val status = state.status
        if (
            state.unlockOwner != null &&
                status?.locked == false &&
                !status.ignitionOn &&
                state.captureFlow != CaptureFlow.VERIFY_IGNITION
        ) {
            item {
                IgnitionPrompt(
                    driver = state.unlockOwner,
                    countdown = state.promptCountdown,
                    busy = state.busy,
                    deviceCameraAvailable = state.capabilities.deviceCamera,
                    onVerify = viewModel::verifyIgnition,
                    onLock = viewModel::lock,
                )
            }
        }

        item {
            CurrentStateCard(state = state, viewModel = viewModel)
        }

        item {
            StatusMetrics(state = state)
        }

        item {
            ConnectionCard(state = state, onForget = viewModel::requestForgetDevice)
        }
    }
}

@Composable
private fun FaceVerificationCard(
    state: BassState,
    onStart: () -> Unit,
    onCancel: () -> Unit,
) {
    val verifyActive =
        state.captureFlow == CaptureFlow.VERIFY_UNLOCK ||
            state.captureFlow == CaptureFlow.VERIFY_IGNITION

    BassCard {
        SectionTitle(
            title = stringResource(R.string.console_face_title),
            description = stringResource(R.string.console_device_camera_description),
        )

        if (!state.capabilities.deviceCamera) {
            Text(
                text = stringResource(R.string.console_device_camera_unavailable),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodySmall,
            )
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.CenterHorizontally),
        ) {
            StatusChip(
                text =
                    if (verifyActive) {
                        stringResource(R.string.status_scanning)
                    } else {
                        stringResource(R.string.status_ready)
                    },
                tone = if (verifyActive) ChipTone.INFO else ChipTone.DEFAULT,
            )
            state.status?.let { status ->
                StatusChip(
                    text =
                        if (status.locked) {
                            stringResource(R.string.status_locked)
                        } else {
                            stringResource(R.string.status_unlocked)
                        },
                    tone = if (status.locked) ChipTone.WARNING else ChipTone.SUCCESS,
                )
            }
        }

        SessionProgress(state)

        if (verifyActive) {
            OutlinedButton(
                modifier = Modifier.fillMaxWidth(),
                onClick = onCancel,
            ) {
                Text(stringResource(R.string.action_cancel_scan))
            }
        } else if (state.status?.locked != false) {
            Button(
                modifier = Modifier.fillMaxWidth(),
                enabled = !state.busy && state.capabilities.deviceCamera,
                onClick = onStart,
            ) {
                Text(stringResource(R.string.action_scan_face))
            }
        }
    }
}

@Composable
private fun SessionProgress(state: BassState) {
    val message = state.activeSessionMessage
    val progress = state.activeSessionProgress
    val total = state.activeSessionTotal
    if (message == null && progress == null) return

    Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
        if (!message.isNullOrBlank()) {
            Text(
                text = message,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodySmall,
            )
        }
        if (progress != null && total != null && total > 0) {
            LinearProgressIndicator(
                progress = { (progress.toFloat() / total).coerceIn(0f, 1f) },
                modifier = Modifier.fillMaxWidth(),
            )
            Text(
                text = "$progress / $total",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.labelSmall,
            )
        }
    }
}

@Composable
private fun IgnitionPrompt(
    driver: String,
    countdown: Int?,
    busy: Boolean,
    deviceCameraAvailable: Boolean,
    onVerify: () -> Unit,
    onLock: () -> Unit,
) {
    BassCard {
        Text(
            text = stringResource(R.string.console_start_ignition_prompt, driver),
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
            textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth(),
        )
        Text(
            text =
                if (countdown != null) {
                    pluralStringResource(
                        R.plurals.console_auto_lock_countdown,
                        countdown,
                        countdown,
                    )
                } else {
                    stringResource(R.string.console_no_auto_lock)
                },
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.bodySmall,
            textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth(),
        )
        Button(
            modifier = Modifier.fillMaxWidth(),
            enabled = !busy && deviceCameraAvailable,
            onClick = onVerify,
        ) {
            Text(stringResource(R.string.action_verify_ignition))
        }
        OutlinedButton(
            modifier = Modifier.fillMaxWidth(),
            enabled = !busy,
            onClick = onLock,
        ) {
            Text(stringResource(R.string.action_lock_now))
        }
    }
}

@Composable
private fun CurrentStateCard(
    state: BassState,
    viewModel: AppViewModel,
) {
    val status = state.status
    BassCard {
        Text(
            text = stringResource(R.string.console_current_state),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.labelMedium,
        )
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text =
                    if (status?.locked != false) {
                        stringResource(R.string.status_locked)
                    } else {
                        stringResource(R.string.status_unlocked)
                    },
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
            )
            StatusChip(
                text =
                    if (status?.ignitionOn == true) {
                        stringResource(R.string.status_ignition_on)
                    } else {
                        stringResource(R.string.status_ignition_off)
                    },
                tone =
                    if (status?.ignitionOn == true) {
                        ChipTone.SUCCESS
                    } else {
                        ChipTone.DEFAULT
                    },
            )
        }
        Text(
            text =
                if (status?.locked != false) {
                    stringResource(R.string.console_locked_hint)
                } else {
                    stringResource(R.string.console_unlocked_hint)
                },
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.bodyMedium,
        )

        if (status?.locked == false) {
            OutlinedButton(
                modifier = Modifier.fillMaxWidth(),
                enabled = !state.busy,
                onClick = viewModel::lock,
            ) {
                Text(stringResource(R.string.action_lock))
            }
        }

        if (status?.ignitionOn == true) {
            OutlinedButton(
                modifier = Modifier.fillMaxWidth(),
                enabled = !state.busy,
                onClick = viewModel::stopIgnition,
            ) {
                Text(stringResource(R.string.action_stop_ignition))
            }
        }
        OutlinedButton(
            modifier = Modifier.fillMaxWidth(),
            enabled = !state.busy,
            onClick = viewModel::fullReset,
        ) {
            Text(
                text = stringResource(R.string.action_full_reset),
                color = MaterialTheme.colorScheme.error,
            )
        }
    }
}

@Composable
private fun StatusMetrics(state: BassState) {
    val status = state.status
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        BassCard(modifier = Modifier.weight(1f)) {
            Text(
                text = stringResource(R.string.console_battery),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.labelMedium,
            )
            Text(
                text = status?.battery?.let { "$it%" } ?: stringResource(R.string.console_unknown),
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
            )
        }
        BassCard(modifier = Modifier.weight(1f)) {
            Text(
                text = stringResource(R.string.console_signal),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.labelMedium,
            )
            Text(
                text =
                    status?.signal?.let { "$it / 5" } ?: stringResource(R.string.console_unknown),
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
            )
        }
    }
}

@Composable
private fun ConnectionCard(
    state: BassState,
    onForget: () -> Unit,
) {
    val device = state.device
    BassCard {
        SectionTitle(
            title = stringResource(R.string.console_connection_title),
            description = stringResource(R.string.console_connection_description),
            action = {
                StatusChip(
                    text =
                        if (state.capabilities.simulatedActuator) {
                            stringResource(R.string.status_simulated)
                        } else {
                            stringResource(R.string.status_live_hardware)
                        },
                    tone =
                        if (state.capabilities.simulatedActuator) {
                            ChipTone.WARNING
                        } else {
                            ChipTone.SUCCESS
                        },
                )
            },
        )
        DeviceLine(stringResource(R.string.console_device), device?.name)
        DeviceLine(stringResource(R.string.console_address), device?.address)
        DeviceLine(
            stringResource(R.string.console_protocol),
            device?.protocolVersion?.toString(),
        )
        OutlinedButton(
            modifier = Modifier.fillMaxWidth(),
            enabled = !state.busy,
            onClick = onForget,
        ) {
            Text(stringResource(R.string.action_forget_device))
        }
    }
}

@Composable
private fun DeviceLine(label: String, value: String?) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(
            text = label,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.bodySmall,
        )
        Text(
            text = value ?: stringResource(R.string.console_unknown),
            style = MaterialTheme.typography.bodySmall,
            fontWeight = FontWeight.Medium,
        )
    }
}
