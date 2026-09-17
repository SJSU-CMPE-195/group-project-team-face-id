package com.bass.app.camera

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.bass.app.R
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executor
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext

@androidx.annotation.OptIn(androidx.camera.core.ExperimentalGetImage::class)
@Composable
fun CameraPanel(
    enabled: Boolean,
    qrMode: Boolean,
    onQr: (String) -> Unit,
    onFrame: (ByteArray) -> Unit,
    onError: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val previewView = remember { PreviewView(context) }
    val executor = remember { ContextCompat.getMainExecutor(context) }
    val currentQr by rememberUpdatedState(onQr)
    val currentFrame by rememberUpdatedState(onFrame)
    val currentError by rememberUpdatedState(onError)
    var capture by remember { mutableStateOf<ImageCapture?>(null) }
    AndroidView(factory = { previewView }, modifier = modifier)
    DisposableEffect(enabled, qrMode, owner) {
        var disposed = false
        var provider: ProcessCameraProvider? = null
        var preview: Preview? = null
        var analysis: ImageAnalysis? = null
        var imageCapture: ImageCapture? = null
        val scanner =
            BarcodeScanning.getClient(
                BarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build()
            )
        if (enabled) {
            val future = ProcessCameraProvider.getInstance(context)
            future.addListener(
                {
                    if (!disposed) {
                        try {
                            val camera = future.get()
                            provider = camera
                            val surface =
                                Preview.Builder().build().also {
                                    it.setSurfaceProvider(previewView.surfaceProvider)
                                }
                            preview = surface
                            val selector =
                                if (qrMode) CameraSelector.DEFAULT_BACK_CAMERA
                                else CameraSelector.DEFAULT_FRONT_CAMERA
                            check(camera.hasCamera(selector)) {
                                context.getString(R.string.core_camera_missing)
                            }
                            if (qrMode) {
                                var processing = false
                                val analyzer =
                                    ImageAnalysis.Builder()
                                        .setBackpressureStrategy(
                                            ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST
                                        )
                                        .build()
                                analysis = analyzer
                                analyzer.setAnalyzer(executor) { proxy ->
                                    val media = proxy.image
                                    if (disposed || processing || media == null) {
                                        proxy.close()
                                    } else {
                                        processing = true
                                        val input =
                                            InputImage.fromMediaImage(
                                                media,
                                                proxy.imageInfo.rotationDegrees,
                                            )
                                        scanner
                                            .process(input)
                                            .addOnSuccessListener { codes ->
                                                if (!disposed)
                                                    codes.firstOrNull()?.rawValue?.let {
                                                        currentQr(it)
                                                    }
                                            }
                                            .addOnFailureListener { error ->
                                                if (!disposed)
                                                    currentError(
                                                        error.message
                                                            ?: context.getString(
                                                                R.string.core_qr_failed
                                                            )
                                                    )
                                            }
                                            .addOnCompleteListener {
                                                processing = false
                                                proxy.close()
                                            }
                                    }
                                }
                                camera.bindToLifecycle(owner, selector, surface, analyzer)
                            } else {
                                val still =
                                    ImageCapture.Builder()
                                        .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                                        .build()
                                imageCapture = still
                                camera.bindToLifecycle(owner, selector, surface, still)
                                capture = still
                            }
                        } catch (error: Exception) {
                            currentError(
                                error.message ?: context.getString(R.string.core_camera_unavailable)
                            )
                        }
                    }
                },
                executor,
            )
        }
        onDispose {
            disposed = true
            capture = null
            analysis?.clearAnalyzer()
            val useCases = listOfNotNull(preview, analysis, imageCapture).toTypedArray()
            provider?.unbind(*useCases)
            scanner.close()
        }
    }
    LaunchedEffect(capture, enabled, qrMode) {
        val still = capture
        if (still != null && enabled && !qrMode) {
            try {
                while (true) {
                    val image = takePicture(still, executor)
                    val jpeg =
                        try {
                            withContext(Dispatchers.Default) {
                                encodeUpright(image, context.getString(R.string.core_decode_failed))
                            }
                        } finally {
                            image.close()
                        }
                    currentFrame(jpeg)
                    delay(500)
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                currentError(error.message ?: context.getString(R.string.core_capture_failed))
            }
        }
    }
}

@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
private suspend fun takePicture(capture: ImageCapture, executor: Executor): ImageProxy =
    suspendCancellableCoroutine { continuation ->
        capture.takePicture(
            executor,
            object : ImageCapture.OnImageCapturedCallback() {
                override fun onCaptureSuccess(image: ImageProxy) {
                    if (continuation.isActive) continuation.resume(image) { image.close() }
                    else image.close()
                }

                override fun onError(exception: ImageCaptureException) {
                    if (continuation.isActive) continuation.resumeWithException(exception)
                }
            },
        )
    }

private fun encodeUpright(image: ImageProxy, decodeError: String): ByteArray {
    val buffer = image.planes[0].buffer
    val bytes = ByteArray(buffer.remaining())
    buffer.get(bytes)
    val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: error(decodeError)
    val matrix = Matrix().apply { postRotate(image.imageInfo.rotationDegrees.toFloat()) }
    val rotated = Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, matrix, true)
    val ratio = 960f / maxOf(rotated.width, rotated.height)
    val resized =
        if (ratio < 1f)
            Bitmap.createScaledBitmap(
                rotated,
                (rotated.width * ratio).toInt(),
                (rotated.height * ratio).toInt(),
                true,
            )
        else rotated
    val output = ByteArrayOutputStream()
    resized.compress(Bitmap.CompressFormat.JPEG, 85, output)
    if (resized !== rotated) resized.recycle()
    if (rotated !== bitmap) rotated.recycle()
    bitmap.recycle()
    return output.toByteArray()
}
