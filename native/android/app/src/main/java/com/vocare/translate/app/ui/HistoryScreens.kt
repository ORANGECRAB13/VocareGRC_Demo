package com.vocare.translate.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vocare.translate.app.api.SessionDetail
import com.vocare.translate.app.api.SessionSummary
import com.vocare.translate.core.model.Language
import com.vocare.translate.core.model.Turn
import com.vocare.translate.core.theme.VocaTheme

private typealias YC = VocaTheme.Colors

/** History (CONTRACT §5): a live session first, if the server still has one, then past sessions. */
@Composable
fun HistoryScreen(
    rows: List<SessionSummary>,
    loading: Boolean,
    onOpen: (String) -> Unit,
    onBack: () -> Unit,
    banner: @Composable () -> Unit = {},
) {
    Column(Modifier.fillMaxSize().background(YC.Ground)) {
        VocaTopBar("History", onBack = onBack)
        banner()
        when {
            loading && rows.isEmpty() -> EmptyState("Loading…", "Fetching your sessions.")
            rows.isEmpty() -> EmptyState("No sessions yet", "Finished conversations and their transcripts show up here.")
            else -> LazyColumn(Modifier.fillMaxSize().padding(horizontal = 20.dp)) {
                items(rows, key = { it.sessionId }) { row -> HistoryRow(row) { onOpen(row.sessionId) } }
            }
        }
    }
}

@Composable
private fun HistoryRow(row: SessionSummary, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp)
            .clip(VocaTheme.Radii.CardShape)
            .background(YC.Surface)
            .border(1.dp, YC.Hairline, VocaTheme.Radii.CardShape)
            .clickable(onClick = onClick)
            .padding(16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(Language.of(row.lang).flag, fontSize = 22.sp)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(row.topic, style = VocaTheme.Type.BodySemi, color = YC.Ink)
            Spacer(Modifier.height(3.dp))
            Text(
                listOfNotNull(
                    row.callerName.takeIf { it.isNotBlank() },
                    row.durationLabel,
                    row.participantCount?.let { "$it participants" },
                ).joinToString(" · "),
                style = VocaTheme.Type.Body.copy(fontSize = 12.5.sp),
                color = YC.TextMuted,
            )
        }
        if (row.isLive) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(7.dp).clip(CircleShape).background(YC.Error))
                Spacer(Modifier.width(6.dp))
                MicroLabel("Live", color = YC.Error)
            }
        }
    }
}

/** Session detail (CONTRACT §5): the persisted transcript, original above translation. */
@Composable
fun SessionDetailScreen(detail: SessionDetail?, onBack: () -> Unit) {
    Column(Modifier.fillMaxSize().background(YC.Ground)) {
        VocaTopBar(detail?.topic ?: "Session", onBack = onBack)
        when {
            detail == null -> EmptyState("Loading…", "Fetching the transcript.")
            detail.transcript.isEmpty() -> EmptyState("No transcript", "Nothing was captured in this session.")
            else -> LazyColumn(Modifier.fillMaxSize().padding(horizontal = 20.dp)) {
                items(detail.transcript.size) { index -> TranscriptRow(detail.transcript[index]) }
            }
        }
    }
}

@Composable
private fun TranscriptRow(turn: Turn) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp)
            .clip(VocaTheme.Radii.CardShape)
            .background(YC.Surface)
            .border(1.dp, YC.Hairline, VocaTheme.Radii.CardShape)
            .padding(14.dp),
    ) {
        MicroLabel(
            listOfNotNull(turn.speakerName, Language.of(turn.originalLang).label).joinToString(" · "),
            color = YC.InkMute,
        )
        Spacer(Modifier.height(6.dp))
        Text(turn.original, style = VocaTheme.Type.Body, color = YC.Ink)
        turn.translated?.takeIf { it.isNotBlank() }?.let {
            Spacer(Modifier.height(8.dp))
            Box(Modifier.fillMaxWidth().height(1.dp).background(YC.Divider))
            Spacer(Modifier.height(8.dp))
            Text(it, style = VocaTheme.Type.BodyMedium, color = YC.ADeep)
        }
    }
}
