// :core — shared contract between :app and :session (native/CONTRACT.md §6).
// Theme tokens, the VocaApi interface, models, and the OfflineEngine /
// SessionHistoryStore interfaces. No UI screens, no network, no storage: the
// point of this module is that neither :app nor :session depends on the other.
plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "com.vocare.translate.core"
    compileSdk = 36

    defaultConfig {
        minSdk = 26
        consumerProguardFiles("consumer-rules.pro")
    }

    buildFeatures {
        compose = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_21
        targetCompatibility = JavaVersion.VERSION_21
    }
}

kotlin {
    jvmToolchain(21)
}

dependencies {
    api(platform(libs.androidx.compose.bom))
    api(libs.androidx.compose.ui)
    api(libs.androidx.compose.ui.graphics)
    api(libs.androidx.compose.foundation)
    api(libs.androidx.compose.material3)
    api(libs.kotlinx.serialization.json)
    api(libs.kotlinx.coroutines.android)

    testImplementation(libs.junit)
}
