package com.vocare.translate;

import android.os.Bundle;

import com.getcapacitor.BridgeActivity;
import com.vocare.translate.history.TranslationHistoryPlugin;
import com.vocare.translate.purchases.PurchasesPlugin;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        // Registered before super.onCreate() so the bridge exposes
        // window.Capacitor.Plugins.TranslationHistory on first page load,
        // matching the iOS VocareBridgeViewController registration.
        registerPlugin(TranslationHistoryPlugin.class);
        registerPlugin(PurchasesPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
