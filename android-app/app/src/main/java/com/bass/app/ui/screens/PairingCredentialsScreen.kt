package com.bass.app.ui.screens

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.BackHandler
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
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
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.bass.app.AppViewModel
import com.bass.app.BassState
import com.bass.app.CaptureFlow
import com.bass.app.R
import com.bass.app.camera.CameraPanel
import com.bass.app.ui.components.BassCard

@Composable
fun PairingCredentialsScreen(
    state: BassState,
    viewModel: AppViewModel,
) {
    val context = LocalContext.current
    var pin by remember { mutableStateOf("") }
    var cameraDenied by remember { mutableStateOf(false) }
    var cameraError by remember { mutableStateOf<String?>(null) }
    val cameraPermission = Manifest.permission.CAMERA
    val permissionLauncher =
        rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            cameraDenied = !granted
            if (granted) viewModel.beginPairingInviteScan()
        }
    val hasCameraPermission =
        ContextCompat.checkSelfPermission(context, cameraPermission) ==
            PackageManager.PERMISSION_GRANTED

    fun startInviteScan() {
        cameraError = null
        cameraDenied = false
        if (hasCameraPermission) viewModel.beginPairingInviteScan()
        else permissionLauncher.launch(cameraPermission)
    }

    fun cancel() {
        pin = ""
        viewModel.cancelPairingCredentials()
    }

    DisposableEffect(Unit) {
        onDispose {
            pin = ""
        }
    }
    BackHandler(enabled = !state.busy) {
        if (state.captureFlow == CaptureFlow.QR) viewModel.cancelPairingInviteScan()
        else cancel()
    }

    Column(
        modifier =
            Modifier.fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 28.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = "BASS",
            color = MaterialTheme.colorScheme.primary,
            style = MaterialTheme.typography.displaySmall,
            fontWeight = FontWeight.Black,
        )
        Spacer(Modifier.height(28.dp))
        BassCard {
            Text(
                text = stringResource(R.string.pair_credentials_title),
                modifier = Modifier.fillMaxWidth(),
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.Bold,
                textAlign = TextAlign.Center,
            )
            Text(
                text = stringResource(R.string.pair_credentials_description),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodyMedium,
                textAlign = TextAlign.Center,
            )
            if (state.captureFlow == CaptureFlow.QR && hasCameraPermission) {
                Box(
                    modifier =
                        Modifier.fillMaxWidth()
                            .height(360.dp)
                            .clip(RoundedCornerShape(16.dp)),
                ) {
                    CameraPanel(
                        enabled = true,
                        qrMode = true,
                        onQr = viewModel::acceptPairingInviteQr,
                        onFrame = {},
                        onError = { cameraError = it },
                        modifier =
                            Modifier.fillMaxSize().semantics {
                                contentDescription =
                                    context.getString(R.string.pair_invite_camera_preview)
                            },
                    )
                    Text(
                        text = stringResource(R.string.pair_scanner_hint),
                        modifier =
                            Modifier.align(Alignment.BottomCenter)
                                .fillMaxWidth()
                                .padding(18.dp),
                        color = MaterialTheme.colorScheme.onSurface,
                        style = MaterialTheme.typography.bodySmall,
                        textAlign = TextAlign.Center,
                    )
                }
            } else {
                if (state.pairingInviteReady) {
                    Text(
                        text = stringResource(R.string.pair_invite_ready),
                        color = MaterialTheme.colorScheme.primary,
                        style = MaterialTheme.typography.bodyMedium,
                        textAlign = TextAlign.Center,
                    )
                }
                OutlinedButton(
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !state.busy,
                    onClick = ::startInviteScan,
                ) {
                    Text(stringResource(R.string.pair_invite_scan_action))
                }
            }
            val captureError =
                when {
                    cameraDenied -> stringResource(R.string.pair_permission_denied)
                    cameraError != null -> cameraError
                    else -> null
                }
            captureError?.let {
                Text(
                    text = it,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            OutlinedTextField(
                value = pin,
                onValueChange = { raw -> pin = raw.filter(Char::isDigit).take(6) },
                modifier = Modifier.fillMaxWidth(),
                enabled = !state.busy,
                singleLine = true,
                label = { Text(stringResource(R.string.pair_account_pin)) },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                visualTransformation = PasswordVisualTransformation(),
            )
            if (state.busy) {
                CircularProgressIndicator(modifier = Modifier.align(Alignment.CenterHorizontally))
            }
            Button(
                modifier = Modifier.fillMaxWidth(),
                enabled = !state.busy && state.pairingInviteReady && pin.length == 6,
                onClick = {
                    val submittedPin = pin.toCharArray()
                    pin = ""
                    viewModel.submitPairingCredentials(submittedPin)
                },
            ) {
                Text(stringResource(R.string.pair_authorize_phone))
            }
            OutlinedButton(
                modifier = Modifier.fillMaxWidth(),
                enabled = !state.busy,
                onClick = {
                    if (state.captureFlow == CaptureFlow.QR) {
                        viewModel.cancelPairingInviteScan()
                    } else {
                        cancel()
                    }
                },
            ) {
                Text(
                    stringResource(
                        if (state.captureFlow == CaptureFlow.QR) {
                            R.string.action_cancel_scan
                        } else {
                            R.string.action_cancel
                        }
                    )
                )
            }
        }
    }
}
