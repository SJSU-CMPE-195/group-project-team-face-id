plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

val releaseSigningEnvironment =
    mapOf(
        "BASS_ANDROID_KEYSTORE" to providers.environmentVariable("BASS_ANDROID_KEYSTORE").orNull,
        "BASS_ANDROID_STORE_PASSWORD" to
            providers.environmentVariable("BASS_ANDROID_STORE_PASSWORD").orNull,
        "BASS_ANDROID_KEY_ALIAS" to providers.environmentVariable("BASS_ANDROID_KEY_ALIAS").orNull,
        "BASS_ANDROID_KEY_PASSWORD" to
            providers.environmentVariable("BASS_ANDROID_KEY_PASSWORD").orNull,
    )
val releaseSigningConfigured = releaseSigningEnvironment.values.all { !it.isNullOrBlank() }
val releaseSigningPartiallyConfigured = releaseSigningEnvironment.values.any { !it.isNullOrBlank() }
if (releaseSigningPartiallyConfigured && !releaseSigningConfigured) {
    throw GradleException(
        "Set all BASS_ANDROID_* signing variables or leave all of them unset.",
    )
}

android {
    namespace = "com.bass.app"
    compileSdk = 36
    defaultConfig {
        applicationId = "com.bass.app"
        minSdk = 26
        targetSdk = 36
        versionCode = 5
        versionName = "0.4.0"
    }
    androidResources { localeFilters += listOf("en") }
    buildFeatures { compose = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    signingConfigs {
        if (releaseSigningConfigured) {
            create("release") {
                val keystore = file(checkNotNull(releaseSigningEnvironment["BASS_ANDROID_KEYSTORE"]))
                if (!keystore.isFile) {
                    throw GradleException("BASS_ANDROID_KEYSTORE does not name a file.")
                }
                storeFile = keystore
                storePassword = releaseSigningEnvironment["BASS_ANDROID_STORE_PASSWORD"]
                keyAlias = releaseSigningEnvironment["BASS_ANDROID_KEY_ALIAS"]
                keyPassword = releaseSigningEnvironment["BASS_ANDROID_KEY_PASSWORD"]
            }
        }
    }
    buildTypes {
        getByName("release") {
            if (releaseSigningConfigured) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }
    bundle { language { enableSplit = false } }
}
dependencies {
    implementation(platform("androidx.compose:compose-bom:2025.10.01"))
    implementation("androidx.activity:activity-compose:1.11.0")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.4")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.4")
    implementation("androidx.camera:camera-camera2:1.5.1")
    implementation("androidx.camera:camera-lifecycle:1.5.1")
    implementation("androidx.camera:camera-view:1.5.1")
    implementation("com.google.mlkit:barcode-scanning:17.3.0")
}
