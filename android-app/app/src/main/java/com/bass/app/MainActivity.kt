package com.bass.app

import android.content.Context
import android.content.res.Configuration
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import com.bass.app.ui.BassApp
import java.util.Locale

class MainActivity : ComponentActivity() {
    private val model: AppViewModel by viewModels()
    private val bluetoothPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { grants ->
            if (grants.values.all { it } && DeviceDiscovery.hasPermissions(this)) {
                model.onForeground()
            } else {
                model.onBluetoothPermissionDenied()
            }
        }

    override fun attachBaseContext(newBase: Context) {
        val englishConfiguration = Configuration(newBase.resources.configuration)
        englishConfiguration.setLocale(Locale.ENGLISH)
        super.attachBaseContext(newBase.createConfigurationContext(englishConfiguration))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { BassApp(model) }
    }

    override fun onStart() {
        super.onStart()
        if (DeviceDiscovery.hasPermissions(this)) {
            model.onForeground()
        } else {
            model.onBluetoothPermissionDenied()
            bluetoothPermissionLauncher.launch(DeviceDiscovery.requiredPermissions())
        }
    }

    override fun onStop() {
        model.onBackground()
        super.onStop()
    }
}
