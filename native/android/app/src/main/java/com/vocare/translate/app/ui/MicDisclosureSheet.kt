package com.vocare.translate.app.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vocare.translate.core.theme.VocaTheme

private typealias MC = VocaTheme.Colors

/**
 * Prominent microphone disclosure, shown once before the first mic use.
 * Google Play requires this *before* the runtime permission prompt, and it must
 * say what is recorded, why, and where it goes.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MicDisclosureSheet(
    onContinue: () -> Unit,
    onDismiss: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = MC.Surface,
    ) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 8.dp)) {
            Text("Voca needs your microphone", style = VocaTheme.Type.Display.copy(fontSize = 22.sp), color = MC.Ink)
            Spacer(Modifier.height(12.dp))
            Text(
                "While you hold the talk button, Voca records what is said so it can be translated.",
                style = VocaTheme.Type.Body.copy(fontSize = 15.sp, lineHeight = 22.sp),
                color = MC.Ink,
            )
            Spacer(Modifier.height(10.dp))
            Text(
                "In on-device mode the audio never leaves this phone. In a cloud session the audio is sent to " +
                    "Voca's translation service for as long as the session lasts, and is not used for anything else. " +
                    "Recording only happens while the button is held.",
                style = VocaTheme.Type.Body.copy(fontSize = 14.sp, lineHeight = 21.sp),
                color = MC.TextMuted,
            )
            Spacer(Modifier.height(24.dp))
            VocaPrimaryButton("Continue", onContinue)
            Spacer(Modifier.height(10.dp))
            VocaSecondaryButton("Not now", onDismiss)
            Spacer(Modifier.height(24.dp))
        }
    }
}
