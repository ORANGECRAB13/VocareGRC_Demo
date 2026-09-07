package com.vocare.translate.app

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.vocare.translate.app.store.EntitlementStore
import com.vocare.translate.app.store.PaywallReason
import com.vocare.translate.app.ui.AppUiState
import com.vocare.translate.app.ui.HomeScreen
import com.vocare.translate.app.ui.HomeTags
import com.vocare.translate.app.ui.PaywallScreen
import com.vocare.translate.app.ui.PaywallTags
import com.vocare.translate.core.model.Balance
import com.vocare.translate.core.theme.VocaTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Instrumented UI test for Home and the Paywall. Requires a device or emulator
 * (`./gradlew :app:connectedDebugAndroidTest`); it is not part of the unit-test
 * run and has not been executed on hardware yet — see README.
 */
@RunWith(AndroidJUnit4::class)
class ScreensUiTest {

    @get:Rule val compose = createComposeRule()

    private fun state(balance: Balance?, offlinePref: Boolean? = null) = AppUiState(
        snapshot = EntitlementStore.Snapshot(balance, offlinePref),
    )

    @Test
    fun homeShowsTheFreeChipAndStartsASession() {
        var started = 0
        compose.setContent {
            VocaTheme {
                HomeScreen(
                    state = state(Balance(tier = "free", enforced = true)),
                    onStart = { started++ },
                    onPickLanguage = {},
                    onSwapLanguages = {},
                    onOfflineToggle = {},
                    onOpenHistory = {},
                    onOpenSettings = {},
                    onOpenPaywall = {},
                )
            }
        }

        compose.onNodeWithTag(HomeTags.TIER_CHIP).assertIsDisplayed()
        compose.onNodeWithText("FREE · ON DEVICE").assertIsDisplayed()
        compose.onNodeWithTag(HomeTags.START).performClick()
        assertEquals(1, started)
    }

    @Test
    fun homeShowsMinutesLeftForPro() {
        compose.setContent {
            VocaTheme {
                HomeScreen(
                    state = state(Balance(tier = "pro", secondsRemaining = 2700, enforced = true)),
                    onStart = {},
                    onPickLanguage = {},
                    onSwapLanguages = {},
                    onOfflineToggle = {},
                    onOpenHistory = {},
                    onOpenSettings = {},
                    onOpenPaywall = {},
                )
            }
        }
        compose.onNodeWithText("PRO · 45 MIN LEFT").assertIsDisplayed()
    }

    @Test
    fun paywallShowsTheExhaustedVariantAndTheStorePrice() {
        var bought = 0
        var restored = 0
        compose.setContent {
            VocaTheme {
                PaywallScreen(
                    reason = PaywallReason.EXHAUSTED,
                    price = "€24.99",
                    storeAvailable = true,
                    busy = false,
                    message = null,
                    onBuy = { bought++ },
                    onRestore = { restored++ },
                    onClose = {},
                )
            }
        }

        compose.onNodeWithTag(PaywallTags.HEADLINE).assertIsDisplayed()
        compose.onNodeWithText("You're out of minutes").assertIsDisplayed()
        compose.onNodeWithTag(PaywallTags.PRICE).assertIsDisplayed()
        compose.onNodeWithTag(PaywallTags.BUY).performClick()
        compose.onNodeWithTag(PaywallTags.RESTORE).performClick()
        assertEquals(1, bought)
        assertEquals(1, restored)
    }

    @Test
    fun paywallDisablesPurchaseWhenPlayIsMissing() {
        var bought = 0
        compose.setContent {
            VocaTheme {
                PaywallScreen(
                    reason = PaywallReason.UNSUPPORTED,
                    price = "A$29.99",
                    storeAvailable = false,
                    busy = false,
                    message = null,
                    onBuy = { bought++ },
                    onRestore = {},
                    onClose = {},
                )
            }
        }
        compose.onNodeWithText("This pair needs the cloud").assertIsDisplayed()
        compose.onNodeWithTag(PaywallTags.BUY).performClick()
        assertTrue("a disabled button must not start a purchase", bought == 0)
    }
}
