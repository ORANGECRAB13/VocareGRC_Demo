package com.vocare.translate.app.ui

import androidx.compose.foundation.background
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
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vocare.translate.app.store.PaywallReason
import com.vocare.translate.app.store.StoreClient
import com.vocare.translate.core.theme.VocaTheme

private typealias PC = VocaTheme.Colors

object PaywallTags {
    const val HEADLINE = "paywall.headline"
    const val BUY = "paywall.buy"
    const val RESTORE = "paywall.restore"
    const val PRICE = "paywall.price"
}

/**
 * Paywall (CONTRACT §5). The three variants differ only in the framing copy:
 * `upsell` is an offer, `unsupported` explains why on-device could not run this
 * pair, and `exhausted` is what a spent balance says. The price always comes
 * from the store when it answered, so it is localized.
 */
@Composable
fun PaywallScreen(
    reason: PaywallReason,
    price: String,
    storeAvailable: Boolean,
    busy: Boolean,
    message: String?,
    onBuy: () -> Unit,
    onRestore: () -> Unit,
    /** Non-null once the user is subscribed: Play's own management page. */
    onManageInPlay: (() -> Unit)? = null,
    onClose: () -> Unit,
) {
    val headline = when (reason) {
        PaywallReason.UPSELL -> "Go Pro"
        PaywallReason.UNSUPPORTED -> "This pair needs the cloud"
        PaywallReason.EXHAUSTED -> "You're out of minutes"
    }
    val body = when (reason) {
        PaywallReason.UPSELL ->
            "Voca Pro adds ${StoreClient.PLAN_MINUTES} minutes of live cloud translation a month, " +
                "with natural voices and no ads."
        PaywallReason.UNSUPPORTED ->
            "These two languages have no on-device model on this phone. Cloud translation handles them; " +
                "Pro includes ${StoreClient.PLAN_MINUTES} minutes a month."
        PaywallReason.EXHAUSTED ->
            "This month's cloud minutes are used up. They reset at the start of the next billing period, " +
                "or keep going on-device for free."
    }

    Column(
        Modifier.fillMaxSize().background(PC.Ground).verticalScroll(rememberScrollState()),
    ) {
        VocaTopBar("Voca Pro", onBack = onClose)
        Column(Modifier.padding(horizontal = 20.dp)) {
            Text(
                headline,
                style = VocaTheme.Type.Display.copy(fontSize = 28.sp, lineHeight = 32.sp),
                color = PC.Ink,
                modifier = Modifier.testTag(PaywallTags.HEADLINE),
            )
            Spacer(Modifier.height(10.dp))
            Text(body, style = VocaTheme.Type.Body.copy(fontSize = 15.sp, lineHeight = 22.sp), color = PC.TextMuted)
            Spacer(Modifier.height(20.dp))

            VocaCard(background = PC.Surface2) {
                Row(verticalAlignment = Alignment.Bottom) {
                    Text(
                        price,
                        style = VocaTheme.Type.Display.copy(fontSize = 30.sp),
                        color = PC.Ink,
                        modifier = Modifier.testTag(PaywallTags.PRICE),
                    )
                    Spacer(Modifier.width(6.dp))
                    Text("/ month", style = VocaTheme.Type.Body, color = PC.TextMuted)
                }
                Spacer(Modifier.height(12.dp))
                Benefit("${StoreClient.PLAN_MINUTES} minutes of cloud translation each month")
                Benefit("On-device mode stays free and unlimited")
                Benefit("No ads, anywhere in the app")
                Benefit("Cancel any time in Google Play")
            }

            Spacer(Modifier.height(20.dp))
            VocaPrimaryButton(
                label = if (busy) "Working…" else "Subscribe · $price",
                onClick = onBuy,
                enabled = storeAvailable && !busy,
                modifier = Modifier.testTag(PaywallTags.BUY),
            )
            Spacer(Modifier.height(10.dp))
            VocaSecondaryButton(
                label = "Restore purchases",
                onClick = onRestore,
                enabled = storeAvailable && !busy,
                modifier = Modifier.testTag(PaywallTags.RESTORE),
            )
            onManageInPlay?.let {
                Spacer(Modifier.height(10.dp))
                VocaSecondaryButton(label = "Cancel or change plan in Google Play", onClick = it)
            }
            if (!storeAvailable) {
                Spacer(Modifier.height(10.dp))
                Text(
                    "Google Play billing isn't available on this device.",
                    style = VocaTheme.Type.Body,
                    color = PC.Error,
                )
            }
            message?.let {
                Spacer(Modifier.height(12.dp))
                Text(it, style = VocaTheme.Type.BodyMedium, color = PC.ADeep)
            }
            Spacer(Modifier.height(16.dp))
            Text(
                "Subscriptions renew automatically until cancelled. Managed by Google Play.",
                style = VocaTheme.Type.Body.copy(fontSize = 12.sp),
                color = PC.TextFaint,
            )
            Spacer(Modifier.height(28.dp))
        }
    }
}

@Composable
private fun Benefit(text: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Text("•", color = PC.A, style = VocaTheme.Type.BodySemi)
        Spacer(Modifier.width(10.dp))
        Text(text, style = VocaTheme.Type.Body, color = PC.Ink)
    }
}
