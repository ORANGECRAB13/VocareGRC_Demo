package com.vocare.translate.core.history

import com.vocare.translate.core.model.SessionRecord

/**
 * Upsert by `sessionId`. `:session` calls this on every transcript change and
 * on teardown; `:app` implements it over Room and adds list/get on its own
 * repository type.
 */
interface SessionHistoryStore {
    suspend fun save(record: SessionRecord)
}
