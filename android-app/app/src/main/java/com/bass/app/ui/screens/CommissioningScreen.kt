package com.bass.app.ui.screens

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
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
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.bass.app.AppViewModel
import com.bass.app.BassState
import com.bass.app.CommissioningPurpose
import com.bass.app.R
import com.bass.app.ui.components.BassCard

@Composable
fun CommissioningScreen(state: BassState, viewModel: AppViewModel) {
    val purpose = checkNotNull(state.commissioningPrompt).purpose
    val needsName = purpose != CommissioningPurpose.RECOVERY
    var name by remember(purpose) { mutableStateOf("") }
    var pin by remember(purpose) { mutableStateOf("") }

    fun cancel() {
        name = ""
        pin = ""
        viewModel.cancelPairingCredentials()
    }

    DisposableEffect(purpose) {
        onDispose {
            name = ""
            pin = ""
        }
    }
    BackHandler(enabled = !state.busy, onBack = ::cancel)

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
                text = stringResource(purpose.titleResource()),
                modifier = Modifier.fillMaxWidth(),
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.Bold,
                textAlign = TextAlign.Center,
            )
            Text(
                text = stringResource(purpose.descriptionResource()),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodyMedium,
                textAlign = TextAlign.Center,
            )
            if (needsName) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it.take(128) },
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !state.busy,
                    singleLine = true,
                    label = { Text(stringResource(R.string.users_display_name)) },
                )
            }
            OutlinedTextField(
                value = pin,
                onValueChange = { raw -> pin = raw.filter(Char::isDigit).take(6) },
                modifier = Modifier.fillMaxWidth(),
                enabled = !state.busy,
                singleLine = true,
                label = { Text(stringResource(R.string.commissioning_new_pin)) },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                visualTransformation = PasswordVisualTransformation(),
            )
            if (state.busy) {
                CircularProgressIndicator(modifier = Modifier.align(Alignment.CenterHorizontally))
            }
            Button(
                modifier = Modifier.fillMaxWidth(),
                enabled = !state.busy && (!needsName || name.isNotBlank()) && pin.length == 6,
                onClick = {
                    val submittedPin = pin.toCharArray()
                    val submittedName = name
                    name = ""
                    pin = ""
                    viewModel.submitOwnerCommissioning(submittedName, submittedPin)
                },
            ) {
                Text(stringResource(purpose.actionResource()))
            }
            OutlinedButton(
                modifier = Modifier.fillMaxWidth(),
                enabled = !state.busy,
                onClick = ::cancel,
            ) {
                Text(stringResource(R.string.action_cancel))
            }
        }
    }
}

private fun CommissioningPurpose.titleResource(): Int =
    when (this) {
        CommissioningPurpose.ACTIVATION -> R.string.commissioning_claim_title
        CommissioningPurpose.RECOVERY -> R.string.commissioning_recover_title
        CommissioningPurpose.TRANSFER -> R.string.commissioning_transfer_accept_title
        CommissioningPurpose.DEVICE -> R.string.pair_credentials_title
    }

private fun CommissioningPurpose.descriptionResource(): Int =
    when (this) {
        CommissioningPurpose.ACTIVATION -> R.string.commissioning_claim_description
        CommissioningPurpose.RECOVERY -> R.string.commissioning_recover_description
        CommissioningPurpose.TRANSFER -> R.string.commissioning_transfer_accept_description
        CommissioningPurpose.DEVICE -> R.string.pair_credentials_description
    }

private fun CommissioningPurpose.actionResource(): Int =
    when (this) {
        CommissioningPurpose.ACTIVATION -> R.string.commissioning_claim_action
        CommissioningPurpose.RECOVERY -> R.string.commissioning_recover_action
        CommissioningPurpose.TRANSFER -> R.string.commissioning_transfer_accept_action
        CommissioningPurpose.DEVICE -> R.string.pair_authorize_phone
    }
