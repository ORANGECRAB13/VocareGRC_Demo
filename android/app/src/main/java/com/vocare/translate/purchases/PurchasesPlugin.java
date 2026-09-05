package com.vocare.translate.purchases;

import androidx.annotation.NonNull;

import com.android.billingclient.api.AcknowledgePurchaseParams;
import com.android.billingclient.api.BillingClient;
import com.android.billingclient.api.BillingClientStateListener;
import com.android.billingclient.api.BillingFlowParams;
import com.android.billingclient.api.BillingResult;
import com.android.billingclient.api.PendingPurchasesParams;
import com.android.billingclient.api.ProductDetails;
import com.android.billingclient.api.Purchase;
import com.android.billingclient.api.PurchasesUpdatedListener;
import com.android.billingclient.api.QueryProductDetailsParams;
import com.android.billingclient.api.QueryPurchasesParams;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.util.Collections;
import java.util.List;
import java.util.ArrayList;

/**
 * Auto-renewing subscription purchases via Google Play Billing.
 *
 * Mirrors the iOS PurchasesPlugin: the plugin obtains a purchase and hands the
 * opaque purchaseToken to the server, which verifies it against the Play
 * Developer API. Nothing here decides entitlement — a rooted device can say
 * anything it likes, which is why the minute balance lives server-side.
 *
 * Two Play-specific rules the code depends on:
 *   - A purchase must be acknowledged within three days or Play automatically
 *     refunds it. We acknowledge only AFTER the server has accepted the token,
 *     so a purchase we could not validate is refunded rather than silently kept.
 *   - queryPurchasesAsync is the restore path; Play has no separate restore API.
 */
@CapacitorPlugin(name = "VocarePurchases")
public class PurchasesPlugin extends Plugin {

    /** Must match App Store Connect / Play Console and PRO_PRODUCT_ID in vocare-app.jsx. */
    private static final String PRODUCT_ID = "vocare_pro_monthly";

    private BillingClient billingClient;
    private boolean connecting;
    private final List<Runnable> readyCalls = new ArrayList<>();
    private final List<PluginCall> connectionCalls = new ArrayList<>();

    /** Set for the duration of a purchase flow; Play answers on a listener, not a callback. */
    private PluginCall pendingPurchase;

    private final PurchasesUpdatedListener purchasesUpdatedListener = (result, purchases) -> {
        PluginCall call = pendingPurchase;
        pendingPurchase = null;

        if (result.getResponseCode() == BillingClient.BillingResponseCode.USER_CANCELED) {
            if (call != null) call.resolve(status("cancelled"));
            return;
        }
        if (result.getResponseCode() != BillingClient.BillingResponseCode.OK || purchases == null) {
            if (call != null) call.reject("purchase_failed: " + result.getDebugMessage());
            return;
        }

        for (Purchase purchase : purchases) {
            if (!purchase.getProducts().contains(PRODUCT_ID)) continue;
            if (purchase.getPurchaseState() == Purchase.PurchaseState.PENDING) {
                // Slow payment methods (e.g. cash at a kiosk). Not an error: the
                // purchase lands on this listener again once it clears.
                if (call != null) call.resolve(status("pending"));
                return;
            }
            if (purchase.getPurchaseState() == Purchase.PurchaseState.PURCHASED) {
                JSObject ret = status("purchased");
                ret.put("purchaseToken", purchase.getPurchaseToken());
                ret.put("productId", PRODUCT_ID);
                ret.put("acknowledged", purchase.isAcknowledged());
                if (call != null) call.resolve(ret);
                else notifyListeners("entitlementChanged", ret);
                return;
            }
        }
        if (call != null) call.resolve(status("unknown"));
    };

    // ── Connection ────────────────────────────────────────────────

    private void withBilling(PluginCall call, Runnable ready) {
        getActivity().runOnUiThread(() -> connectBilling(call, ready));
    }

    private void reject(PluginCall call, String message) {
        if (pendingPurchase == call) pendingPurchase = null;
        call.reject(message);
    }

    private void connectBilling(PluginCall call, Runnable ready) {
        if (billingClient != null && billingClient.isReady()) {
            ready.run();
            return;
        }
        readyCalls.add(ready);
        connectionCalls.add(call);
        if (connecting) return;
        connecting = true;
        if (billingClient != null) billingClient.endConnection();
        billingClient = BillingClient
            .newBuilder(getContext())
            .setListener(purchasesUpdatedListener)
            .enableAutoServiceReconnection()
            .enablePendingPurchases(
                PendingPurchasesParams.newBuilder().enableOneTimeProducts().build()
            )
            .build();

        billingClient.startConnection(new BillingClientStateListener() {
            @Override
            public void onBillingSetupFinished(@NonNull BillingResult result) {
                connecting = false;
                List<Runnable> callbacks = new ArrayList<>(readyCalls);
                List<PluginCall> calls = new ArrayList<>(connectionCalls);
                readyCalls.clear();
                connectionCalls.clear();
                if (result.getResponseCode() == BillingClient.BillingResponseCode.OK) {
                    for (Runnable callback : callbacks) callback.run();
                } else {
                    for (PluginCall waiting : calls) reject(waiting, "billing_unavailable: " + result.getDebugMessage());
                }
            }

            @Override
            public void onBillingServiceDisconnected() {
                connecting = false;
                for (PluginCall waiting : connectionCalls) reject(waiting, "billing_disconnected");
                readyCalls.clear();
                connectionCalls.clear();
            }
        });
    }

    // ── Methods ───────────────────────────────────────────────────

    @PluginMethod
    public void isAvailable(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("platform", "android");
        withBilling(call, () -> {
            ret.put("available", true);
            call.resolve(ret);
        });
    }

    @PluginMethod
    public void getProduct(PluginCall call) {
        withBilling(call, () -> queryProduct(call, details -> {
            ProductDetails.SubscriptionOfferDetails offer = monthlyOffer(details);
            if (offer == null) {
                reject(call, "monthly_subscription_unavailable");
                return;
            }
            String price = offer.getPricingPhases().getPricingPhaseList().get(0).getFormattedPrice();

            JSObject ret = new JSObject();
            ret.put("productId", details.getProductId());
            ret.put("displayPrice", price);
            ret.put("displayName", details.getName());
            ret.put("description", details.getDescription());
            call.resolve(ret);
        }));
    }

    @PluginMethod
    public void purchase(PluginCall call) {
        getActivity().runOnUiThread(() -> {
          if (pendingPurchase != null) {
              call.reject("purchase_in_progress");
              return;
          }
          pendingPurchase = call;
          withBilling(call, () -> queryProduct(call, details -> {
            ProductDetails.SubscriptionOfferDetails offer = monthlyOffer(details);
            if (offer == null) {
                reject(call, "monthly_subscription_unavailable");
                return;
            }

            BillingFlowParams params = BillingFlowParams.newBuilder()
                .setProductDetailsParamsList(Collections.singletonList(
                    BillingFlowParams.ProductDetailsParams.newBuilder()
                        .setProductDetails(details)
                        .setOfferToken(offer.getOfferToken())
                        .build()
                ))
                .build();

            BillingResult result = billingClient.launchBillingFlow(getActivity(), params);
            if (result.getResponseCode() != BillingClient.BillingResponseCode.OK) {
                pendingPurchase = null;
                reject(call, "could_not_launch_billing: " + result.getDebugMessage());
            }
          }));
        });
    }

    /** Play's restore: whatever this account already owns on this device. */
    @PluginMethod
    public void restore(PluginCall call) {
        currentEntitlement(call);
    }

    @PluginMethod
    public void currentEntitlement(PluginCall call) {
        withBilling(call, () -> billingClient.queryPurchasesAsync(
            QueryPurchasesParams.newBuilder().setProductType(BillingClient.ProductType.SUBS).build(),
            (result, purchases) -> {
                if (result.getResponseCode() != BillingClient.BillingResponseCode.OK) {
                    call.reject("query_purchases_failed: " + result.getDebugMessage());
                    return;
                }
                for (Purchase purchase : purchases) {
                    if (!purchase.getProducts().contains(PRODUCT_ID)) continue;
                    if (purchase.getPurchaseState() != Purchase.PurchaseState.PURCHASED) continue;
                    JSObject ret = new JSObject();
                    ret.put("active", true);
                    ret.put("purchaseToken", purchase.getPurchaseToken());
                    ret.put("productId", PRODUCT_ID);
                    ret.put("acknowledged", purchase.isAcknowledged());
                    call.resolve(ret);
                    return;
                }
                JSObject ret = new JSObject();
                ret.put("active", false);
                call.resolve(ret);
            }
        ));
    }

    /**
     * Called by the webview once the server has validated the token. Play
     * auto-refunds anything unacknowledged after three days, so acknowledging
     * only after server validation means a token we cannot verify costs the
     * user nothing.
     */
    @PluginMethod
    public void acknowledge(PluginCall call) {
        String token = call.getString("purchaseToken");
        if (token == null || token.isEmpty()) {
            call.reject("purchaseToken required");
            return;
        }
        withBilling(call, () -> billingClient.acknowledgePurchase(
            AcknowledgePurchaseParams.newBuilder().setPurchaseToken(token).build(),
            result -> {
                if (result.getResponseCode() == BillingClient.BillingResponseCode.OK) {
                    call.resolve(status("acknowledged"));
                } else {
                    call.reject("acknowledge_failed: " + result.getDebugMessage());
                }
            }
        ));
    }

    // ── Helpers ───────────────────────────────────────────────────

    private interface ProductHandler {
        void handle(ProductDetails details);
    }

    private void queryProduct(PluginCall call, ProductHandler handler) {
        QueryProductDetailsParams params = QueryProductDetailsParams.newBuilder()
            .setProductList(Collections.singletonList(
                QueryProductDetailsParams.Product.newBuilder()
                    .setProductId(PRODUCT_ID)
                    .setProductType(BillingClient.ProductType.SUBS)
                    .build()
            ))
            .build();

        billingClient.queryProductDetailsAsync(params, (result, queryResult) -> {
            List<ProductDetails> productDetailsList = queryResult.getProductDetailsList();
            if (result.getResponseCode() != BillingClient.BillingResponseCode.OK
                || productDetailsList == null
                || productDetailsList.isEmpty()) {
                reject(call, "product_not_found: " + result.getDebugMessage());
                return;
            }
            handler.handle(productDetailsList.get(0));
        });
    }

    // The paywall promises a monthly price, without a trial. Select the matching
    // base plan, not an arbitrary introductory offer or yearly plan.
    private ProductDetails.SubscriptionOfferDetails monthlyOffer(ProductDetails details) {
        if (details.getSubscriptionOfferDetails() == null) return null;
        for (ProductDetails.SubscriptionOfferDetails offer : details.getSubscriptionOfferDetails()) {
            List<ProductDetails.PricingPhase> phases = offer.getPricingPhases().getPricingPhaseList();
            if (offer.getOfferId() == null && phases.size() == 1
                    && "P1M".equals(phases.get(0).getBillingPeriod())
                    && phases.get(0).getRecurrenceMode() == ProductDetails.RecurrenceMode.INFINITE_RECURRING) {
                return offer;
            }
        }
        return null;
    }

    @Override
    protected void handleOnResume() {
        super.handleOnResume();
        notifyListeners("entitlementChanged", new JSObject());
    }

    private static JSObject status(String value) {
        JSObject ret = new JSObject();
        ret.put("status", value);
        return ret;
    }

    @Override
    protected void handleOnDestroy() {
        if (billingClient != null) {
            billingClient.endConnection();
            billingClient = null;
        }
        super.handleOnDestroy();
    }
}
