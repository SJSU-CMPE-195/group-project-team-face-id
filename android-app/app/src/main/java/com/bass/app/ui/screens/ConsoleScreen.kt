package com.bass.app.ui.screens

import android.os.SystemClock
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.bass.app.AppViewModel
import com.bass.app.BassState
import com.bass.app.ConnectionPhase
import com.bass.app.ControllerIgnitionState
import com.bass.app.ControllerLockState
import com.bass.app.ControllerState
import com.bass.app.R
import com.bass.app.ui.components.BassCard
import com.bass.app.ui.components.ChipTone
import com.bass.app.ui.components.SectionTitle
import com.bass.app.ui.components.StatusChip
import kotlinx.coroutines.delay

@Composable
fun ConsoleScreen(
    state: BassState,
    viewModel: AppViewModel,
    modifier: Modifier = Modifier,
) {
    val controller = state.status?.controller ?: ControllerState()
    var nowElapsedMillis by remember { mutableLongStateOf(SystemClock.elapsedRealtime()) }
    LaunchedEffect(
        controller.requestedAtElapsedMillis,
        controller.ageMillis,
        controller.fresh,
    ) {
        nowElapsedMillis = SystemClock.elapsedRealtime()
        val freshForMillis = controller.freshnessRemainingAt(nowElapsedMillis)
        if (freshForMillis > 0L) {
            delay(freshForMillis)
            nowElapsedMillis = SystemClock.elapsedRealtime()
        }
    }
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item { CurrentStateCard(state, nowElapsedMillis, viewModel::lock) }
        item { StatusMetrics(state) }
        item { CameraContextCard(state) }
        item { ConnectionCard(state, viewModel::requestForgetDevice) }
    }
}

@Composable
private fun CurrentStateCard(
    state: BassState,
    nowElapsedMillis: Long,
    onLock: () -> Unit,
) {
    val status = state.status
    val controller = status?.controller ?: ControllerState()
    val simulated = status?.simulatedActuator == true
    val controllerFresh = controller.isFreshAt(nowElapsedMillis)
    val lockLabel =
        when {
            simulated ->
                if (status?.locked == true) R.string.status_locked else R.string.status_unlocked
            !controllerFresh -> R.string.console_unknown
            else ->
                when (controller.lock) {
                    ControllerLockState.LOCKED -> R.string.status_locked
                    ControllerLockState.UNLOCKED -> R.string.status_unlocked
                    ControllerLockState.LOCKING -> R.string.status_locking
                    ControllerLockState.UNLOCKING -> R.string.status_unlocking
                    ControllerLockState.UNKNOWN -> R.string.console_unknown
                }
        }
    val ignitionLabel =
        when {
            simulated ->
                if (status?.ignitionOn == true) {
                    R.string.status_ignition_on
                } else {
                    R.string.status_ignition_off
                }
            !controllerFresh -> R.string.status_ignition_unknown
            else ->
                when (controller.ignition) {
                    ControllerIgnitionState.ON -> R.string.status_ignition_on
                    ControllerIgnitionState.OFF -> R.string.status_ignition_off
                    ControllerIgnitionState.UNKNOWN -> R.string.status_ignition_unknown
                }
        }
    val ignitionOn =
        if (simulated) status?.ignitionOn == true
        else controllerFresh && controller.ignition == ControllerIgnitionState.ON
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
                text = stringResource(lockLabel),
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
            )
            StatusChip(
                text = stringResource(ignitionLabel),
                tone =
                    when {
                        ignitionOn -> ChipTone.SUCCESS
                        !simulated &&
                            (!controllerFresh ||
                                controller.ignition == ControllerIgnitionState.UNKNOWN) ->
                            ChipTone.WARNING
                        else -> ChipTone.DEFAULT
                    },
            )
        }
        if (!simulated) {
            Text(
                text =
                    stringResource(
                        if (controllerFresh) {
                            R.string.console_controller_reported
                        } else {
                            R.string.console_controller_unavailable
                        }
                    ),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodySmall,
            )
        }
        if (status?.phoneLockAvailable == true && state.phase == ConnectionPhase.CONNECTED) {
            // Locks the doors and arms the alarm; the view model asks for the PIN.
            Button(
                modifier = Modifier.fillMaxWidth(),
                enabled = !state.busy,
                onClick = onLock,
            ) {
                Text(stringResource(R.string.console_lock))
            }
            Text(
                text = stringResource(R.string.console_lock_hint),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodySmall,
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
