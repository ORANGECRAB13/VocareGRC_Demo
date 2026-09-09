
# ads/AdsRuntime.kt instantiates the AdMob-backed runtime by name so that a
# build with vocare.ads=false can drop the class (and the SDK) entirely.
-keep class com.vocare.translate.app.ads.sdk.AdMobRuntime { <init>(...); }
