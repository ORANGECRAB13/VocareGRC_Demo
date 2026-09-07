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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vocare.translate.app.store.SettingKey
import com.vocare.translate.app.store.Tier
import com.vocare.translate.core.model.Language
import com.vocare.translate.core.offline.OfflineLanguages
import com.vocare.translate.core.theme.VocaTheme

private typealias SC = VocaTheme.Colors

/** Legal links live in one place so Settings and the store listing cannot drift. */
object LegalLinks {
    const val PRIVACY = "https://vocare.cx/privacy"
    const val TERMS = "https://vocare.cx/terms"
    const val SUPPORT = "https://vocare.cx/support"
}

/**
 * Settings (CONTRACT §5): session toggles, the subscription section, offline
 * language packs with in-app install buttons, and the legal links.
 */
@Composable
fun SettingsScreen(
    state: AppUiState,
    onToggle: (SettingKey, Boolean) -> Unit,
    onManageSubscription: () -> Unit,
    onRestore: () -> Unit,
    onInstall: (String) -> Unit,
    onOpenLink: (String) -> Unit,
    onBack: () -> Unit,
    banner: @Composable () -> Unit = {},
) {
    Column(
        Modifier.fillMaxSize().background(SC.Ground).verticalScroll(rememberScrollState()),
    ) {
        VocaTopBar("Settings", onBack = onBack)
        Column(Modifier.padding(horizontal = 20.dp)) {
            banner()

            SectionLabel("Session")
            VocaCard {
                ToggleRow("Show live notes", "Keep partial transcripts on screen while someone speaks", state.settings.notes) {
                    onToggle(SettingKey.NOTES, it)
                }
                ToggleRow("Save transcripts", "Keep finished sessions in History on this device", state.settings.saveTranscripts) {
                    onToggle(SettingKey.SAVE, it)
                }
                ToggleRow("Speak translations", "Play the translation aloud when it is ready", state.settings.speakAloud) {
                    onToggle(SettingKey.AUTOPLAY, it)
                }
                ToggleRow("Haptics", "Vibrate when a turn starts and ends", state.settings.haptics) {
                    onToggle(SettingKey.HAPTICS, it)
                }
                ToggleRow("Large text", "Bigger transcript type on the live screen", state.settings.largeText, last = true) {
                    onToggle(SettingKey.LARGE, it)
                }
            }

            SectionLabel("Subscription")
            VocaCard {
                val pro = state.snapshot.tier == Tier.PRO
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(if (pro) "Voca Pro" else "Free", style = VocaTheme.Type.BodySemi, color = SC.Ink)
                        Spacer(Modifier.height(3.dp))
                        Text(
                            if (pro) {
                                "${state.snapshot.secondsLeft / 60} of ${state.snapshot.balance?.secondsTotal?.div(60) ?: 0} " +
                                    "cloud minutes left this period"
                            } else {
                                "On-device translation, unlimited"
                            },
                            style = VocaTheme.Type.Body.copy(fontSize = 12.5.sp),
                            color = SC.TextMuted,
                        )
                    }
                    TierChip(if (pro) "Pro" else "Free", pro = pro, onClick = onManageSubscription)
                }
                Spacer(Modifier.height(12.dp))
                VocaSecondaryButton(if (pro) "Manage subscription" else "See Voca Pro", onManageSubscription)
                Spacer(Modifier.height(8.dp))
                VocaSecondaryButton("Restore purchases", onRestore, enabled = state.storeAvailable && !state.purchaseBusy)
                state.purchaseMessage?.let {
                    Spacer(Modifier.height(8.dp))
                    Text(it, style = VocaTheme.Type.Body, color = SC.ADeep)
                }
            }

            SectionLabel("On-device languages")
            VocaCard {
                Text(
                    "Downloaded language packs translate with no network. Each is around 30 MB.",
                    style = VocaTheme.Type.Body.copy(fontSize = 12.5.sp),
                    color = SC.TextMuted,
                )
                Spacer(Modifier.height(10.dp))
                OfflineLanguages.CODES.forEach { code ->
                    OfflineRow(
                        language = Language.of(code),
                        installed = code in state.offlineInstalled,
                        installing = state.installing == code,
                        onInstall = { onInstall(code) },
                    )
                }
            }

            SectionLabel("About")
            VocaCard {
                LinkRow("Privacy policy") { onOpenLink(LegalLinks.PRIVACY) }
                LinkRow("Terms of service") { onOpenLink(LegalLinks.TERMS) }
                LinkRow("Support", last = true) { onOpenLink(LegalLinks.SUPPORT) }
            }
            Spacer(Modifier.height(28.dp))
        }
    }
}

@Composable
private fun SectionLabel(text: String) {
    Spacer(Modifier.height(18.dp))
    MicroLabel(text, color = SC.InkMute)
    Spacer(Modifier.height(8.dp))
}

@Composable
private fun ToggleRow(
    title: String,
    subtitle: String,
    checked: Boolean,
    last: Boolean = false,
    onChange: (Boolean) -> Unit,
) {
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = VocaTheme.Type.BodySemi, color = SC.Ink)
            Spacer(Modifier.height(2.dp))
            Text(subtitle, style = VocaTheme.Type.Body.copy(fontSize = 12.sp), color = SC.TextMuted)
        }
        Switch(
            checked = checked,
            onCheckedChange = onChange,
            colors = SwitchDefaults.colors(
                checkedTrackColor = SC.A,
                checkedThumbColor = Color.White,
                uncheckedTrackColor = SC.SwitchOff,
                uncheckedThumbColor = Color.White,
            ),
        )
    }
    if (!last) Box(Modifier.fillMaxWidth().height(1.dp).background(SC.Divider))
}

@Composable
private fun OfflineRow(
    language: Language,
    installed: Boolean,
    installing: Boolean,
    onInstall: () -> Unit,
) {
    Row(Modifier.fillMaxWidth().padding(vertical = 7.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(language.flag, fontSize = 18.sp)
        Spacer(Modifier.width(10.dp))
        Text(language.label, style = VocaTheme.Type.Body, color = SC.Ink, modifier = Modifier.weight(1f))
        when {
            installed -> MicroLabel("Installed", color = SC.BDeep)
            installing -> MicroLabel("Downloading…", color = SC.ADeep)
            else -> Box(
                modifier = Modifier
                    .clip(VocaTheme.Radii.ButtonShape)
                    .background(SC.Surface2)
                    .clickable(onClick = onInstall)
                    .padding(horizontal = 14.dp, vertical = 7.dp),
            ) { MicroLabel("Install", color = SC.ADeep) }
        }
    }
}

@Composable
private fun LinkRow(title: String, last: Boolean = false, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(title, style = VocaTheme.Type.Body, color = SC.Ink, modifier = Modifier.weight(1f))
        Text("›", style = VocaTheme.Type.BodySemi.copy(fontSize = 18.sp), color = SC.TextFaint)
    }
    if (!last) Box(Modifier.fillMaxWidth().height(1.dp).background(SC.Divider))
}
