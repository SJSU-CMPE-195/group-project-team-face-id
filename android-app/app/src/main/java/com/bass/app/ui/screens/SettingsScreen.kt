package com.bass.app.ui.screens

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
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
    val isAdmin = state.identity?.user?.isAdmin == true
    val isOwner = state.identity?.user?.isOwner == true
    val context = LocalContext.current
    var transferPin by remember { mutableStateOf("") }
    val material = state.exportMaterial
    val exportLauncher =
        rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/json")) {
            uri ->
            if (uri != null && material != null) {
                runCatching {
                    context.contentResolver.openOutputStream(uri, "wt")?.bufferedWriter()?.use {
                        it.write(material.content)
                    } ?: error("Could not open selected document")
                }.onSuccess { viewModel.exportMaterialSaved() }
                    .onFailure { viewModel.materialExportFailed() }
            }
        }
    DisposableEffect(Unit) { onDispose { transferPin = "" } }
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item {
            BassCard {
                SectionTitle(
                    title = stringResource(R.string.settings_core_title),
                    description =
                        stringResource(
                            if (isAdmin) {
                                R.string.settings_description
                            } else {
                                R.string.settings_read_only
                            }
                        ),
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
                    enabled = isAdmin,
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
                    enabled = isAdmin,
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
                    enabled = isAdmin,
                )

                SecuritySettings(
                    settings = settings,
                    enabled = isAdmin,
                    onSettingsChange = viewModel::updateSettings,
                )

                if (!state.capabilities.livenessAvailable) {
                    Text(
                        text =
                            stringResource(
                                if (settings.liveness) {
                                    R.string.settings_liveness_unavailable_enabled
                                } else {
                                    R.string.settings_liveness_unavailable
                                }
                            ),
                        color =
                            if (settings.liveness) {
                                MaterialTheme.colorScheme.error
                            } else {
                                MaterialTheme.colorScheme.onSurfaceVariant
                            },
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                if (state.capabilities.simulatedActuator) {
                    Text(
                        text = stringResource(R.string.settings_actuator_simulated),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodySmall,
                    )
                } else if (!state.capabilities.actuatorControlAvailable) {
                    Text(
                        text = stringResource(R.string.settings_actuator_unavailable),
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                    )
                } else if (state.capabilities.actuatorFeedback != "available") {
                    Text(
                        text = stringResource(R.string.settings_actuator_unconfirmed),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }

                if (isAdmin) {
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
        if (isOwner || material != null) {
            item {
                BassCard {
                    SectionTitle(
                        title = stringResource(R.string.ownership_title),
                        description = stringResource(R.string.ownership_description),
                    )
                    if (material != null) {
                        Text(
                            text = stringResource(R.string.export_material_description),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            style = MaterialTheme.typography.bodySmall,
                        )
                        Button(
                            modifier = Modifier.fillMaxWidth(),
                            onClick = { exportLauncher.launch(material.fileName) },
                        ) {
                            Text(stringResource(R.string.export_material_action))
                        }
                    }
                    if (isOwner) {
                        OutlinedTextField(
                            value = transferPin,
                            onValueChange = { transferPin = it.filter(Char::isDigit).take(6) },
                            modifier = Modifier.fillMaxWidth(),
                            enabled = !state.busy && material == null,
                            singleLine = true,
                            label = { Text(stringResource(R.string.ownership_pin)) },
                            keyboardOptions =
                                KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                            visualTransformation = PasswordVisualTransformation(),
                        )
                        Button(
                            modifier = Modifier.fillMaxWidth(),
                            enabled =
                                !state.busy &&
                                    material == null &&
                                    (state.transferPending || transferPin.length == 6),
                            onClick = {
                                val submitted = transferPin.toCharArray()
                                transferPin = ""
                                viewModel.startOwnershipTransfer(submitted)
                            },
                        ) {
                            Text(
                                stringResource(
                                    if (state.transferPending) {
                                        R.string.ownership_transfer_retry
                                    } else {
                                        R.string.ownership_transfer_action
                                    }
                                )
                            )
                        }
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
    enabled: Boolean,
    onSettingsChange: (AppSettings) -> Unit,
) {
    SettingSwitch(
        checked = settings.liveness,
        onCheckedChange = {
            onSettingsChange(settings.copy(liveness = it))
        },
        label = stringResource(R.string.settings_liveness),
        help = stringResource(R.string.settings_liveness_help),
        enabled = enabled,
    )
    SettingSwitch(
        checked = settings.failLockout,
        onCheckedChange = {
            onSettingsChange(settings.copy(failLockout = it))
        },
        label = stringResource(R.string.settings_fail_lockout),
        help = stringResource(R.string.settings_fail_lockout_help),
        enabled = enabled,
    )
    NumberSetting(
        value = settings.lockoutAfter,
        onValueChange = {
            onSettingsChange(settings.copy(lockoutAfter = it.coerceIn(1, 20)))
        },
        label = stringResource(R.string.settings_lockout_after),
        help = stringResource(R.string.settings_fail_lockout_help),
        enabled = enabled,
    )
}

@Composable
private fun SettingSwitch(
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    label: String,
    help: String,
    enabled: Boolean,
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
                enabled = enabled,
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
