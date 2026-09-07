package com.vocare.translate.app

import android.app.Application

/** Owns the [AppContainer] for the process. Nothing heavy happens here — every field is lazy. */
class VocaApplication : Application() {
    val container: AppContainer by lazy { AppContainer(this) }
}
