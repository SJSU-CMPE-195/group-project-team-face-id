package com.bass.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.bass.app.AppViewModel
import com.bass.app.BassState
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
        item { CurrentStateCard(state) }
        item { StatusMetrics(state) }
        item { CameraContextCard(state) }
        item { ConnectionCard(state, viewModel::requestForgetDevice) }
    }
}

@Composable
private fun CurrentStateCard(state: BassState) {
    val status = state.status
    BassCard {
        SectionTitle(
            title = stringResource(R.string.console_current_state),
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
                tone = if (status?.ignitionOn == true) ChipTone.SUCCESS else ChipTone.DEFAULT,
            )
        }
        Text(
            text = stringResource(R.string.console_host_controls_only),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

@Composable
private fun StatusMetrics(state: BassState) {
    val status = state.status
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Metric(
            modifier = Modifier.weight(1f),
            label = stringResource(R.string.console_battery),
            value = status?.battery?.let { "$it%" } ?: stringResource(R.string.console_unknown),
        )
        Metric(
            modifier = Modifier.weight(1f),
            label = stringResource(R.string.console_signal),
            value =
                status?.signal?.let { "$it / 5" } ?: stringResource(R.string.console_unknown),
        )
    }
}

@Composable
private fun Metric(modifier: Modifier, label: String, value: String) {
    BassCard(modifier = modifier) {
        Text(
            text = label,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.labelMedium,
        )
        Text(
            text = value,
            style = MaterialTheme.typography.titleLarge,
            fontWeight = FontWeight.Bold,
        )
    }
}

@Composable
private fun CameraContextCard(state: BassState) {
    val source = state.status?.cameraSource ?: state.capabilities.cameraSource
    val isDevelopment = source == "pc_webcam"
    BassCard {
        SectionTitle(
            title = stringResource(R.string.console_camera_source_title),
            description =
                stringResource(
                    if (isDevelopment) {
                        R.string.console_camera_source_pc
                    } else {
                        R.string.console_camera_source_pi
                    }
                ),
            action = {
                StatusChip(
                    text =
                        stringResource(
                            if (isDevelopment) {
                                R.string.console_development_mode
                            } else {
                                R.string.console_product_mode
                            }
                        ),
                    tone = if (isDevelopment) ChipTone.WARNING else ChipTone.SUCCESS,
                )
            },
        )
    }
}

@Composable
private fun ConnectionCard(state: BassState, onForget: () -> Unit) {
    BassCard {
        SectionTitle(
            title = stringResource(R.string.console_connection_title),
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
