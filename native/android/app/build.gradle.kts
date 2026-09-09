import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
}

/**
 * Billing switch: `-Pvocare.paidTier=true` (or VOCARE_PAID_TIER=true) drives
 * BuildConfig.PAID_TIER_ENABLED, which decides whether the paywall, the
 * subscription section and Play Billing exist in the binary. It no longer has
 * anything to do with ads. See native/android/README.md.
 */
val paidTierEnabled: Boolean =
    ((project.findProperty("vocare.paidTier") as String?) ?: System.getenv("VOCARE_PAID_TIER") ?: "false")
        .equals("true", ignoreCase = true)

/**
 * Ads switch: the app ships free and ad-supported, so this defaults to TRUE in
 * every build. `-Pvocare.ads=false` (or VOCARE_ADS=false) cuts the AdMob and
 * UMP SDKs out of the binary entirely (no dependency, `ads/sdk` sources not
 * compiled, BuildConfig.ADS_ENABLED=false, `showAds` always false) for the day
 * a build must ship without advertising.
 */
val adsEnabled: Boolean =
    ((project.findProperty("vocare.ads") as String?) ?: System.getenv("VOCARE_ADS") ?: "true")
        .equals("true", ignoreCase = true)

// ── AdMob ids ─────────────────────────────────────────────────────────────────
// Google's published sample ids (https://developers.google.com/admob/android/test-ads).
// They serve test ads only and are what every debug build uses. Real ids are
// never committed: they come from the environment at build time.
val googleSamplePublisher = "ca-app-pub-3940256099942544"
val admobTestAppId = "$googleSamplePublisher~3347511713"
val admobTestAppOpenUnit = "$googleSamplePublisher/9257395921"

val admobAppId: String = System.getenv("VOCARE_ADMOB_ANDROID_APP_ID")?.takeIf { it.isNotBlank() } ?: admobTestAppId
val admobAppOpenUnit: String =
    System.getenv("VOCARE_ADMOB_ANDROID_APP_OPEN_UNIT")?.takeIf { it.isNotBlank() } ?: admobTestAppOpenUnit
/** Optional; empty means "no banner". */
val admobBannerUnit: String = System.getenv("VOCARE_ADMOB_ANDROID_BANNER") ?: ""
/** Comma-separated AdMob test device ids, so real units can be exercised on a dev phone without policy risk. */
val admobTestDeviceIds: String = System.getenv("VOCARE_ADMOB_TEST_DEVICE_IDS") ?: ""
/** UMP hashed test device id for the EEA debug-geography consent flow (debug builds only). */
val umpTestDeviceHash: String = System.getenv("VOCARE_UMP_TEST_DEVICE_HASH") ?: ""

/**
 * The release guard. Shipping Google's sample ids to production is an AdMob
 * policy violation (and earns nothing), so a release build with either id
 * still at its test default must not exist. Checked at configuration time
 * whenever a release task was requested, and again when `preReleaseBuild`
 * runs, so no task alias slips past it. Debug builds keep the test ids.
 */
val releaseAdIdProblems: List<String> = if (!adsEnabled) emptyList() else buildList {
    if (admobAppId.startsWith(googleSamplePublisher)) {
        add("VOCARE_ADMOB_ANDROID_APP_ID is unset or still Google's sample app id ($admobAppId)")
    }
    if (admobAppOpenUnit.startsWith(googleSamplePublisher)) {
        add("VOCARE_ADMOB_ANDROID_APP_OPEN_UNIT is unset or still Google's sample App Open unit ($admobAppOpenUnit)")
    }
    if (admobBannerUnit.startsWith(googleSamplePublisher)) {
        add("VOCARE_ADMOB_ANDROID_BANNER is Google's sample banner unit ($admobBannerUnit)")
    }
}
val releaseAdIdGuardMessage: String =
    "Release build refused: AdMob test ids must not ship. " + releaseAdIdProblems.joinToString("; ") +
        ". Export the real ids from the AdMob console (VOCARE_ADMOB_ANDROID_APP_ID=ca-app-pub-XXXX~YYYY, " +
        "VOCARE_ADMOB_ANDROID_APP_OPEN_UNIT=ca-app-pub-XXXX/ZZZZ) and rebuild, or build debug. " +
        "See native/android/README.md, \"Ads\"."
val releaseRequested: Boolean = gradle.startParameter.taskNames.any { name ->
    name.contains("Release", ignoreCase = true) ||
        name.substringAfterLast(':') in setOf("build", "assemble", "bundle", "publish", "install")
}
if (releaseRequested && releaseAdIdProblems.isNotEmpty()) throw GradleException(releaseAdIdGuardMessage)

// A paid build with the free manifest would fail to purchase; warn early.
if (paidTierEnabled) {
    val manifest = file("src/main/AndroidManifest.xml").readText()
    if (!manifest.contains("<uses-permission android:name=\"com.android.vending.BILLING\" />")) {
        logger.warn(
            "vocare.paidTier=true but src/main/AndroidManifest.xml is still the free one: add the " +
                "com.android.vending.BILLING <uses-permission> (and remove the tools:node=\"remove\" one). " +
                "See native/android/README.md, \"Turning the paid tier on\".",
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
        // nothing secret is committed. See the AdMob block at the top of this file.
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
        // Ads: on by default (the app is ad-supported); see `adsEnabled` above.
        buildConfigField("boolean", "ADS_ENABLED", adsEnabled.toString())
        buildConfigField("String", "VOCARE_ADMOB_APP_OPEN_UNIT_ID", "\"$admobAppOpenUnit\"")
        buildConfigField("String", "VOCARE_ADMOB_BANNER_UNIT_ID", "\"$admobBannerUnit\"")
        buildConfigField("String", "VOCARE_ADMOB_TEST_DEVICE_IDS", "\"$admobTestDeviceIds\"")
        buildConfigField("String", "VOCARE_UMP_TEST_DEVICE_HASH", "\"$umpTestDeviceHash\"")
        // The SDK's startup ContentProvider reads this meta-data and crashes the
        // process when it is missing or malformed, so it is always set — to the
        // sample id in debug, to the real one (release guard above) in release.
        manifestPlaceholders["admobAppId"] = admobAppId
    }

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

// `ads/sdk` is the only package that imports com.google.android.gms.ads or
// com.google.android.ump; `ads/AdsRuntime.kt` reaches it by name, so a build
// without ads simply does not compile it. (A filter on the Kotlin source set is
// ignored for Android source sets; the compile task's own filter is not.)
if (!adsEnabled) {
    tasks.withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompile>().configureEach { exclude("**/ads/sdk/**") }
}

// Second half of the release guard (see `releaseAdIdProblems`): fires even when
// the release variant is reached through a task alias the start-parameter
// check did not recognise.
tasks.configureEach {
    if (name == "preReleaseBuild" && releaseAdIdProblems.isNotEmpty()) {
        val message = releaseAdIdGuardMessage
        doFirst { throw GradleException(message) }
    }
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
    // Advertising: AdMob (App Open + banner) and the User Messaging Platform
    // consent SDK. Linked in every build unless `vocare.ads=false`, in which
    // case the `ads/sdk` sources are excluded too (below) so nothing references
    // them and the binary carries no advertising SDK at all.
    if (adsEnabled) {
        implementation(libs.play.services.ads)
        implementation(libs.user.messaging.platform)
    }

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
