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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.bass.app.AppSettings
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
    val settings = state.settings
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
                )

                NumberSetting(
                    value = settings.autoRelockSeconds,
                    onValueChange = {
                        viewModel.updateSettings(
                            settings.copy(autoRelockSeconds = it.coerceIn(0, 600)),
                        )
                    },
                    label = stringResource(R.string.settings_auto_relock),
                    help = stringResource(R.string.settings_auto_relock_help),
                )
                NumberSetting(
                    value = settings.ignitionAutoStopSeconds,
                    onValueChange = {
                        viewModel.updateSettings(
                            settings.copy(
                                ignitionAutoStopSeconds = it.coerceIn(0, 1800),
                            ),
                        )
                    },
                    label = stringResource(R.string.settings_ignition_stop),
                    help = stringResource(R.string.settings_ignition_stop_help),
                )
                NumberSetting(
                    value = settings.promptAutoLockSeconds,
                    onValueChange = {
                        viewModel.updateSettings(
                            settings.copy(
                                promptAutoLockSeconds = it.coerceIn(0, 600),
                            ),
                        )
                    },
                    label = stringResource(R.string.settings_prompt_lock),
                    help = stringResource(R.string.settings_prompt_lock_help),
                )

                SecuritySettings(
                    settings = settings,
                    onSettingsChange = viewModel::updateSettings,
                )

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    OutlinedButton(
                        modifier = Modifier.weight(1f),
                        enabled = !state.busy,
                        onClick = viewModel::resetSettings,
                    ) {
                        Text(stringResource(R.string.action_reset))
                    }
                    Button(
                        modifier = Modifier.weight(1f),
                        enabled = !state.busy,
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
    value: Int,
    onValueChange: (Int) -> Unit,
    label: String,
    help: String,
    enabled: Boolean = true,
) {
    Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
        OutlinedTextField(
            value = value.toString(),
            onValueChange = { raw ->
                raw.filter { it.isDigit() }.toIntOrNull()?.let(onValueChange)
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = enabled,
            singleLine = true,
            label = { Text(label) },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        )
        Text(
            text = help,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

@Composable
private fun SecuritySettings(
    settings: AppSettings,
    onSettingsChange: (AppSettings) -> Unit,
) {
    SettingSwitch(
        checked = settings.liveness,
        onCheckedChange = {
            onSettingsChange(settings.copy(liveness = it))
        },
        label = stringResource(R.string.settings_liveness),
        help = stringResource(R.string.settings_liveness_help),
    )
    SettingSwitch(
        checked = settings.failLockout,
        onCheckedChange = {
            onSettingsChange(settings.copy(failLockout = it))
        },
        label = stringResource(R.string.settings_fail_lockout),
        help = stringResource(R.string.settings_fail_lockout_help),
    )
    NumberSetting(
        value = settings.lockoutAfter,
        onValueChange = {
            onSettingsChange(settings.copy(lockoutAfter = it.coerceIn(1, 20)))
        },
        label = stringResource(R.string.settings_lockout_after),
        help = stringResource(R.string.settings_fail_lockout_help),
    )
}

@Composable
private fun SettingSwitch(
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    label: String,
    help: String,
) {
    Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = label,
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.bodyLarge,
            )
            Switch(
                checked = checked,
                onCheckedChange = onCheckedChange,
            )
        }
        StatusChip(
            text = stringResource(R.string.settings_not_enforced),
            tone = ChipTone.WARNING,
        )
        Text(
            text = help,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}
