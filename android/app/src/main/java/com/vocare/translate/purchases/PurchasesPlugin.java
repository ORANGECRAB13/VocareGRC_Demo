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
    private ProductDetails cachedProduct;

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
        if (billingClient != null && billingClient.isReady()) {
            ready.run();
            return;
        }
        billingClient = BillingClient
            .newBuilder(getContext())
            .setListener(purchasesUpdatedListener)
            .enablePendingPurchases(
                PendingPurchasesParams.newBuilder().enableOneTimeProducts().build()
            )
            .build();

        billingClient.startConnection(new BillingClientStateListener() {
            @Override
            public void onBillingSetupFinished(@NonNull BillingResult result) {
                if (result.getResponseCode() == BillingClient.BillingResponseCode.OK) {
                    ready.run();
                } else {
                    call.reject("billing_unavailable: " + result.getDebugMessage());
                }
            }

            @Override
            public void onBillingServiceDisconnected() {
                // Left to the next withBilling() call to reconnect. Retrying here
                // risks a tight loop on a device where Play is genuinely absent.
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
            ProductDetails.SubscriptionOfferDetails offer =
                details.getSubscriptionOfferDetails() == null || details.getSubscriptionOfferDetails().isEmpty()
                    ? null
                    : details.getSubscriptionOfferDetails().get(0);

            String price = "";
            if (offer != null && !offer.getPricingPhases().getPricingPhaseList().isEmpty()) {
                price = offer.getPricingPhases().getPricingPhaseList().get(0).getFormattedPrice();
            }

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
        withBilling(call, () -> queryProduct(call, details -> {
            List<ProductDetails.SubscriptionOfferDetails> offers = details.getSubscriptionOfferDetails();
            if (offers == null || offers.isEmpty()) {
                call.reject("no_subscription_offer");
                return;
            }

            BillingFlowParams params = BillingFlowParams.newBuilder()
                .setProductDetailsParamsList(Collections.singletonList(
                    BillingFlowParams.ProductDetailsParams.newBuilder()
                        .setProductDetails(details)
                        .setOfferToken(offers.get(0).getOfferToken())
                        .build()
                ))
                .build();

            pendingPurchase = call;
            call.setKeepAlive(true);
            BillingResult result = billingClient.launchBillingFlow(getActivity(), params);
            if (result.getResponseCode() != BillingClient.BillingResponseCode.OK) {
                pendingPurchase = null;
                call.reject("could_not_launch_billing: " + result.getDebugMessage());
            }
        }));
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
        if (cachedProduct != null) {
            handler.handle(cachedProduct);
            return;
        }
        QueryProductDetailsParams params = QueryProductDetailsParams.newBuilder()
            .setProductList(Collections.singletonList(
                QueryProductDetailsParams.Product.newBuilder()
                    .setProductId(PRODUCT_ID)
                    .setProductType(BillingClient.ProductType.SUBS)
                    .build()
            ))
            .build();

        billingClient.queryProductDetailsAsync(params, (result, productDetailsList) -> {
            if (result.getResponseCode() != BillingClient.BillingResponseCode.OK
                || productDetailsList == null
                || productDetailsList.isEmpty()) {
                call.reject("product_not_found: " + result.getDebugMessage());
                return;
            }
            cachedProduct = productDetailsList.get(0);
            handler.handle(cachedProduct);
        });
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
