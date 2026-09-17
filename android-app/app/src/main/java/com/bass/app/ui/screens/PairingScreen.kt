package com.bass.app.ui.screens

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.bass.app.AppViewModel
import com.bass.app.BassState
import com.bass.app.CaptureFlow
import com.bass.app.ConnectionPhase
import com.bass.app.R
import com.bass.app.camera.CameraPanel
import com.bass.app.ui.components.BassCard
import com.bass.app.ui.components.ChipTone
import com.bass.app.ui.components.StatusChip

@Composable
fun PairingScreen(
    state: BassState,
    viewModel: AppViewModel,
) {
    val context = LocalContext.current
    var cameraDenied by remember { mutableStateOf(false) }
    var cameraError by remember { mutableStateOf<String?>(null) }
    val cameraPermission = Manifest.permission.CAMERA
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        cameraDenied = !granted
        if (granted) viewModel.beginPairing()
    }
    val hasCameraPermission =
        ContextCompat.checkSelfPermission(context, cameraPermission) ==
            PackageManager.PERMISSION_GRANTED

    fun startPairing() {
        cameraError = null
        if (hasCameraPermission) {
            viewModel.beginPairing()
        } else {
            permissionLauncher.launch(cameraPermission)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 28.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = "BASS",
            color = MaterialTheme.colorScheme.primary,
            style = MaterialTheme.typography.displaySmall,
            fontWeight = FontWeight.Black,
        )
        Text(
            text = stringResource(R.string.app_tagline),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.bodySmall,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(28.dp))

        BassCard {
            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(
                    text = stringResource(R.string.pair_title),
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.Bold,
                    textAlign = TextAlign.Center,
                )
                Text(
                    text = stringResource(R.string.pair_description),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.bodyMedium,
                    textAlign = TextAlign.Center,
                )
                StatusChip(
                    text = phaseLabel(state.phase),
                    tone = phaseTone(state.phase),
                )
            }

            if (state.captureFlow == CaptureFlow.QR && hasCameraPermission) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(360.dp)
                        .clip(RoundedCornerShape(16.dp)),
                ) {
                    CameraPanel(
                        enabled = true,
                        qrMode = true,
                        onQr = viewModel::acceptPairingQr,
                        onFrame = {},
                        onError = { cameraError = it },
                        modifier = Modifier
                            .fillMaxSize()
                            .semantics {
                                contentDescription = context.getString(
                                    R.string.pair_camera_preview,
                                )
                            },
                    )
                    Text(
                        text = stringResource(R.string.pair_scanner_hint),
                        modifier = Modifier
                            .align(Alignment.BottomCenter)
                            .fillMaxWidth()
                            .padding(18.dp),
                        color = MaterialTheme.colorScheme.onSurface,
                        style = MaterialTheme.typography.bodySmall,
                        textAlign = TextAlign.Center,
                    )
                }
            }

            if (
                state.phase == ConnectionPhase.DISCOVERING ||
                state.phase == ConnectionPhase.CONNECTING
            ) {
                Column(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    CircularProgressIndicator(modifier = Modifier.size(34.dp))
                    Text(
                        text = phaseDescription(state.phase),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textAlign = TextAlign.Center,
                    )
                }
            } else {
                val message = when {
                    cameraDenied -> stringResource(R.string.pair_permission_denied)
                    cameraError != null -> cameraError.orEmpty()
                    else -> phaseDescription(state.phase)
                }
                if (message.isNotBlank()) {
                    Text(
                        text = message,
                        modifier = Modifier.fillMaxWidth(),
                        color = if (
                            cameraDenied ||
                            cameraError != null ||
                            state.phase == ConnectionPhase.UNAUTHORIZED ||
                            state.phase == ConnectionPhase.OFFLINE ||
                            state.phase == ConnectionPhase.ERROR
                        ) {
                            MaterialTheme.colorScheme.error
                        } else {
                            MaterialTheme.colorScheme.onSurfaceVariant
                        },
                        style = MaterialTheme.typography.bodySmall,
                        textAlign = TextAlign.Center,
                    )
                }
            }

            PairingActions(
                state = state,
                onStartPairing = ::startPairing,
                onCancel = viewModel::cancelActiveSession,
                onRetry = viewModel::retryConnection,
                onForget = viewModel::requestForgetDevice,
            )
        }
    }
}

@Composable
private fun PairingActions(
    state: BassState,
    onStartPairing: () -> Unit,
    onCancel: () -> Unit,
    onRetry: () -> Unit,
    onForget: () -> Unit,
) {
    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        when {
            state.captureFlow == CaptureFlow.QR -> {
                OutlinedButton(
                    modifier = Modifier.fillMaxWidth(),
                    onClick = onCancel,
                ) {
                    Text(stringResource(R.string.action_cancel))
                }
            }
            state.phase == ConnectionPhase.UNPAIRED -> {
                Button(
                    modifier = Modifier.fillMaxWidth(),
                    onClick = onStartPairing,
                ) {
                    Text(stringResource(R.string.action_pair))
                }
            }
            state.phase == ConnectionPhase.UNAUTHORIZED ||
                state.phase == ConnectionPhase.OFFLINE ||
                state.phase == ConnectionPhase.ERROR -> {
                Button(
                    modifier = Modifier.fillMaxWidth(),
                    onClick = onRetry,
                ) {
                    Text(stringResource(R.string.action_retry))
                }
                OutlinedButton(
                    modifier = Modifier.fillMaxWidth(),
                    onClick = onForget,
                ) {
                    Text(stringResource(R.string.action_forget_device))
                }
            }
        }
    }
}

@Composable
private fun phaseLabel(phase: ConnectionPhase): String = when (phase) {
    ConnectionPhase.UNPAIRED -> stringResource(R.string.status_disconnected)
    ConnectionPhase.DISCOVERING -> stringResource(R.string.status_discovering)
    ConnectionPhase.CONNECTING -> stringResource(R.string.status_connecting)
    ConnectionPhase.CONNECTED -> stringResource(R.string.status_connected)
    ConnectionPhase.UNAUTHORIZED -> stringResource(R.string.status_unauthorized)
    ConnectionPhase.OFFLINE -> stringResource(R.string.status_offline)
    ConnectionPhase.ERROR -> stringResource(R.string.status_error)
}

private fun phaseTone(phase: ConnectionPhase): ChipTone = when (phase) {
    ConnectionPhase.CONNECTED -> ChipTone.SUCCESS
    ConnectionPhase.DISCOVERING,
    ConnectionPhase.CONNECTING,
    -> ChipTone.INFO
    ConnectionPhase.UNPAIRED,
    ConnectionPhase.OFFLINE,
    -> ChipTone.WARNING
    ConnectionPhase.UNAUTHORIZED,
    ConnectionPhase.ERROR,
    -> ChipTone.ERROR
}

@Composable
private fun phaseDescription(phase: ConnectionPhase): String = when (phase) {
    ConnectionPhase.UNPAIRED -> stringResource(R.string.pair_camera_permission)
    ConnectionPhase.DISCOVERING -> stringResource(R.string.pair_searching)
    ConnectionPhase.CONNECTING -> stringResource(R.string.pair_connecting)
    ConnectionPhase.CONNECTED -> ""
    ConnectionPhase.UNAUTHORIZED -> stringResource(R.string.pair_unauthorized)
    ConnectionPhase.OFFLINE -> stringResource(R.string.pair_offline)
    ConnectionPhase.ERROR -> stringResource(R.string.pair_error)
}
