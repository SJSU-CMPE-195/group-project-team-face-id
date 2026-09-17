package com.bass.app.ui.screens

import android.Manifest
import android.content.pm.PackageManager
import android.text.format.DateUtils
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
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
import com.bass.app.CameraSource
import com.bass.app.CaptureFlow
import com.bass.app.R
import com.bass.app.User
import com.bass.app.camera.CameraPanel
import com.bass.app.ui.components.BassCard
import com.bass.app.ui.components.ChipTone
import com.bass.app.ui.components.SectionTitle
import com.bass.app.ui.components.StatusChip

@Composable
fun UsersScreen(
    state: BassState,
    viewModel: AppViewModel,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    var displayName by remember { mutableStateOf("") }
    var selectedSource by remember { mutableStateOf(CameraSource.PHONE) }
    var pendingName by remember { mutableStateOf<String?>(null) }
    var permissionDenied by remember { mutableStateOf(false) }
    var deleteTarget by remember { mutableStateOf<User?>(null) }
    val cameraPermission = Manifest.permission.CAMERA
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        permissionDenied = !granted
        val name = pendingName
        pendingName = null
        if (granted && name != null) {
            viewModel.addAndStartEnrollment(name, CameraSource.PHONE)
        }
    }
    val hasCameraPermission =
        ContextCompat.checkSelfPermission(context, cameraPermission) ==
            PackageManager.PERMISSION_GRANTED

    LaunchedEffect(state.capabilities) {
        val currentSupported = when (selectedSource) {
            CameraSource.PHONE -> state.capabilities.clientCamera
            CameraSource.DEVICE -> state.capabilities.deviceCamera
        }
        if (!currentSupported && state.capabilities.deviceCamera) {
            selectedSource = CameraSource.DEVICE
        } else if (!currentSupported && state.capabilities.clientCamera) {
            selectedSource = CameraSource.PHONE
        }
    }

    fun startEnrollment(name: String) {
        val cleanName = name.trim()
        if (cleanName.isEmpty()) return
        permissionDenied = false
        if (selectedSource == CameraSource.DEVICE || hasCameraPermission) {
            viewModel.addAndStartEnrollment(cleanName, selectedSource)
        } else {
            pendingName = cleanName
            permissionLauncher.launch(cameraPermission)
        }
    }

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item {
            EnrollmentCard(
                state = state,
                displayName = displayName,
                selectedSource = selectedSource,
                permissionDenied = permissionDenied,
                onDisplayNameChange = { displayName = it },
                onSourceSelected = { selectedSource = it },
                onStart = { startEnrollment(displayName) },
                onCancel = viewModel::cancelActiveSession,
                onFrame = viewModel::submitEnrollmentSample,
                onCameraError = viewModel::cameraError,
            )
        }

        item {
            BassCard {
                SectionTitle(
                    title = stringResource(R.string.users_people_title),
                    description = stringResource(R.string.users_people_description),
                    action = {
                        StatusChip(
                            text = state.users.size.toString(),
                            tone = ChipTone.INFO,
                        )
                    },
                )
                if (state.users.isEmpty()) {
                    Text(
                        text = stringResource(R.string.users_empty),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
        }

        items(
            items = state.users,
            key = { it.id },
        ) { user ->
            UserCard(
                user = user,
                busy = state.busy,
                onAccessChange = { viewModel.setUserAccess(user.id, it) },
                onEnroll = { startEnrollment(user.name) },
                onRemove = { deleteTarget = user },
            )
        }
    }

    deleteTarget?.let { user ->
        AlertDialog(
            onDismissRequest = { deleteTarget = null },
            title = { Text(stringResource(R.string.users_remove_title, user.name)) },
            text = { Text(stringResource(R.string.users_remove_description)) },
            confirmButton = {
                TextButton(
                    onClick = {
                        deleteTarget = null
                        viewModel.deleteUser(user.id)
                    },
                ) {
                    Text(
                        text = stringResource(R.string.action_remove),
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            },
            dismissButton = {
                TextButton(onClick = { deleteTarget = null }) {
                    Text(stringResource(R.string.action_cancel))
                }
            },
        )
    }
}

@Composable
private fun EnrollmentCard(
    state: BassState,
    displayName: String,
    selectedSource: CameraSource,
    permissionDenied: Boolean,
    onDisplayNameChange: (String) -> Unit,
    onSourceSelected: (CameraSource) -> Unit,
    onStart: () -> Unit,
    onCancel: () -> Unit,
    onFrame: (ByteArray) -> Unit,
    onCameraError: (String) -> Unit,
) {
    val enrolling = state.captureFlow == CaptureFlow.ENROLL
    val cameraPreviewDescription = stringResource(R.string.console_camera_preview)
    BassCard {
        SectionTitle(
            title = stringResource(R.string.users_enroll_title),
            description = stringResource(R.string.users_enroll_description),
            action = {
                StatusChip(
                    text = state.faceStatus.count.toString(),
                    tone = ChipTone.INFO,
                )
            },
        )

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            FilterChip(
                modifier = Modifier.weight(1f),
                selected = selectedSource == CameraSource.PHONE,
                onClick = { onSourceSelected(CameraSource.PHONE) },
                enabled = state.capabilities.clientCamera && !enrolling,
                label = {
                    Text(
                        text = stringResource(R.string.console_phone_camera),
                        textAlign = TextAlign.Center,
                        modifier = Modifier.fillMaxWidth(),
                    )
                },
            )
            FilterChip(
                modifier = Modifier.weight(1f),
                selected = selectedSource == CameraSource.DEVICE,
                onClick = { onSourceSelected(CameraSource.DEVICE) },
                enabled = state.capabilities.deviceCamera && !enrolling,
                label = {
                    Text(
                        text = stringResource(R.string.console_device_camera),
                        textAlign = TextAlign.Center,
                        modifier = Modifier.fillMaxWidth(),
                    )
                },
            )
        }

        OutlinedTextField(
            value = displayName,
            onValueChange = onDisplayNameChange,
            modifier = Modifier.fillMaxWidth(),
            enabled = !enrolling,
            singleLine = true,
            label = { Text(stringResource(R.string.users_display_name)) },
            placeholder = { Text(stringResource(R.string.users_display_name_hint)) },
        )

        if (
            enrolling &&
            state.cameraSource == CameraSource.PHONE &&
            hasCameraPermission()
        ) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(280.dp)
                    .clip(RoundedCornerShape(16.dp)),
            ) {
                CameraPanel(
                    enabled = true,
                    qrMode = false,
                    onQr = {},
                    onFrame = onFrame,
                    onError = onCameraError,
                    modifier = Modifier
                        .fillMaxSize()
                        .semantics {
                            contentDescription = cameraPreviewDescription
                        },
                )
            }
        }

        if (enrolling) {
            Text(
                text = if (state.cameraSource == CameraSource.DEVICE) {
                    stringResource(R.string.users_device_capture)
                } else {
                    stringResource(R.string.users_phone_capture)
                },
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodySmall,
            )
            val progress = state.activeSessionProgress ?: 0
            val total = state.activeSessionTotal ?: 10
            LinearProgressIndicator(
                progress = { (progress.toFloat() / total.coerceAtLeast(1)).coerceIn(0f, 1f) },
                modifier = Modifier.fillMaxWidth(),
            )
            Text(
                text = stringResource(R.string.users_enroll_progress, progress, total),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.labelSmall,
            )
            state.activeSessionMessage?.takeIf { it.isNotBlank() }?.let {
                Text(
                    text = it,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            OutlinedButton(
                modifier = Modifier.fillMaxWidth(),
                onClick = onCancel,
            ) {
                Text(stringResource(R.string.action_cancel))
            }
        } else {
            Button(
                modifier = Modifier.fillMaxWidth(),
                enabled = !state.busy && displayName.isNotBlank() && when (selectedSource) {
                    CameraSource.PHONE -> state.capabilities.clientCamera
                    CameraSource.DEVICE -> state.capabilities.deviceCamera
                },
                onClick = onStart,
            ) {
                Text(stringResource(R.string.action_add_enroll))
            }
        }

        if (permissionDenied) {
            Text(
                text = stringResource(R.string.pair_permission_denied),
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
            )
        }
        Text(
            text = stringResource(R.string.users_retry_note),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.labelSmall,
        )
    }
}

@Composable
private fun UserCard(
    user: User,
    busy: Boolean,
    onAccessChange: (Boolean) -> Unit,
    onEnroll: () -> Unit,
    onRemove: () -> Unit,
) {
    val added = DateUtils.getRelativeTimeSpanString(
        user.createdAtMillis,
        System.currentTimeMillis(),
        DateUtils.MINUTE_IN_MILLIS,
    ).toString()

    BassCard {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(5.dp),
            ) {
                Text(
                    text = user.name,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    text = stringResource(R.string.users_added, added),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            StatusChip(
                text = if (user.enrolled) {
                    stringResource(R.string.status_enrolled)
                } else {
                    stringResource(R.string.status_not_enrolled)
                },
                tone = if (user.enrolled) ChipTone.SUCCESS else ChipTone.WARNING,
            )
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = if (user.faceAccess) {
                    stringResource(R.string.status_allowed)
                } else {
                    stringResource(R.string.status_blocked)
                },
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodyMedium,
            )
            Switch(
                checked = user.faceAccess,
                enabled = !busy,
                onCheckedChange = onAccessChange,
            )
        }

        if (!user.enrolled) {
            Button(
                modifier = Modifier.fillMaxWidth(),
                enabled = !busy,
                onClick = onEnroll,
            ) {
                Text(stringResource(R.string.action_enroll_face))
            }
        }
        OutlinedButton(
            modifier = Modifier.fillMaxWidth(),
            enabled = !busy,
            onClick = onRemove,
        ) {
            Text(
                text = stringResource(R.string.action_remove),
                color = MaterialTheme.colorScheme.error,
            )
        }
    }
}

@Composable
private fun hasCameraPermission(): Boolean {
    val context = LocalContext.current
    return ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
        PackageManager.PERMISSION_GRANTED
}
