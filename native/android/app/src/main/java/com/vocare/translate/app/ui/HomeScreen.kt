package com.vocare.translate.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vocare.translate.core.model.Language
import com.vocare.translate.core.model.Side
import com.vocare.translate.core.theme.VocaTheme

private typealias HC = VocaTheme.Colors

object HomeTags {
    const val START = "home.start"
    const val OFFLINE_SWITCH = "home.offlineSwitch"
    const val TIER_CHIP = "home.tierChip"
}

/**
 * Home (CONTRACT §5): logo, tier chip, mascot, offline switch, the language
 * pair card and Start. The banner sits above the Start button and only for a
 * metered free tier — never on a live screen.
 */
@Composable
fun HomeScreen(
    state: AppUiState,
    onStart: () -> Unit,
    onPickLanguage: (Side) -> Unit,
    onSwapLanguages: () -> Unit,
    onOfflineToggle: (Boolean) -> Unit,
    onOpenHistory: () -> Unit,
    onOpenSettings: () -> Unit,
    /** Null when this build has no paid surface: the chip is then a plain, inert status label. */
    onOpenPaywall: (() -> Unit)?,
    banner: @Composable () -> Unit = {},
) {
    val pro = state.snapshot.tier == com.vocare.translate.app.store.Tier.PRO
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(HC.Ground)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().height(64.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            VocaLogo(Modifier.weight(1f))
            IconPill("≡", onOpenSettings)
        }

        TierChip(
            text = if (pro) "Pro · ${state.snapshot.secondsLeft / 60} min left" else "Free · on device",
            pro = pro,
            onClick = onOpenPaywall,
            modifier = Modifier.testTag(HomeTags.TIER_CHIP),
        )

        Spacer(Modifier.height(18.dp))
        Mascot(Modifier.fillMaxWidth().padding(vertical = 4.dp))
        Spacer(Modifier.height(18.dp))

        Text(
            "Face to face,\nin any language.",
            style = VocaTheme.Type.Display.copy(fontSize = 28.sp, lineHeight = 32.sp),
            color = HC.Ink,
        )
        Spacer(Modifier.height(16.dp))

        VocaCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    MicroLabel("On-device mode")
                    Spacer(Modifier.height(4.dp))
                    Text(
                        if (state.pairOfflineCapable) {
                            "Translate with no network. Nothing leaves the phone."
                        } else {
                            "Not available for this language pair."
                        },
                        style = VocaTheme.Type.Body,
                        color = HC.TextMuted,
                    )
                }
                Switch(
                    checked = state.offlineMode,
                    onCheckedChange = onOfflineToggle,
                    modifier = Modifier.testTag(HomeTags.OFFLINE_SWITCH),
                    colors = SwitchDefaults.colors(
                        checkedTrackColor = HC.A,
                        checkedThumbColor = Color.White,
                        uncheckedTrackColor = HC.SwitchOff,
                        uncheckedThumbColor = Color.White,
                    ),
                )
            }
        }

        Spacer(Modifier.height(12.dp))

        VocaCard {
            MicroLabel("Language pair")
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                LanguageTile(
                    label = "You",
                    language = Language.of(state.langA),
                    tint = HC.TileA,
                    onClick = { onPickLanguage(Side.A) },
                    modifier = Modifier.weight(1f),
                )
                Box(
                    modifier = Modifier
                        .padding(horizontal = 10.dp)
                        .size(36.dp)
                        .clip(CircleShape)
                        .background(HC.Surface2)
                        .clickable(onClick = onSwapLanguages),
                    contentAlignment = Alignment.Center,
                ) { Text("⇄", color = HC.ADeep, style = VocaTheme.Type.BodySemi) }
                LanguageTile(
                    label = "Them",
                    language = Language.of(state.langB),
                    tint = HC.TileB,
                    onClick = { onPickLanguage(Side.B) },
                    modifier = Modifier.weight(1f),
                )
            }
        }

        Spacer(Modifier.height(20.dp))
        banner()
        Spacer(Modifier.height(12.dp))

        VocaPrimaryButton(
            label = "Start session",
            onClick = onStart,
            modifier = Modifier.testTag(HomeTags.START),
        )
        Spacer(Modifier.height(10.dp))
        VocaSecondaryButton(label = "History", onClick = onOpenHistory)
        Spacer(Modifier.height(28.dp))
    }
}

@Composable
private fun LanguageTile(
    label: String,
    language: Language,
    tint: Color,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .clip(RoundedCornerShape(VocaTheme.Radii.Tile))
            .background(tint)
            .clickable(onClick = onClick)
            .padding(14.dp),
    ) {
        MicroLabel(label, color = HC.InkSoft)
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(language.flag, fontSize = 20.sp)
            Spacer(Modifier.width(8.dp))
            Text(language.label, style = VocaTheme.Type.BodySemi, color = HC.Ink)
        }
    }
}

@Composable
private fun IconPill(glyph: String, onClick: () -> Unit) {
    Box(
        modifier = Modifier
            .size(38.dp)
            .clip(CircleShape)
            .background(HC.Surface)
            .border(1.dp, HC.Border, CircleShape)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { Text(glyph, color = HC.Ink, style = VocaTheme.Type.BodySemi.copy(fontSize = 18.sp)) }
}
