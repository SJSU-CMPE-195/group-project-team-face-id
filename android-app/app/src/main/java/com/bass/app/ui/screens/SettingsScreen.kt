package com.bass.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.bass.app.AppViewModel
import com.bass.app.BassState
import com.bass.app.R
import com.bass.app.ui.components.BassCard
import com.bass.app.ui.components.ChipTone
import com.bass.app.ui.components.SectionTitle
import com.bass.app.ui.components.StatusChip

@Composable
fun SettingsScreen(
    state: BassState,
    viewModel: AppViewModel,
    modifier: Modifier = Modifier,
) {
    if (state.identity?.user?.isAdmin != true) return

    val settings = state.settings
    var resetRevision by rememberSaveable { mutableIntStateOf(0) }
    var autoRelock by rememberSaveable { mutableStateOf(settings.autoRelockSeconds.toString()) }
    var ignitionStop by
        rememberSaveable { mutableStateOf(settings.ignitionAutoStopSeconds.toString()) }
    var promptLock by
        rememberSaveable { mutableStateOf(settings.promptAutoLockSeconds.toString()) }
    var lockoutAfter by rememberSaveable { mutableStateOf(settings.lockoutAfter.toString()) }

    LaunchedEffect(settings.autoRelockSeconds, resetRevision) {
        autoRelock = settings.autoRelockSeconds.toString()
    }
    LaunchedEffect(settings.ignitionAutoStopSeconds, resetRevision) {
        ignitionStop = settings.ignitionAutoStopSeconds.toString()
    }
    LaunchedEffect(settings.promptAutoLockSeconds, resetRevision) {
        promptLock = settings.promptAutoLockSeconds.toString()
    }
    LaunchedEffect(settings.lockoutAfter, resetRevision) {
        lockoutAfter = settings.lockoutAfter.toString()
    }

    val valid =
        validNumber(autoRelock, 0, 600) &&
            validNumber(ignitionStop, 0, 1800) &&
            validNumber(promptLock, 0, 600) &&
            validNumber(lockoutAfter, 1, 20)
    val canEdit =
        state.settingsLoaded && !state.busy && state.pinPrompt == null

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item {
            BassCard {
                SectionTitle(
                    title = stringResource(R.string.settings_core_title),
                    description = stringResource(R.string.settings_description),
                    action = { SettingsStatus(state, valid) },
                )

                NumberSetting(
                    value = autoRelock,
                    range = 0..600,
                    enabled = canEdit,
                    label = stringResource(R.string.settings_auto_relock),
                    help = stringResource(R.string.settings_auto_relock_help),
                    onValueChange = { raw ->
                        autoRelock = raw
                        raw.toIntOrNull()?.takeIf { it in 0..600 }?.let {
                            viewModel.updateSettings(settings.copy(autoRelockSeconds = it))
                        }
                    },
                )
                NumberSetting(
                    value = ignitionStop,
                    range = 0..1800,
                    enabled = canEdit,
                    label = stringResource(R.string.settings_ignition_stop),
                    help = stringResource(R.string.settings_ignition_stop_help),
                    onValueChange = { raw ->
                        ignitionStop = raw
                        raw.toIntOrNull()?.takeIf { it in 0..1800 }?.let {
                            viewModel.updateSettings(settings.copy(ignitionAutoStopSeconds = it))
                        }
                    },
                )
                NumberSetting(
                    value = promptLock,
                    range = 0..600,
                    enabled = canEdit,
                    label = stringResource(R.string.settings_prompt_lock),
                    help = stringResource(R.string.settings_prompt_lock_help),
                    onValueChange = { raw ->
                        promptLock = raw
                        raw.toIntOrNull()?.takeIf { it in 0..600 }?.let {
                            viewModel.updateSettings(settings.copy(promptAutoLockSeconds = it))
                        }
                    },
                )

                SettingSwitch(
                    checked = settings.liveness,
                    enabled = canEdit,
                    label = stringResource(R.string.settings_liveness),
                    onCheckedChange = {
                        viewModel.updateSettings(settings.copy(liveness = it))
                    },
                )
                if (!state.capabilities.livenessAvailable && settings.liveness) {
                    SettingsWarning()
                }
                SettingSwitch(
                    checked = settings.failLockout,
                    enabled = canEdit,
                    label = stringResource(R.string.settings_fail_lockout),
                    onCheckedChange = {
                        viewModel.updateSettings(settings.copy(failLockout = it))
                    },
                )
                NumberSetting(
                    value = lockoutAfter,
                    range = 1..20,
                    enabled = canEdit,
                    label = stringResource(R.string.settings_lockout_after),
                    help = stringResource(R.string.settings_lockout_after_help),
                    onValueChange = { raw ->
                        lockoutAfter = raw
                        raw.toIntOrNull()?.takeIf { it in 1..20 }?.let {
                            viewModel.updateSettings(settings.copy(lockoutAfter = it))
                        }
                    },
                )

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    OutlinedButton(
                        modifier = Modifier.weight(1f),
                        enabled = canEdit,
                        onClick = {
                            viewModel.resetSettings()
                            resetRevision += 1
                        },
                    ) {
                        Text(stringResource(R.string.action_reset))
                    }
                    Button(
                        modifier = Modifier.weight(1f),
                        enabled = canEdit && valid && state.settingsDirty,
                        onClick = viewModel::saveSettings,
                    ) {
                        Text(stringResource(R.string.action_save))
                    }
                }
            }
        }
    }
}

@Composable
private fun NumberSetting(
    value: String,
    range: IntRange,
    enabled: Boolean,
    label: String,
    help: String,
    onValueChange: (String) -> Unit,
) {
    val valid = validNumber(value, range.first, range.last)
    OutlinedTextField(
        value = value,
        onValueChange = { raw ->
            if (raw.isEmpty() || raw.all(Char::isDigit)) onValueChange(raw)
        },
        modifier = Modifier.fillMaxWidth(),
        enabled = enabled,
        singleLine = true,
        isError = !valid,
        label = { Text(label) },
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        supportingText = {
            Text(
                if (valid) help
                else stringResource(R.string.settings_number_range, range.first, range.last)
            )
        },
    )
}

@Composable
private fun SettingSwitch(
    checked: Boolean,
    enabled: Boolean,
    label: String,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text = label, modifier = Modifier.weight(1f))
        Switch(
            checked = checked,
            enabled = enabled,
            onCheckedChange = onCheckedChange,
            modifier = Modifier.semantics { contentDescription = label },
        )
    }
}

@Composable
private fun SettingsWarning() {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        StatusChip(
            text = stringResource(R.string.settings_liveness_unavailable_title),
            tone = ChipTone.WARNING,
        )
        Text(
            text = stringResource(R.string.settings_liveness_unavailable_description),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

@Composable
private fun SettingsStatus(state: BassState, valid: Boolean) {
    val (text, tone) =
        when {
            !state.settingsLoaded -> R.string.settings_loading to ChipTone.INFO
            !valid -> R.string.settings_invalid to ChipTone.ERROR
            state.settingsDirty -> R.string.settings_unsaved to ChipTone.WARNING
            state.settingsSaved -> R.string.settings_saved to ChipTone.SUCCESS
            else -> R.string.settings_current to ChipTone.DEFAULT
        }
    StatusChip(text = stringResource(text), tone = tone)
}

private fun validNumber(value: String, minimum: Int, maximum: Int): Boolean =
    value.toIntOrNull()?.let { it in minimum..maximum } == true
