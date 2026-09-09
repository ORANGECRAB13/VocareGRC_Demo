package com.vocare.translate.app

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.lifecycleScope
import com.vocare.translate.app.ui.AppViewModel
import com.vocare.translate.app.ui.VocaNavHost
import com.vocare.translate.core.theme.VocaTheme
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.launch

/**
 * `RECORD_AUDIO` as a suspending call. `:session` cannot show a permission
 * prompt itself (it has no Activity), so the activity registers the launcher
 * here and the session view model asks through this bridge.
 */
object MicPermissionBridge {
    internal var requester: (suspend () -> Boolean)? = null

    suspend fun request(): Boolean = requester?.invoke() ?: false
}

class MainActivity : ComponentActivity() {

    private var pending: CompletableDeferred<Boolean>? = null
    private var viewModel: AppViewModel? = null

    private val permissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            pending?.complete(granted)
            pending = null
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        MicPermissionBridge.requester = {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
            ) {
                true
            } else {
                val deferred = CompletableDeferred<Boolean>()
                pending = deferred
                lifecycleScope.launch { permissionLauncher.launch(Manifest.permission.RECORD_AUDIO) }
                deferred.await()
            }
        }

        val container = (application as VocaApplication).container
        val model = ViewModelProvider(this, AppViewModel.factory(container))[AppViewModel::class.java]
        viewModel = model

        // Consent first (UMP), then the ads SDK, then the App Open preload —
        // in that order, and none of it without consent. See AdMobRuntime.
        container.ads.start(this)

        setContent {
            VocaTheme {
                Box(Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.systemBars)) {
                    VocaNavHost(vm = model, ads = container.ads)
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // Play can approve a deferred purchase, or a renewal can land, while the
        // app is backgrounded; re-read the balance whenever we come forward.
        viewModel?.refreshEntitlement()
    }

    override fun onDestroy() {
        MicPermissionBridge.requester = null
        super.onDestroy()
    }
}
