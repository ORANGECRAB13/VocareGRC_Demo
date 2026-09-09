import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
}

/**
 * The single switch that decides whether this is a free build or a paid one:
 * `-Pvocare.paidTier=true` (or VOCARE_PAID_TIER=true). It drives
 * BuildConfig.PAID_TIER_ENABLED, which of `src/free` / `src/paid` is compiled,
 * and whether the AdMob SDK is linked at all. See native/android/README.md.
 */
val paidTierEnabled: Boolean =
    ((project.findProperty("vocare.paidTier") as String?) ?: System.getenv("VOCARE_PAID_TIER") ?: "false")
        .equals("true", ignoreCase = true)

// Flipping the flag alone is not enough: the free manifest deliberately drops
// the BILLING permission and the AdMob application id, and an APK built with
// the paid code but the free manifest would fail to purchase (and crash the ads
// SDK's init provider). Fail early with the checklist instead.
if (paidTierEnabled) {
    val manifest = file("src/main/AndroidManifest.xml").readText()
    val missing = buildList {
        if (!manifest.contains("<uses-permission android:name=\"com.android.vending.BILLING\" />")) {
            add("the com.android.vending.BILLING <uses-permission> (and remove the tools:node=\"remove\" one)")
        }
        if (!manifest.contains("com.google.android.gms.ads.APPLICATION_ID")) {
            add("the com.google.android.gms.ads.APPLICATION_ID <meta-data> with \${admobAppId} — only if ads go live")
        }
    }
    if (missing.isNotEmpty()) {
        logger.warn(
            "vocare.paidTier=true but src/main/AndroidManifest.xml is still the free one: add " +
                missing.joinToString("; ") + ". See native/android/README.md, \"Turning the paid tier on\".",
        )
    }
}

android {
    // The namespace (Kotlin package / R / BuildConfig) deliberately differs from
    // the applicationId: `native` is a Java keyword, and BuildConfig is generated
    // as Java source, so it cannot live in a package containing that segment.
    namespace = "com.vocare.translate.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.vocare.translate.native"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // Backend base URL and ad ids come from the environment at build time;
        // nothing secret is committed. Google's sample AdMob app id keeps the
        // SDK's startup content provider satisfied in ad-free builds (it can
        // never serve production ads). The banner unit id defaults to EMPTY,
        // which AdsService reads as "never initialise the ads SDK".
        buildConfigField(
            "String", "VOCARE_API_BASE",
            "\"${System.getenv("VOCARE_API_BASE") ?: "https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io"}\"",
        )
        // Build-time kill switch for the whole paid surface. FALSE ships a plain
        // free app: no paywall destination in the nav graph, no upgrade
        // affordance, no Subscribe button anywhere — the billing code stays in
        // the source, but nothing can reach it, whatever the backend says. Flip
        // it with `-Pvocare.paidTier=true` (or VOCARE_PAID_TIER=true) once
        // `vocare_pro_monthly` exists in Play Console. See native/android/README.md.
        buildConfigField("boolean", "PAID_TIER_ENABLED", paidTierEnabled.toString())
        buildConfigField(
            "String", "VOCARE_ADMOB_BANNER_UNIT_ID",
            "\"${System.getenv("VOCARE_ADMOB_ANDROID_BANNER") ?: ""}\"",
        )
        // The AdMob application-id placeholder only exists in a paid build; the
        // free manifest carries no com.google.android.gms.ads.APPLICATION_ID
        // meta-data because the SDK is not linked, so nothing needs it.
        if (paidTierEnabled) {
            manifestPlaceholders["admobAppId"] =
                System.getenv("VOCARE_ADMOB_ANDROID_APP_ID") ?: "ca-app-pub-3940256099942544~3347511713"
        }
    }

    // Exactly one ads implementation is compiled: a no-op in the free build (no
    // play-services-ads on the classpath at all, so the shipped binary contains
    // no advertising SDK, matching the published privacy policy) and the real
    // AdMob banner in a paid build.
    sourceSets.getByName("main").java.srcDir(if (paidTierEnabled) "src/paid/java" else "src/free/java")

    // Release signing material is read from keystore.properties (git-ignored)
    // when present; otherwise the release build type simply stays unsigned so
    // CI that only runs assembleDebug keeps working.
    val keystoreProperties = rootProject.file("keystore.properties")
    if (keystoreProperties.exists()) {
        val props = Properties().apply { keystoreProperties.inputStream().use { load(it) } }
        signingConfigs {
            create("release") {
                storeFile = file(props.getProperty("storeFile"))
                storePassword = props.getProperty("storePassword")
                keyAlias = props.getProperty("keyAlias")
                keyPassword = props.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (keystoreProperties.exists()) signingConfig = signingConfigs.getByName("release")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_21
        targetCompatibility = JavaVersion.VERSION_21
    }

    packaging {
        resources.excludes += setOf("META-INF/AL2.0", "META-INF/LGPL2.1")
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

kotlin {
    jvmToolchain(21)
}

dependencies {
    implementation(project(":core"))
    implementation(project(":session"))

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.process)
    implementation(libs.androidx.datastore.preferences)

    implementation(libs.androidx.room.runtime)
    implementation(libs.androidx.room.ktx)
    ksp(libs.androidx.room.compiler)

    implementation(libs.retrofit)
    implementation(libs.retrofit.converter.kotlinx.serialization)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)

    implementation(libs.play.billing)
    implementation(libs.play.billing.ktx)
    // Advertising SDK: paid builds only. See `paidTierEnabled` above.
    if (paidTierEnabled) implementation(libs.play.services.ads)

    debugImplementation(libs.androidx.compose.ui.tooling)
    debugImplementation(libs.androidx.compose.ui.test.manifest)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.turbine)
    testImplementation(libs.okhttp.mockwebserver)

    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.androidx.test.espresso.core)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
}
