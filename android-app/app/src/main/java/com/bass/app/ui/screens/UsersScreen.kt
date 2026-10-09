package com.bass.app.ui.screens

import android.text.format.DateUtils
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.bass.app.AppViewModel
import com.bass.app.BassState
import com.bass.app.CaptureFlow
import com.bass.app.PairedDevice
import com.bass.app.R
import com.bass.app.User
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
    val actor = state.identity?.user
    val isAdmin = actor?.isAdmin == true
    val isOwner = actor?.isOwner == true
    val newUserDraft by viewModel.newUserDraft.collectAsStateWithLifecycle()
    var deleteTarget by remember { mutableStateOf<User?>(null) }
    var revokeTarget by remember { mutableStateOf<PairedDevice?>(null) }

    LaunchedEffect(isAdmin, isOwner) {
        deleteTarget = null
        revokeTarget = null
    }
    LaunchedEffect(state.users, state.pairedDevices) {
        deleteTarget =
            deleteTarget?.let { target ->
                state.users.singleOrNull { it.id == target.id }
            }
        revokeTarget =
            revokeTarget?.let { target ->
                state.pairedDevices.singleOrNull {
                    it.id == target.id && it.active && (!it.userIsOwner || isOwner)
                }
            }
    }
    val inviteMaterial = state.inviteMaterial
    val inviteExportLauncher =
        rememberLauncherForActivityResult(
            ActivityResultContracts.CreateDocument("application/json"),
        ) { uri ->
            if (uri != null && inviteMaterial != null) {
                runCatching {
                    context.contentResolver.openOutputStream(uri, "wt")?.bufferedWriter()?.use {
                        it.write(inviteMaterial.content)
                    } ?: error("Could not open selected document")
                }.onSuccess { viewModel.clearInviteMaterial() }
                    .onFailure { viewModel.materialExportFailed() }
            }
        }

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        if (isAdmin) {
            item {
                CreateUserCard(
                    state = state,
                    name = newUserDraft.name,
                    pin = newUserDraft.pin,
                    onNameChange = viewModel::updateNewUserName,
                    onPinChange = viewModel::updateNewUserPin,
                    onCreate = viewModel::createUser,
                )
            }
        }

        if (state.captureFlow == CaptureFlow.ENROLL) {
            item {
                EnrollmentCard(state, viewModel::cancelActiveSession)
            }
        }

        item {
            BassCard {
                SectionTitle(
                    title = stringResource(R.string.users_people_title),
                    action = {
                        StatusChip(state.users.size.toString(), tone = ChipTone.INFO)
                    },
                )
                if (state.users.isEmpty()) {
                    Text(
                        text = stringResource(R.string.users_empty),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        items(state.users, key = { it.id }) { user ->
            UserCard(
                user = user,
                busy = state.busy,
                isAdmin = isAdmin,
                isOwner = isOwner,
                onAccessChange = { viewModel.setUserAccess(user.id, it) },
                onRoleChange = { viewModel.setUserRole(user.id, !user.isAdmin) },
                onEnroll = { viewModel.startEnrollment(user.id) },
                onRemove = { deleteTarget = user },
                onInvite = { viewModel.createPairingInvite(user.id) },
            )
        }

        if (isAdmin) {
            item {
                BassCard {
                    SectionTitle(
                        title = stringResource(R.string.devices_title),
                        action = {
                            StatusChip(
                                state.pairedDevices.count { it.active }.toString(),
                                tone = ChipTone.INFO,
                            )
                        },
                    )
                    if (state.pairedDevices.isEmpty()) {
                        Text(
                            text = stringResource(R.string.devices_empty),
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
            items(state.pairedDevices, key = { it.id }) { device ->
                DeviceCard(
                    device = device,
                    currentDeviceId = state.identity?.deviceId,
                    busy = state.busy,
                    canRevoke = device.active && (!device.userIsOwner || isOwner),
                    onRevoke = { revokeTarget = device },
                )
            }
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
                        stringResource(R.string.action_remove),
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

    revokeTarget?.let { device ->
        AlertDialog(
            onDismissRequest = { revokeTarget = null },
            title = { Text(stringResource(R.string.devices_revoke_title, device.name)) },
            text = { Text(stringResource(R.string.devices_revoke_description)) },
            confirmButton = {
                TextButton(
                    onClick = {
                        revokeTarget = null
                        viewModel.revokeDevice(device.id)
                    },
                ) {
                    Text(
                        stringResource(R.string.devices_revoke),
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            },
            dismissButton = {
                TextButton(onClick = { revokeTarget = null }) {
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
private fun CreateUserCard(
    state: BassState,
    name: String,
    pin: String,
    onNameChange: (String) -> Unit,
    onPinChange: (String) -> Unit,
    onCreate: () -> Unit,
) {
    BassCard {
        SectionTitle(
            title = stringResource(R.string.users_add_title),
        )
        OutlinedTextField(
            value = name,
            onValueChange = onNameChange,
            modifier = Modifier.fillMaxWidth(),
            enabled = !state.busy,
            singleLine = true,
            label = { Text(stringResource(R.string.users_display_name)) },
        )
        OutlinedTextField(
            value = pin,
            onValueChange = onPinChange,
            modifier = Modifier.fillMaxWidth(),
            enabled = !state.busy,
            singleLine = true,
            label = { Text(stringResource(R.string.users_new_pin)) },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
            visualTransformation = PasswordVisualTransformation(),
        )
        Button(
            modifier = Modifier.fillMaxWidth(),
            enabled = !state.busy && name.isNotBlank() && pin.length == 6,
            onClick = onCreate,
        ) {
            Text(stringResource(R.string.action_add_user))
        }
    }
}

@Composable
private fun EnrollmentCard(state: BassState, onCancel: () -> Unit) {
    val progress = state.activeSessionProgress ?: 0
    val total = state.activeSessionTotal ?: 10
    BassCard {
        SectionTitle(
            title = stringResource(R.string.users_enroll_title),
            description = stringResource(R.string.users_enroll_description),
        )
        LinearProgressIndicator(
            progress = { (progress.toFloat() / total.coerceAtLeast(1)).coerceIn(0f, 1f) },
            modifier = Modifier.fillMaxWidth(),
        )
        Text(stringResource(R.string.users_enroll_progress, progress, total))
        state.activeSessionMessage?.takeIf { it.isNotBlank() }?.let {
            Text(
                text = it,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodySmall,
            )
        }
        OutlinedButton(modifier = Modifier.fillMaxWidth(), onClick = onCancel) {
            Text(stringResource(R.string.action_cancel))
        }
    }
}

@Composable
private fun UserCard(
    user: User,
    busy: Boolean,
    isAdmin: Boolean,
    isOwner: Boolean,
    onAccessChange: (Boolean) -> Unit,
    onRoleChange: () -> Unit,
    onEnroll: () -> Unit,
    onRemove: () -> Unit,
    onInvite: () -> Unit,
) {
    val context = LocalContext.current
    val added = DateUtils.getRelativeTimeSpanString(context, user.createdAtMillis, false).toString()
    val canManage = isAdmin && (!user.isOwner || isOwner)
    BassCard {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    user.name,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    stringResource(R.string.users_added, added),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            Column(
                horizontalAlignment = Alignment.End,
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                if (user.isOwner) {
                    RoleBadge(isOwner = true)
                } else if (user.isAdmin) {
                    RoleBadge(isOwner = false)
                }
                StatusChip(
                    stringResource(
                        if (user.faceEnrolled) R.string.status_enrolled
                        else R.string.status_not_enrolled,
                    ),
                    tone = if (user.faceEnrolled) ChipTone.SUCCESS else ChipTone.WARNING,
                )
            }
        }
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                stringResource(
                    if (user.faceAccess) R.string.status_allowed else R.string.status_blocked
                ),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (isAdmin && !user.isOwner) {
                Switch(checked = user.faceAccess, enabled = !busy, onCheckedChange = onAccessChange)
            }
        }
        if (canManage) {
            OutlinedButton(
                modifier = Modifier.fillMaxWidth(),
                enabled = !busy,
                onClick = onInvite,
            ) {
                Text(stringResource(R.string.users_phone_invite))
            }
            Button(modifier = Modifier.fillMaxWidth(), enabled = !busy, onClick = onEnroll) {
                Text(stringResource(R.string.action_enroll_face))
            }
            if (isOwner && !user.isOwner) {
                OutlinedButton(
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !busy,
                    onClick = onRoleChange,
                ) {
                    Text(
                        stringResource(
                            if (user.isAdmin) R.string.users_remove_admin
                            else R.string.users_make_admin,
                        )
                    )
                }
            }
            if (!user.isOwner) {
                OutlinedButton(
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !busy,
                    onClick = onRemove,
                ) {
                    Text(
                        stringResource(R.string.action_remove),
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
        }
    }
}

@Composable
private fun RoleBadge(isOwner: Boolean) {
    val colors = MaterialTheme.colorScheme
    val label =
        stringResource(
            if (isOwner) R.string.users_owner else R.string.users_admin,
        )
    Surface(
        color = if (isOwner) colors.primaryContainer else colors.secondaryContainer,
        contentColor = if (isOwner) colors.onPrimaryContainer else colors.onSecondaryContainer,
        shape = CircleShape,
    ) {
        Icon(
            painter =
                painterResource(
                    if (isOwner) R.drawable.ic_role_owner else R.drawable.ic_role_admin,
                ),
            contentDescription = label,
            modifier = Modifier.padding(6.dp).size(16.dp),
        )
    }
}

@Composable
private fun DeviceCard(
    device: PairedDevice,
    currentDeviceId: String?,
    busy: Boolean,
    canRevoke: Boolean,
    onRevoke: () -> Unit,
) {
    val context = LocalContext.current
    val lastSeen = DateUtils.getRelativeTimeSpanString(context, device.lastSeenMillis, false)
    BassCard {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(device.name, style = MaterialTheme.typography.titleMedium)
                Text(
                    device.userName,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.bodySmall,
                )
                Text(
                    stringResource(R.string.devices_last_seen, lastSeen),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.labelSmall,
                )
            }
            StatusChip(
                stringResource(
                    when {
                        device.id == currentDeviceId -> R.string.devices_this_phone
                        device.active -> R.string.devices_active
                        else -> R.string.devices_revoked
                    }
                ),
                tone = if (device.active) ChipTone.SUCCESS else ChipTone.DEFAULT,
            )
        }
        if (canRevoke) {
            OutlinedButton(
                modifier = Modifier.fillMaxWidth(),
                enabled = !busy,
                onClick = onRevoke,
            ) {
                Text(
                    stringResource(R.string.devices_revoke),
                    color = MaterialTheme.colorScheme.error,
                )
            }
        }
    }
}
