package com.vocare.translate.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vocare.translate.core.model.SessionError
import com.vocare.translate.core.theme.VocaTheme

private typealias EC = VocaTheme.Colors

/**
 * Error screen (CONTRACT §5). `mic` is a permission problem the user fixes in
 * system settings; `network` is a connection that failed or dropped and is
 * worth retrying.
 */
@Composable
fun ErrorScreen(
    error: SessionError,
    onRetry: () -> Unit,
    onOpenAppSettings: () -> Unit,
    onHome: () -> Unit,
) {
    val title = if (error == SessionError.MIC) "Microphone is off" else "Connection lost"
    val body = if (error == SessionError.MIC) {
        "Voca can't translate without the microphone. Turn it on for Voca in Android settings, then start again."
    } else {
        "We couldn't reach the translation service, or the connection dropped mid-session. " +
            "Check your network and try again — on-device mode works with no network at all."
    }

    Column(
        modifier = Modifier.fillMaxSize().background(EC.Ground).padding(28.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = androidx.compose.foundation.layout.Arrangement.Center,
    ) {
        Box(
            Modifier.size(72.dp).clip(CircleShape).background(EC.ErrorLight),
            contentAlignment = Alignment.Center,
        ) { Text("!", style = VocaTheme.Type.Display.copy(fontSize = 34.sp), color = EC.Error) }
        Spacer(Modifier.height(20.dp))
        Text(title, style = VocaTheme.Type.Display.copy(fontSize = 24.sp), color = EC.Ink)
        Spacer(Modifier.height(10.dp))
        Text(body, style = VocaTheme.Type.Body, color = EC.TextMuted, textAlign = TextAlign.Center)
        Spacer(Modifier.height(28.dp))
        if (error == SessionError.MIC) {
            VocaPrimaryButton("Open app settings", onOpenAppSettings)
        } else {
            VocaPrimaryButton("Try again", onRetry)
        }
        Spacer(Modifier.height(10.dp))
        VocaSecondaryButton("Back to home", onHome)
    }
}
