package com.vocare.translate.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vocare.translate.core.theme.VocaTheme

private typealias C = VocaTheme.Colors

/** Uppercase JetBrains Mono micro-label (CONTRACT §7). */
@Composable
fun MicroLabel(text: String, color: Color = C.InkMute, modifier: Modifier = Modifier) {
    Text(
        text = text.uppercase(),
        style = VocaTheme.Type.Micro,
        color = color,
        modifier = modifier,
    )
}

@Composable
fun VocaCard(
    modifier: Modifier = Modifier,
    background: Color = C.Surface,
    content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .clip(VocaTheme.Radii.CardShape)
            .background(background)
            .border(1.dp, C.Hairline, VocaTheme.Radii.CardShape)
            .padding(16.dp),
        content = content,
    )
}

/** The primary violet pill button. */
@Composable
fun VocaPrimaryButton(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(56.dp)
            .clip(VocaTheme.Radii.ButtonShape)
            .background(
                if (enabled) {
                    Brush.horizontalGradient(listOf(C.A, C.ADeep))
                } else {
                    Brush.horizontalGradient(listOf(C.SwitchOff, C.SwitchOff))
                },
            )
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, style = VocaTheme.Type.BodySemi.copy(fontSize = 16.sp), color = Color.White)
    }
}

@Composable
fun VocaSecondaryButton(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(48.dp)
            .clip(VocaTheme.Radii.ButtonShape)
            .background(C.Surface)
            .border(1.dp, C.Border, VocaTheme.Radii.ButtonShape)
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, style = VocaTheme.Type.BodySemi, color = if (enabled) C.Ink else C.InkFaint)
    }
}

/** Header row used by every non-home screen. */
@Composable
fun VocaTopBar(title: String, onBack: (() -> Unit)? = null, trailing: @Composable () -> Unit = {}) {
    Row(
        modifier = Modifier.fillMaxWidth().height(56.dp).padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (onBack != null) {
            Box(
                modifier = Modifier
                    .size(36.dp)
                    .clip(CircleShape)
                    .background(C.Surface2)
                    .clickable(onClick = onBack),
                contentAlignment = Alignment.Center,
            ) { Text("‹", style = VocaTheme.Type.BodySemi.copy(fontSize = 20.sp), color = C.Ink) }
            Spacer(Modifier.width(12.dp))
        }
        Text(title, style = VocaTheme.Type.Display.copy(fontSize = 20.sp), color = C.Ink, modifier = Modifier.weight(1f))
        trailing()
    }
}

/** Tier chip: "FREE · ON DEVICE" or "PRO · 42 MIN LEFT". */
@Composable
/** [onClick] null makes the chip a status label with no upsell affordance — no ripple, no navigation. */
fun TierChip(text: String, pro: Boolean, onClick: (() -> Unit)?, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier
            .clip(CircleShape)
            .background(if (pro) C.InkB14 else C.InkA08)
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(horizontal = 12.dp, vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(6.dp).clip(CircleShape).background(if (pro) C.B else C.A))
        Spacer(Modifier.width(7.dp))
        MicroLabel(text, color = if (pro) C.BDeep else C.ADeep)
    }
}

/** The Voca wordmark: a violet rounded tile plus the name. */
@Composable
fun VocaLogo(modifier: Modifier = Modifier) {
    Row(modifier = modifier, verticalAlignment = Alignment.CenterVertically) {
        Box(
            modifier = Modifier
                .size(34.dp)
                .clip(RoundedCornerShape(11.dp))
                .background(Brush.linearGradient(listOf(C.ALight, C.ADeep))),
            contentAlignment = Alignment.Center,
        ) { Text("V", style = VocaTheme.Type.Display.copy(fontSize = 20.sp), color = Color.White) }
        Spacer(Modifier.width(10.dp))
        Text("Voca", style = VocaTheme.Type.Display.copy(fontSize = 24.sp), color = C.Ink)
    }
}

/**
 * The home mascot. A static illustration is acceptable for v1 (CONTRACT §7);
 * this is the canvas robot reduced to its silhouette: rounded head, visor,
 * antenna, drawn from theme tokens so it stays on-palette.
 */
@Composable
fun Mascot(modifier: Modifier = Modifier) {
    Column(modifier = modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        Box(Modifier.size(width = 3.dp, height = 14.dp).background(C.InkA35))
        Box(Modifier.size(10.dp).clip(CircleShape).background(C.Accent))
        Spacer(Modifier.height(2.dp))
        Box(
            modifier = Modifier
                .size(width = 132.dp, height = 104.dp)
                .clip(RoundedCornerShape(34.dp))
                .background(Brush.verticalGradient(listOf(C.ALight, C.A))),
            contentAlignment = Alignment.Center,
        ) {
            Row(
                modifier = Modifier
                    .size(width = 92.dp, height = 46.dp)
                    .clip(RoundedCornerShape(23.dp))
                    .background(C.Ink),
                horizontalArrangement = Arrangement.spacedBy(14.dp, Alignment.CenterHorizontally),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(Modifier.size(12.dp).clip(CircleShape).background(C.BLight))
                Box(Modifier.size(12.dp).clip(CircleShape).background(C.BLight))
            }
        }
        Spacer(Modifier.height(6.dp))
        Box(
            modifier = Modifier
                .size(width = 92.dp, height = 10.dp)
                .clip(RoundedCornerShape(5.dp))
                .background(C.InkA14),
        )
    }
}

@Composable
fun EmptyState(title: String, body: String, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxWidth().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(title, style = VocaTheme.Type.Display.copy(fontSize = 18.sp), color = C.Ink)
        Spacer(Modifier.height(6.dp))
        Text(body, style = VocaTheme.Type.Body, color = C.TextMuted, textAlign = TextAlign.Center)
    }
}
