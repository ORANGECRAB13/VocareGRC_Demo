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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vocare.translate.core.model.Language
import com.vocare.translate.core.theme.VocaTheme

private typealias LC = VocaTheme.Colors

/**
 * Language picker (CONTRACT §5): search over `Language.ALL` — which is VOCA
 * `main`'s table, so Cantonese is absent — with the flag beside each name.
 */
@Composable
fun LanguagePickerScreen(
    title: String,
    selected: String,
    onPick: (String) -> Unit,
    onBack: () -> Unit,
) {
    var query by remember { mutableStateOf("") }
    val matches = remember(query) {
        val q = query.trim().lowercase()
        if (q.isEmpty()) Language.ALL else Language.ALL.filter {
            it.label.lowercase().contains(q) || it.code.contains(q)
        }
    }

    Column(Modifier.fillMaxSize().background(LC.Ground)) {
        VocaTopBar(title, onBack = onBack)
        Box(
            modifier = Modifier
                .padding(horizontal = 20.dp)
                .fillMaxWidth()
                .height(46.dp)
                .clip(VocaTheme.Radii.ButtonShape)
                .background(LC.Surface)
                .border(1.dp, LC.Border, VocaTheme.Radii.ButtonShape)
                .padding(horizontal = 14.dp),
            contentAlignment = Alignment.CenterStart,
        ) {
            if (query.isEmpty()) Text("Search languages", style = VocaTheme.Type.Body, color = LC.TextFaint)
            BasicTextField(
                value = query,
                onValueChange = { query = it },
                singleLine = true,
                cursorBrush = SolidColor(LC.A),
                textStyle = VocaTheme.Type.Body.copy(color = LC.Ink),
                modifier = Modifier.fillMaxWidth(),
            )
        }
        Spacer(Modifier.height(8.dp))
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 20.dp)) {
            items(matches, key = { it.code }) { language ->
                LanguageRow(language, language.code == selected) { onPick(language.code) }
            }
        }
    }
}

@Composable
private fun LanguageRow(language: Language, selected: Boolean, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp)
            .clip(VocaTheme.Radii.CardShape)
            .background(if (selected) LC.Surface2 else LC.Surface)
            .border(1.dp, if (selected) LC.InkA35 else LC.Hairline, VocaTheme.Radii.CardShape)
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(language.flag, fontSize = 22.sp)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(language.label, style = VocaTheme.Type.BodySemi, color = LC.Ink)
            MicroLabel(language.code, color = LC.TextFaint)
        }
        if (selected) {
            Box(
                Modifier.height(20.dp).width(20.dp).clip(CircleShape).background(LC.A),
                contentAlignment = Alignment.Center,
            ) { Text("✓", color = androidx.compose.ui.graphics.Color.White, fontSize = 12.sp) }
        }
    }
}
