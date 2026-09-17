package com.bass.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.bass.app.AppViewModel
import com.bass.app.BassState
import com.bass.app.ConnectionPhase
import com.bass.app.R
import com.bass.app.Tab
import com.bass.app.ui.screens.ConsoleScreen
import com.bass.app.ui.screens.LogsScreen
import com.bass.app.ui.screens.PairingScreen
import com.bass.app.ui.screens.PinScreen
import com.bass.app.ui.screens.SettingsScreen
import com.bass.app.ui.screens.UsersScreen
import com.bass.app.ui.theme.BassTheme

@Composable
fun BassApp(viewModel: AppViewModel) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    BassTheme {
        Surface(
            modifier = Modifier.fillMaxSize(),
            color = MaterialTheme.colorScheme.background,
        ) {
            val pinPrompt = state.pinPrompt
            when {
                pinPrompt != null ->
                    PinScreen(
                        prompt = pinPrompt,
                        onSubmit = viewModel::submitPin,
                        onCancel = viewModel::cancelPin,
                    )
                state.phase == ConnectionPhase.CONNECTED -> {
                    ConnectedApp(state = state, viewModel = viewModel)
                }
                else -> PairingScreen(state = state, viewModel = viewModel)
            }
        }

        state.error?.let { error ->
            AlertDialog(
                onDismissRequest = viewModel::clearError,
                title = { Text(stringResource(R.string.error_title)) },
                text = { Text(error) },
                confirmButton = {
                    TextButton(onClick = viewModel::clearError) {
                        Text(stringResource(R.string.action_close))
                    }
                },
            )
        }

        if (state.showForgetDeviceDialog) {
            ForgetDeviceDialog(
                busy = state.busy,
                error = state.forgetDeviceError,
                onConfirm = viewModel::forgetDevice,
                onDismiss = viewModel::cancelForgetDevice,
            )
        }
    }
}

@Composable
private fun ForgetDeviceDialog(
    busy: Boolean,
    error: String?,
    onConfirm: (Boolean) -> Unit,
    onDismiss: () -> Unit,
) {
    var resetPin by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text(stringResource(R.string.forget_device_title)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(stringResource(R.string.forget_device_description))
                Row(
                    modifier =
                        Modifier.fillMaxWidth()
                            .toggleable(
                                value = resetPin,
                                enabled = !busy,
                                role = Role.Checkbox,
                                onValueChange = { resetPin = it },
                            )
                            .padding(vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Checkbox(
                        checked = resetPin,
                        enabled = !busy,
                        onCheckedChange = null,
                    )
                    Column(modifier = Modifier.padding(start = 8.dp)) {
                        Text(stringResource(R.string.forget_reset_pin))
                        Text(
                            text = stringResource(R.string.forget_reset_pin_description),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }
                error?.let {
                    Text(
                        text = it,
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
        },
        confirmButton = {
            TextButton(
                enabled = !busy,
                onClick = { onConfirm(resetPin) },
            ) {
                Text(
                    stringResource(
                        if (error == null) R.string.action_forget_device else R.string.action_retry
                    )
                )
            }
        },
        dismissButton = {
            TextButton(enabled = !busy, onClick = onDismiss) {
                Text(stringResource(R.string.action_cancel))
            }
        },
    )
}

@Composable
private fun ConnectedApp(
    state: BassState,
    viewModel: AppViewModel,
) {
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = { AppHeader(state = state, onRefresh = viewModel::refresh) },
        bottomBar = {
            AppNavigation(
                selectedTab = state.selectedTab,
                onSelect = viewModel::selectTab,
            )
        },
    ) { innerPadding ->
        when (state.selectedTab) {
            Tab.CONSOLE ->
                ConsoleScreen(
                    state = state,
                    viewModel = viewModel,
                    modifier = Modifier.padding(innerPadding),
                )
            Tab.USERS ->
                UsersScreen(
                    state = state,
                    viewModel = viewModel,
                    modifier = Modifier.padding(innerPadding),
                )
            Tab.LOGS ->
                LogsScreen(
                    logs = state.logs,
                    modifier = Modifier.padding(innerPadding),
                )
            Tab.SETTINGS ->
                SettingsScreen(
                    state = state,
                    viewModel = viewModel,
                    modifier = Modifier.padding(innerPadding),
                )
        }
    }
}

@Composable
private fun AppHeader(
    state: BassState,
    onRefresh: () -> Unit,
) {
    Surface(color = MaterialTheme.colorScheme.surface) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column {
                Text(
                    text = "BASS",
                    color = MaterialTheme.colorScheme.primary,
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Black,
                )
                Text(
                    text = state.device?.name ?: stringResource(R.string.console_unknown),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            TextButton(
                enabled = !state.busy,
                onClick = onRefresh,
            ) {
                Text(stringResource(R.string.action_refresh))
            }
        }
    }
}

private data class NavigationDestination(
    val tab: Tab,
    val label: Int,
    val glyph: String,
)

@Composable
private fun AppNavigation(
    selectedTab: Tab,
    onSelect: (Tab) -> Unit,
) {
    val destinations =
        listOf(
            NavigationDestination(Tab.CONSOLE, R.string.nav_console, "⌂"),
            NavigationDestination(Tab.USERS, R.string.nav_users, "♙"),
            NavigationDestination(Tab.LOGS, R.string.nav_logs, "≡"),
            NavigationDestination(Tab.SETTINGS, R.string.nav_settings, "⚙"),
        )

    NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
        destinations.forEach { destination ->
            NavigationBarItem(
                selected = selectedTab == destination.tab,
                onClick = { onSelect(destination.tab) },
                icon = {
                    Text(
                        text = destination.glyph,
                        modifier = Modifier.clearAndSetSemantics {},
                        style = MaterialTheme.typography.titleMedium,
                    )
                },
                label = { Text(stringResource(destination.label)) },
            )
        }
    }
}
