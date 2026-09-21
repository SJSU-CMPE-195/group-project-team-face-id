package com.bass.app

import android.content.Context
import android.content.res.Configuration
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import com.bass.app.ui.BassApp
import java.util.Locale

class MainActivity : ComponentActivity() {
    private val model: AppViewModel by viewModels()

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
        model.onForeground()
    }

    override fun onStop() {
        model.onBackground()
        super.onStop()
    }
}
