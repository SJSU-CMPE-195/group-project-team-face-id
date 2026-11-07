package com.bass.app.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
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
import com.bass.app.ui.screens.CommissioningScreen
import com.bass.app.ui.screens.LogsScreen
import com.bass.app.ui.screens.PairingScreen
import com.bass.app.ui.screens.PairingCredentialsScreen
import com.bass.app.ui.screens.PinScreen
import com.bass.app.ui.screens.SettingsScreen
import com.bass.app.ui.screens.UsersScreen
import com.bass.app.ui.theme.BassTheme

@Composable
fun BassApp(viewModel: AppViewModel) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val exportMaterial = state.exportMaterial
    var showExport by remember(exportMaterial?.content) { mutableStateOf(exportMaterial != null) }
    val exportLauncher =
        rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/json")) {
            uri ->
            if (uri != null && exportMaterial != null) {
                runCatching {
                    context.contentResolver.openOutputStream(uri, "wt")?.bufferedWriter()?.use {
                        writer -> writer.write(exportMaterial.content)
                    } ?: error("Could not open selected document")
                }.onSuccess {
                    viewModel.exportMaterialSaved()
                    showExport = false
                }.onFailure { viewModel.materialExportFailed() }
            }
        }

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
                state.commissioningPrompt != null ->
                    CommissioningScreen(state = state, viewModel = viewModel)
                state.pairingCredentialsRequired ->
                    PairingCredentialsScreen(state = state, viewModel = viewModel)
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

        if (
            state.phase == ConnectionPhase.CONNECTED &&
                state.pinPrompt == null &&
                showExport &&
                exportMaterial != null
        ) {
            AlertDialog(
                onDismissRequest = { showExport = false },
                title = { Text(stringResource(R.string.export_material_title)) },
                text = { Text(stringResource(R.string.export_material_description)) },
                confirmButton = {
                    TextButton(onClick = { exportLauncher.launch(exportMaterial.fileName) }) {
                        Text(stringResource(R.string.export_material_action))
                    }
                },
                dismissButton = {
                    TextButton(onClick = { showExport = false }) {
                        Text(stringResource(R.string.export_material_later))
                    }
                },
            )
        }
    }
}

@Composable
private fun ForgetDeviceDialog(
    busy: Boolean,
    error: String?,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text(stringResource(R.string.forget_device_title)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(stringResource(R.string.forget_device_description))
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
                onClick = onConfirm,
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
        modifier = Modifier.imePadding(),
        containerColor = MaterialTheme.colorScheme.background,
        topBar = { AppHeader(state = state) },
        bottomBar = {
            Surface(color = MaterialTheme.colorScheme.surface) {
                Column(
                    modifier =
                        Modifier.windowInsetsPadding(
                            WindowInsets.safeDrawing.only(WindowInsetsSides.Horizontal),
                        ),
                ) {
                    OutlinedButton(
                        modifier =
                            Modifier.fillMaxWidth()
                                .padding(horizontal = 16.dp, vertical = 8.dp)
                                .heightIn(min = 48.dp),
                        enabled = !state.busy && !state.refreshing,
                        onClick = viewModel::refresh,
                    ) {
                        Text(
                            stringResource(
                                if (state.refreshing) R.string.action_refreshing
                                else R.string.action_refresh,
                            ),
                        )
                    }
                    AppNavigation(
                        selectedTab = state.selectedTab,
                        isAdmin = state.identity?.user?.isAdmin == true,
                        onSelect = viewModel::selectTab,
                    )
                }
            }
        },
    ) { innerPadding ->
        val contentModifier = Modifier.padding(innerPadding).consumeWindowInsets(innerPadding)
        when (state.selectedTab) {
            Tab.CONSOLE ->
                ConsoleScreen(
                    state = state,
                    viewModel = viewModel,
                    modifier = contentModifier,
                )
            Tab.USERS ->
                UsersScreen(
                    state = state,
                    viewModel = viewModel,
                    modifier = contentModifier,
                )
            Tab.LOGS ->
                LogsScreen(
                    logs = state.logs,
                    modifier = contentModifier,
                )
            Tab.SETTINGS ->
                SettingsScreen(
                    state = state,
                    viewModel = viewModel,
                    modifier = contentModifier,
                )
        }
    }
}

@Composable
private fun AppHeader(
    state: BassState,
) {
    Surface(color = MaterialTheme.colorScheme.surface) {
        Row(
            modifier =
                Modifier.fillMaxWidth()
                    .windowInsetsPadding(
                        WindowInsets.safeDrawing.only(
                            WindowInsetsSides.Top + WindowInsetsSides.Horizontal,
                        ),
                    )
                    .padding(horizontal = 18.dp, vertical = 12.dp),
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
    isAdmin: Boolean,
    onSelect: (Tab) -> Unit,
) {
    val destinations =
        buildList {
            add(NavigationDestination(Tab.CONSOLE, R.string.nav_console, "⌂"))
            add(NavigationDestination(Tab.USERS, R.string.nav_users, "♙"))
            add(NavigationDestination(Tab.LOGS, R.string.nav_logs, "≡"))
            if (isAdmin) add(NavigationDestination(Tab.SETTINGS, R.string.nav_settings, "⚙"))
        }

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
