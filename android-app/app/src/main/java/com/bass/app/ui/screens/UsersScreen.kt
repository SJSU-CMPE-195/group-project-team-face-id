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
import androidx.compose.foundation.text.KeyboardOptions
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
import androidx.compose.runtime.DisposableEffect
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
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
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
    val isAdmin = state.identity?.user?.isAdmin == true
    val isOwner = state.identity?.user?.isOwner == true
    val newUserDraft by viewModel.newUserDraft.collectAsStateWithLifecycle()
    var selectedSource by remember { mutableStateOf(CameraSource.PHONE) }
    var pendingUserId by remember { mutableStateOf<String?>(null) }
    var permissionDenied by remember { mutableStateOf(false) }
    var deleteTarget by remember { mutableStateOf<User?>(null) }
    val cameraPermission = Manifest.permission.CAMERA
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        permissionDenied = !granted
        val userId = pendingUserId
        pendingUserId = null
        if (granted && userId != null) {
            viewModel.startEnrollment(userId, CameraSource.PHONE)
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

    DisposableEffect(Unit) {
        onDispose {
            pendingUserId = null
        }
    }
    LaunchedEffect(isOwner) {
        if (!isOwner) viewModel.updateNewUserAdmin(false)
    }
    val inviteMaterial = state.inviteMaterial
    val inviteExportLauncher =
        rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/json")) {
            uri ->
            if (uri != null && inviteMaterial != null) {
                runCatching {
                    context.contentResolver.openOutputStream(uri, "wt")?.bufferedWriter()?.use {
                        it.write(inviteMaterial.content)
                    } ?: error("Could not open selected document")
                }.onSuccess { viewModel.clearInviteMaterial() }
                    .onFailure { viewModel.materialExportFailed() }
            }
        }

    fun startEnrollment(userId: String) {
        if (!isAdmin) return
        permissionDenied = false
        if (selectedSource == CameraSource.DEVICE || hasCameraPermission) {
            viewModel.startEnrollment(userId, selectedSource)
        } else {
            pendingUserId = userId
            permissionLauncher.launch(cameraPermission)
        }
    }

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        if (isAdmin) {
            item {
                AdminControlsCard(
                    state = state,
                    newName = newUserDraft.name,
                    newPin = newUserDraft.pin,
                    newIsAdmin = newUserDraft.isAdmin,
                    selectedSource = selectedSource,
                    permissionDenied = permissionDenied,
                    onNameChange = viewModel::updateNewUserName,
                    onPinChange = viewModel::updateNewUserPin,
                    onAdminChange = viewModel::updateNewUserAdmin,
                    canCreateAdmin = isOwner,
                    onCreate = viewModel::createUser,
                    onSourceSelected = { selectedSource = it },
                    onCancel = viewModel::cancelActiveSession,
                    onFrame = viewModel::submitEnrollmentSample,
                    onCameraError = viewModel::cameraError,
                )
            }
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
                isAdmin = isAdmin,
                isOwner = isOwner,
                onAccessChange = { viewModel.setUserAccess(user.id, it) },
                onEnroll = { startEnrollment(user.id) },
                onRemove = { deleteTarget = user },
                onInvite = { viewModel.createPairingInvite(user.id) },
            )
        }
    }

    if (isAdmin) deleteTarget?.let { user ->
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
    inviteMaterial?.let { material ->
        AlertDialog(
            onDismissRequest = viewModel::clearInviteMaterial,
            title = { Text(stringResource(R.string.invite_ready_title)) },
            text = { Text(stringResource(R.string.invite_ready_description)) },
            confirmButton = {
                TextButton(onClick = { inviteExportLauncher.launch(material.fileName) }) {
                    Text(stringResource(R.string.invite_save_action))
                }
            },
            dismissButton = {
                TextButton(onClick = viewModel::clearInviteMaterial) {
                    Text(stringResource(R.string.action_close))
                }
            },
        )
    }
}

@Composable
private fun AdminControlsCard(
    state: BassState,
    newName: String,
    newPin: String,
    newIsAdmin: Boolean,
    selectedSource: CameraSource,
    permissionDenied: Boolean,
    onNameChange: (String) -> Unit,
    onPinChange: (String) -> Unit,
    onAdminChange: (Boolean) -> Unit,
    canCreateAdmin: Boolean,
    onCreate: () -> Unit,
    onSourceSelected: (CameraSource) -> Unit,
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

        OutlinedTextField(
            value = newName,
            onValueChange = onNameChange,
            modifier = Modifier.fillMaxWidth(),
            enabled = !enrolling && !state.busy,
            singleLine = true,
            label = { Text(stringResource(R.string.users_display_name)) },
            placeholder = { Text(stringResource(R.string.users_display_name_hint)) },
        )
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(stringResource(R.string.users_make_admin))
            Switch(
                checked = newIsAdmin,
                enabled = canCreateAdmin && !enrolling && !state.busy,
                onCheckedChange = onAdminChange,
            )
        }
        OutlinedTextField(
            value = newPin,
            onValueChange = onPinChange,
            modifier = Modifier.fillMaxWidth(),
            enabled = !enrolling && !state.busy,
            singleLine = true,
            label = { Text(stringResource(R.string.users_new_pin)) },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
            visualTransformation = PasswordVisualTransformation(),
        )
        Button(
            modifier = Modifier.fillMaxWidth(),
            enabled = !enrolling && !state.busy && newName.isNotBlank() && newPin.length == 6,
            onClick = onCreate,
        ) {
            Text(stringResource(R.string.action_add_user))
        }

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
    isAdmin: Boolean,
    isOwner: Boolean,
    onAccessChange: (Boolean) -> Unit,
    onEnroll: () -> Unit,
    onRemove: () -> Unit,
    onInvite: () -> Unit,
) {
    val context = LocalContext.current
    val added =
        DateUtils.getRelativeTimeSpanString(
            context,
            user.createdAtMillis,
            false,
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
            Column(horizontalAlignment = Alignment.End) {
                if (user.isOwner) {
                    StatusChip(
                        text = stringResource(R.string.users_owner),
                        tone = ChipTone.SUCCESS,
                    )
                } else if (user.isAdmin) {
                    StatusChip(
                        text = stringResource(R.string.users_admin),
                        tone = ChipTone.INFO,
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
            if (isAdmin && !user.isOwner) {
                Switch(
                    checked = user.faceAccess,
                    enabled = !busy,
                    onCheckedChange = onAccessChange,
                )
            }
        }

        if (isAdmin && (!user.isOwner || isOwner)) {
            OutlinedButton(
                modifier = Modifier.fillMaxWidth(),
                enabled = !busy,
                onClick = onInvite,
            ) {
                Text(stringResource(R.string.users_phone_invite))
            }
            Button(
                modifier = Modifier.fillMaxWidth(),
                enabled = !busy,
                onClick = onEnroll,
            ) {
                Text(stringResource(R.string.action_enroll_face))
            }
            if (!user.isOwner) {
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
    }
}

@Composable
private fun hasCameraPermission(): Boolean {
    val context = LocalContext.current
    return ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
        PackageManager.PERMISSION_GRANTED
}
