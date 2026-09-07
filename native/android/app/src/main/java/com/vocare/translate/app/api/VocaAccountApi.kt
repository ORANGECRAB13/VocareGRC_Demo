package com.vocare.translate.app.api

import com.vocare.translate.core.api.VocaApi
import com.vocare.translate.core.model.Balance

/**
 * The endpoints only `:app` needs (entitlement and history), on top of the
 * cross-owner [VocaApi]. One Retrofit-backed object implements both.
 */
interface VocaAccountApi : VocaApi {
    suspend fun entitlement(subject: String): Balance
    suspend fun activate(subject: String, receipt: String, storeReachable: Boolean): Balance
    suspend fun sessions(clientId: String): List<RemoteSessionSummary>

    /** Null when the server no longer has the session (404). */
    suspend fun sessionDetail(sessionId: String): RemoteSessionDetail?
}
