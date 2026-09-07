package com.vocare.translate.app.ui

import android.app.Activity
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.vocare.translate.app.AppContainer
import com.vocare.translate.app.api.SessionDetail
import com.vocare.translate.app.api.SessionSummary
import com.vocare.translate.app.store.EntitlementStore
import com.vocare.translate.app.store.PurchaseOutcome
import com.vocare.translate.app.store.SessionSettings
import com.vocare.translate.app.store.SettingKey
import com.vocare.translate.app.store.StartRoute
import com.vocare.translate.app.store.StoreClient
import com.vocare.translate.core.model.SessionConfig
import com.vocare.translate.core.offline.OfflineLanguages
import com.vocare.translate.core.offline.PairStatus
import com.vocare.translate.session.cloud.CloudSessionViewModel
import com.vocare.translate.session.cloud.MicPermission
import com.vocare.translate.session.offline.OfflineSessionViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Everything the non-live screens render. One flow, so navigation stays dumb. */
data class AppUiState(
    val langA: String = "en",
    val langB: String = "zh",
    val snapshot: EntitlementStore.Snapshot = EntitlementStore.Snapshot(null, null),
    val price: String = StoreClient.PRICE_FALLBACK,
    val storeAvailable: Boolean = false,
    val micConsentGiven: Boolean = false,
    val settings: SessionSettings = SessionSettings(),
    val history: List<SessionSummary> = emptyList(),
    val historyLoading: Boolean = false,
    val detail: SessionDetail? = null,
    val purchaseBusy: Boolean = false,
    val purchaseMessage: String? = null,
    /** Offline model status per language code, for Settings' install buttons. */
    val offlineInstalled: Set<String> = emptySet(),
    val installing: String? = null,
) {
    val pairOfflineCapable: Boolean get() = OfflineLanguages.canTranslate(langA, langB)
    val offlineMode: Boolean get() = snapshot.offlineMode
}

/**
 * The app's single non-live view model: preferences, entitlement, history and
 * the purchase flow. Live sessions get their own view models from `:session`,
 * created here so a rotation does not drop the call.
 */
class AppViewModel(private val container: AppContainer) : ViewModel() {

    private val _state = MutableStateFlow(AppUiState())
    val state: StateFlow<AppUiState> = _state.asStateFlow()

    private val _cloud = MutableStateFlow<CloudSessionViewModel?>(null)
    val cloud: StateFlow<CloudSessionViewModel?> = _cloud.asStateFlow()

    private val _offline = MutableStateFlow<OfflineSessionViewModel?>(null)
    val offline: StateFlow<OfflineSessionViewModel?> = _offline.asStateFlow()

    init {
        viewModelScope.launch {
            container.preferences.offlinePref.collect { pref ->
                container.entitlements.setOfflinePref(pref)
                publish()
            }
        }
        viewModelScope.launch {
            container.preferences.micConsent.collect { given ->
                _state.update { it.copy(micConsentGiven = given) }
            }
        }
        viewModelScope.launch {
            container.preferences.settings.collect { settings ->
                _state.update { it.copy(settings = settings) }
            }
        }
        refreshEntitlement()
    }

    private fun publish() {
        _state.update { it.copy(snapshot = container.entitlements.snapshot) }
    }

    // ── entitlement / purchases ──────────────────────────────────────────────

    fun refreshEntitlement() {
        viewModelScope.launch {
            val balance = runCatching { container.purchases.syncEntitlement() }.getOrNull()
            if (balance != null) container.entitlements.update(balance)
            publish()
            if (container.entitlements.snapshot.showAds) container.ads.initialise()
        }
        viewModelScope.launch {
            val price = container.purchases.price()
            _state.update {
                it.copy(
                    price = price ?: StoreClient.PRICE_FALLBACK,
                    storeAvailable = container.purchases.storeAvailable,
                )
            }
        }
    }

    fun buyPro(activity: Activity) {
        if (_state.value.purchaseBusy) return
        _state.update { it.copy(purchaseBusy = true, purchaseMessage = null) }
        viewModelScope.launch {
            apply(container.purchases.purchasePro(activity), restore = false)
        }
    }

    fun restorePurchases() {
        if (_state.value.purchaseBusy) return
        _state.update { it.copy(purchaseBusy = true, purchaseMessage = null) }
        viewModelScope.launch { apply(container.purchases.restore(), restore = true) }
    }

    private fun apply(outcome: PurchaseOutcome, restore: Boolean) {
        val message = when (outcome) {
            is PurchaseOutcome.Purchased -> {
                container.entitlements.update(outcome.balance)
                if (restore) "Subscription restored." else "You're on Voca Pro."
            }
            PurchaseOutcome.Cancelled -> null
            PurchaseOutcome.Pending -> "Payment pending — we'll unlock Pro as soon as it clears."
            PurchaseOutcome.NoSubscription -> "No active subscription found on this account."
            is PurchaseOutcome.NotVerified -> {
                container.entitlements.update(outcome.balance)
                "We couldn't verify that purchase yet. Try again in a moment."
            }
            is PurchaseOutcome.Failed -> "Purchase failed (${outcome.reason})."
        }
        _state.update { it.copy(purchaseBusy = false, purchaseMessage = message, snapshot = container.entitlements.snapshot) }
    }

    fun clearPurchaseMessage() = _state.update { it.copy(purchaseMessage = null) }

    // ── preferences ──────────────────────────────────────────────────────────

    fun setLanguage(side: com.vocare.translate.core.model.Side, code: String) {
        _state.update {
            if (side == com.vocare.translate.core.model.Side.A) {
                it.copy(langA = code, langB = if (it.langB == code) it.langA else it.langB)
            } else {
                it.copy(langB = code, langA = if (it.langA == code) it.langB else it.langA)
            }
        }
    }

    fun swapLanguages() = _state.update { it.copy(langA = it.langB, langB = it.langA) }

    fun setOfflineMode(enabled: Boolean) {
        viewModelScope.launch { container.preferences.setOfflinePref(enabled) }
    }

    fun setSetting(key: SettingKey, value: Boolean) {
        viewModelScope.launch { container.preferences.setSetting(key, value) }
    }

    fun recordMicConsent() {
        viewModelScope.launch { container.preferences.recordMicConsent() }
    }

    // ── history ──────────────────────────────────────────────────────────────

    fun loadHistory() {
        _state.update { it.copy(historyLoading = true) }
        viewModelScope.launch {
            val rows = runCatching { container.history.list() }.getOrDefault(emptyList())
            _state.update { it.copy(history = rows, historyLoading = false) }
        }
    }

    fun loadDetail(sessionId: String) {
        _state.update { it.copy(detail = null) }
        viewModelScope.launch {
            val detail = runCatching { container.history.detail(sessionId) }.getOrNull()
            _state.update { it.copy(detail = detail) }
        }
    }

    // ── offline models ───────────────────────────────────────────────────────

    fun refreshOfflineStatus() {
        viewModelScope.launch {
            val installed = OfflineLanguages.CODES.filter { code ->
                code == "en" ||
                    runCatching { container.offlineEngine.pairStatus("en", code) }.getOrNull() == PairStatus.INSTALLED
            }.toSet()
            _state.update { it.copy(offlineInstalled = installed) }
        }
    }

    fun installOffline(code: String) {
        if (_state.value.installing != null) return
        _state.update { it.copy(installing = code) }
        viewModelScope.launch {
            runCatching { container.offlineEngine.prepare("en", code) }
            _state.update { it.copy(installing = null) }
            refreshOfflineStatus()
        }
    }

    // ── start routing (CONTRACT §5) ──────────────────────────────────────────

    fun startRoute(): StartRoute = _state.value.snapshot.route(_state.value.pairOfflineCapable)

    fun beginOffline() {
        endSessions()
        _offline.value = OfflineSessionViewModel(config(Double.POSITIVE_INFINITY), container.offlineEngine)
    }

    fun beginCloud(micPermission: MicPermission) {
        endSessions()
        viewModelScope.launch {
            val clientId = container.preferences.clientId()
            val snapshot = _state.value.snapshot
            val vm = CloudSessionViewModel(
                config = config(snapshot.budgetSeconds).copy(clientId = clientId),
                api = container.api,
                history = container.history,
                legFactory = container.legFactory(),
                micPermission = micPermission,
            )
            _cloud.value = vm
            vm.start()
        }
    }

    private fun config(budget: Double) = SessionConfig(
        langA = _state.value.langA,
        langB = _state.value.langB,
        clientId = "",
        budgetSeconds = budget,
    )

    fun endSessions() {
        _cloud.value?.end()
        _offline.value?.end()
        _cloud.value = null
        _offline.value = null
    }

    override fun onCleared() {
        endSessions()
    }

    companion object {
        fun factory(container: AppContainer) = object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T = AppViewModel(container) as T
        }
    }
}
