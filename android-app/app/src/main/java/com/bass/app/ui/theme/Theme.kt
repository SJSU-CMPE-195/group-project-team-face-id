package com.bass.app.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val BassDarkColors = darkColorScheme(
    primary = Color(0xFFA78BFA),
    onPrimary = Color(0xFF1E1236),
    primaryContainer = Color(0xFF332259),
    onPrimaryContainer = Color(0xFFE9DDFF),
    secondary = Color(0xFFE879F9),
    onSecondary = Color(0xFF35113B),
    background = Color(0xFF080B14),
    onBackground = Color(0xFFF1F5F9),
    surface = Color(0xFF111827),
    onSurface = Color(0xFFE2E8F0),
    surfaceVariant = Color(0xFF1D2433),
    onSurfaceVariant = Color(0xFF94A3B8),
    outline = Color(0xFF465064),
    error = Color(0xFFFB7185),
    onError = Color(0xFF3D0710),
)

@Composable
fun BassTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = BassDarkColors,
        content = content,
    )
}
