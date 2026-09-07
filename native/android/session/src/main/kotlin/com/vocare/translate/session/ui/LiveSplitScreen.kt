package com.vocare.translate.session.ui

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.StartOffset
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.TextUnitType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vocare.translate.core.model.LiveState
import com.vocare.translate.core.model.Side
import com.vocare.translate.core.model.SideState
import com.vocare.translate.core.model.TurnView
import com.vocare.translate.core.theme.VocaTheme
import com.vocare.translate.session.elapsedClock

private typealias C = VocaTheme.Colors
private typealias F = VocaTheme.Fonts

private val Double.em: TextUnit get() = TextUnit(toFloat(), TextUnitType.Em)

/**
 * The two-sided session screen (CONTRACT §5/§7, JSX `LiveSplitView`).
 *
 * Presentation only: both the cloud and the on-device view models render this,
 * so the two sessions look identical; only the centre-bar badge differs. Person
 * B's half is rotated 180° so they read it from across the table.
 */
@Composable
fun LiveSplitScreen(
    state: LiveState,
    onHold: (Side) -> Unit,
    onRelease: (Side) -> Unit,
    onEnd: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier.fillMaxSize().background(C.Ground)) {
        LiveSidePanel(
            side = Side.B,
            data = state.sideB,
            rotated = true,
            onHold = { onHold(Side.B) },
            onRelease = { onRelease(Side.B) },
            modifier = Modifier.weight(1f).fillMaxWidth(),
        )

        CentreBar(state = state, onEnd = onEnd)

        state.notice?.let { NoticeStrip(it) }

        LiveSidePanel(
            side = Side.A,
            data = state.sideA,
            rotated = false,
            onHold = { onHold(Side.A) },
            onRelease = { onRelease(Side.A) },
            modifier = Modifier.weight(1f).fillMaxWidth(),
        )
    }
}

@Composable
private fun CentreBar(state: LiveState, onEnd: () -> Unit) {
    Column {
        Hairline()
        Row(
            modifier = Modifier.fillMaxWidth().height(56.dp).background(C.Surface).padding(horizontal = 16.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // Language direction: B's code, swap glyph, A's code.
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                Text(state.sideB.langCode.uppercase(), fontSize = 11.sp, fontWeight = FontWeight.Bold, color = C.BLight, fontFamily = F.Body)
                Text("⇄", fontSize = 13.sp, color = C.InkFaint)
                Text(state.sideA.langCode.uppercase(), fontSize = 11.sp, fontWeight = FontWeight.Bold, color = C.ALight, fontFamily = F.Body)
            }

            Row(
                modifier = Modifier.weight(1f),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                val badge = state.badge
                if (badge != null) {
                    Text(
                        text = badge.uppercase(),
                        fontFamily = F.Mono,
                        fontSize = 9.sp,
                        letterSpacing = 0.12.em,
                        fontWeight = FontWeight.Bold,
                        color = C.BDeep,
                        modifier = Modifier.clip(RoundedCornerShape(99.dp)).background(C.PanelBActive).padding(horizontal = 8.dp, vertical = 4.dp),
                    )
                } else {
                    LiveDot(color = C.B)
                }
                Spacer(Modifier.width(8.dp))
                Text(
                    text = state.elapsedClock,
                    fontSize = 12.5.sp,
                    fontWeight = FontWeight.Medium,
                    color = C.Ink.copy(alpha = .75f),
                    fontFamily = F.Body,
                )
            }

            Row(
                modifier = Modifier
                    .height(34.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(C.B.copy(alpha = .15f))
                    .pointerInput(Unit) { detectTapGestures { onEnd() } }
                    .padding(horizontal = 14.dp)
                    .semantics { contentDescription = "End session" },
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(7.dp),
            ) {
                Box(Modifier.size(9.dp).clip(RoundedCornerShape(2.dp)).background(C.BLight))
                Text("End", fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold, color = C.BLight, fontFamily = F.Body)
            }
        }
        Hairline()
    }
}

@Composable
private fun Hairline() {
    Box(Modifier.fillMaxWidth().height(1.dp).background(C.Hairline))
}

@Composable
private fun LiveDot(color: Color) {
    val transition = rememberInfiniteTransition(label = "live-dot")
    val alpha by transition.animateFloat(
        initialValue = 1f,
        targetValue = 0.25f,
        animationSpec = infiniteRepeatable(tween(800, easing = LinearEasing), RepeatMode.Reverse),
        label = "dot-blink",
    )
    Box(Modifier.size(6.dp).alpha(alpha).clip(CircleShape).background(color))
}

@Composable
private fun NoticeStrip(text: String) {
    Text(
        text = text,
        fontSize = 12.sp,
        lineHeight = 17.sp,
        color = C.Error,
        fontFamily = F.Body,
        modifier = Modifier.fillMaxWidth().background(C.ErrorLight).padding(horizontal = 18.dp, vertical = 9.dp),
    )
}

/** One half of the screen: name tag + status, the turn list, and the 104dp mic (JSX `LiveSidePanel`). */
@Composable
internal fun LiveSidePanel(
    side: Side,
    data: SideState,
    rotated: Boolean,
    onHold: () -> Unit,
    onRelease: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val isA = side == Side.A
    val accent = if (isA) C.A else C.B
    val accentDeep = if (isA) C.ADeep else C.BDeep
    val idleBg = if (isA) C.PanelAIdle else C.PanelBIdle
    val activeBg = if (isA) C.PanelAActive else C.PanelBActive
    val background by animateColorAsState(if (data.pressing) activeBg else idleBg, label = "panel-bg")

    Column(
        modifier = modifier
            .graphicsLayer { if (rotated) rotationZ = 180f }
            .background(background),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(start = 18.dp, end = 18.dp, top = 14.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            PersonNameTag(
                code = data.langCode,
                label = data.langLabel,
                name = data.name,
                tileBackground = if (isA) C.TileA else C.TileB,
                tileInk = if (isA) accent else accentDeep,
            )
            Spacer(Modifier.weight(1f))
            Text(
                text = data.status.uppercase(),
                fontSize = 10.5.sp,
                letterSpacing = 0.1.em,
                fontWeight = FontWeight.Bold,
                fontFamily = F.Mono,
                color = if (data.pressing) accentDeep else C.Ink.copy(alpha = .4f),
                textAlign = TextAlign.End,
            )
        }

        // Newest turn sits nearest this person's microphone.
        LazyColumn(
            modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 18.dp, vertical = 4.dp),
            reverseLayout = true,
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            data.note?.let { note ->
                item(key = "note") {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TypingDots(color = C.Ink.copy(alpha = .4f))
                        Text(note, fontSize = 13.sp, color = C.InkMute, fontFamily = F.Body)
                    }
                }
            }
            items(data.turns.asReversed(), key = { it.key }) { turn ->
                LiveTurn(turn = turn, side = side)
            }
        }

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(150.dp)
                .then(if (rotated) Modifier else Modifier.navigationBarsPadding()),
            contentAlignment = Alignment.Center,
        ) {
            MicButton(
                pressing = data.pressing,
                disabled = data.disabled,
                dimmed = data.dimmed,
                accent = accent,
                accentDeep = accentDeep,
                idleBackground = if (isA) C.InkA14 else C.InkB14,
                idleBorder = if (isA) C.InkA35 else C.InkB35,
                idleInk = if (isA) accent else accentDeep,
                label = "${data.name} hold to speak",
                onHold = onHold,
                onRelease = onRelease,
            )
        }
    }
}

@Composable
private fun MicButton(
    pressing: Boolean,
    disabled: Boolean,
    dimmed: Boolean,
    accent: Color,
    accentDeep: Color,
    idleBackground: Color,
    idleBorder: Color,
    idleInk: Color,
    label: String,
    onHold: () -> Unit,
    onRelease: () -> Unit,
) {
    val currentDisabled by rememberUpdatedState(disabled)
    val currentHold by rememberUpdatedState(onHold)
    val currentRelease by rememberUpdatedState(onRelease)
    Box(Modifier.size(VocaTheme.Radii.MicDiameter), contentAlignment = Alignment.Center) {
        if (pressing) PulseRing(color = accent, size = VocaTheme.Radii.MicDiameter)
        Box(
            modifier = Modifier
                .size(VocaTheme.Radii.MicDiameter)
                .scale(if (pressing) 1.12f else 1f)
                .alpha(if (dimmed) 0.58f else 1f)
                .clip(CircleShape)
                .background(if (pressing) accent else idleBackground)
                .border(2.dp, if (pressing) accentDeep else idleBorder, CircleShape)
                .semantics { contentDescription = label }
                .pointerInput(Unit) {
                    // Press = hold, lift or cancel = release (JSX pointerdown/up/cancel).
                    detectTapGestures(
                        onPress = {
                            if (currentDisabled) return@detectTapGestures
                            currentHold()
                            try {
                                tryAwaitRelease()
                            } finally {
                                currentRelease()
                            }
                        },
                    )
                },
            contentAlignment = Alignment.Center,
        ) {
            MicGlyph(color = if (pressing) Color.White else idleInk)
        }
    }
}

/** Three expanding rings behind the pressed mic (JSX `PulseRing`, 1.8s, staggered 0.6s). */
@Composable
internal fun PulseRing(color: Color, size: Dp) {
    val transition = rememberInfiniteTransition(label = "pulse-ring")
    repeat(3) { i ->
        val progress by transition.animateFloat(
            initialValue = 0f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(
                tween(durationMillis = 1800, easing = LinearEasing),
                RepeatMode.Restart,
                initialStartOffset = StartOffset(i * 600),
            ),
            label = "ring-$i",
        )
        Box(
            Modifier
                .size(size)
                .scale(1f + progress * 0.6f)
                .alpha((1f - progress) * 0.7f)
                .border(2.dp, color, CircleShape),
        )
    }
}

/** Language tile + name + language label (JSX `PersonNameTag`). */
@Composable
internal fun PersonNameTag(code: String, label: String, name: String, tileBackground: Color, tileInk: Color) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Box(
            Modifier.size(30.dp).clip(RoundedCornerShape(10.dp)).background(tileBackground),
            contentAlignment = Alignment.Center,
        ) {
            Text(code.uppercase(), fontSize = 11.sp, fontWeight = FontWeight.Bold, color = tileInk, fontFamily = F.Mono)
        }
        Column {
            Text(name, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = C.Ink, fontFamily = F.Body)
            Text(label, fontSize = 11.sp, color = C.InkMute, fontFamily = F.Body)
        }
    }
}

/**
 * One bubble (JSX `LiveTurn`). `mine` is the reader's own words ("You said",
 * original text on a white bubble); otherwise the translation they should read,
 * on their side's accent, with the source in italics underneath.
 */
@Composable
internal fun LiveTurn(turn: TurnView, side: Side) {
    val isA = side == Side.A
    val mine = turn.mine
    val tagColor = if (mine) C.Ink.copy(alpha = .4f) else if (isA) C.ADeep else C.BDeep
    val bubble = if (mine) C.Surface else if (isA) C.A else C.B
    val fg = if (mine) C.Ink else Color.White
    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = if (mine) Alignment.End else Alignment.Start,
    ) {
        Column(Modifier.fillMaxWidth(0.88f), horizontalAlignment = if (mine) Alignment.End else Alignment.Start) {
            Text(
                text = if (mine) "YOU SAID" else "TRANSLATED",
                fontSize = 9.5.sp,
                letterSpacing = 0.12.em,
                fontWeight = FontWeight.Bold,
                color = tagColor,
                fontFamily = F.Mono,
                modifier = Modifier.padding(bottom = 4.dp),
            )
            Text(
                text = if (mine) turn.turn.original else turn.turn.translated.orEmpty(),
                fontSize = 15.5.sp,
                lineHeight = 21.7.sp,
                color = fg,
                fontFamily = F.Body,
                modifier = Modifier
                    .clip(RoundedCornerShape(16.dp))
                    .background(bubble)
                    .padding(horizontal = 13.dp, vertical = 11.dp),
            )
            if (!mine) {
                Text(
                    text = turn.turn.original,
                    fontSize = 11.5.sp,
                    fontStyle = FontStyle.Italic,
                    color = C.Ink.copy(alpha = .4f),
                    fontFamily = F.Body,
                    modifier = Modifier.padding(top = 5.dp),
                )
            }
        }
    }
}

@Composable
private fun TypingDots(color: Color) {
    val transition = rememberInfiniteTransition(label = "typing")
    Row(horizontalArrangement = Arrangement.spacedBy(3.dp), verticalAlignment = Alignment.CenterVertically) {
        repeat(3) { i ->
            val alpha by transition.animateFloat(
                initialValue = 0.25f,
                targetValue = 1f,
                animationSpec = infiniteRepeatable(
                    tween(600, easing = LinearEasing),
                    RepeatMode.Reverse,
                    initialStartOffset = StartOffset(i * 200),
                ),
                label = "dot-$i",
            )
            Box(Modifier.size(5.dp).alpha(alpha).clip(CircleShape).background(color))
        }
    }
}

/** A simple microphone glyph drawn from shapes so the module carries no icon assets. */
@Composable
private fun MicGlyph(color: Color) {
    val cup = RoundedCornerShape(bottomStart = 10.dp, bottomEnd = 10.dp)
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(Modifier.width(12.dp).height(20.dp).clip(RoundedCornerShape(6.dp)).background(color))
        Spacer(Modifier.height(2.dp))
        Box(Modifier.width(20.dp).height(8.dp).clip(cup).border(2.dp, color, cup))
        Box(Modifier.width(2.dp).height(4.dp).background(color))
        Box(Modifier.width(10.dp).height(2.dp).background(color))
    }
}
