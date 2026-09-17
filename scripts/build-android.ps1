param(
    [string]$JavaHome = $env:JAVA_HOME,
    [string]$AndroidSdk = $env:ANDROID_HOME
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$androidProject = Join-Path $projectRoot 'android-app'

if (-not $AndroidSdk) {
    $AndroidSdk = Join-Path $env:LOCALAPPDATA 'Android/Sdk'
}
if (-not $JavaHome) {
    throw 'Specify -JavaHome with a JDK 17 directory, or set JAVA_HOME for this shell.'
}
$javaExecutable = Join-Path $JavaHome 'bin/java.exe'
if (-not (Test-Path -LiteralPath $javaExecutable)) {
    throw "Java executable not found: $javaExecutable"
}
if (-not (Test-Path -LiteralPath (Join-Path $AndroidSdk 'platforms/android-36/android.jar'))) {
    throw 'Install Android SDK Platform 36 before building, then pass its SDK root with -AndroidSdk.'
}

$originalJavaHome = $env:JAVA_HOME
$originalAndroidHome = $env:ANDROID_HOME
try {
    $env:JAVA_HOME = (Resolve-Path -LiteralPath $JavaHome).Path
    $env:ANDROID_HOME = (Resolve-Path -LiteralPath $AndroidSdk).Path
    & (Join-Path $androidProject 'gradlew.bat') -p $androidProject assembleDebug lintDebug --console=plain
    if ($LASTEXITCODE -ne 0) {
        throw "Android compilation/lint failed (exit $LASTEXITCODE)."
    }
    $apkPath = Join-Path $androidProject 'app/build/outputs/apk/debug/app-debug.apk'
    Write-Host "APK: $apkPath"
    Write-Host 'No automated tests or device UI automation were run.'
} finally {
    $env:JAVA_HOME = $originalJavaHome
    $env:ANDROID_HOME = $originalAndroidHome
}
