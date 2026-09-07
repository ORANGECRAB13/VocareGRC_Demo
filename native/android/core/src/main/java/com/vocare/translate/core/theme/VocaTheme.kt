package com.vocare.translate.core.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vocare.translate.core.R

/**
 * Design tokens from CONTRACT §7, copied exactly. `VocaTheme.Colors.A` is the
 * Person A / brand violet, `VocaTheme.Colors.B` the Person B green.
 */
object VocaTheme {
    object Colors {
        val Ink = Color(0xFF1C2033)
        val Ground = Color(0xFFFBF9F7)
        val Surface = Color(0xFFFFFFFF)
        val Surface2 = Color(0xFFF1EEFC)

        val A = Color(0xFF6C5CE7)
        val ADeep = Color(0xFF5546C9)
        val ALight = Color(0xFF8B7CF0)
        val B = Color(0xFF34A46F)
        val BDeep = Color(0xFF2A8B5D)
        val BLight = Color(0xFF5FBF8E)
        val Accent = Color(0xFFE0397F)
        val Error = Color(0xFFD93A5C)
        val ErrorLight = Color(0xFFFBE4E9)

        val PanelAIdle = Color(0xFFF7F5FF)
        val PanelAActive = Color(0xFFEBE7FD)
        val PanelBIdle = Color(0xFFF4FAF6)
        val PanelBActive = Color(0xFFE2F3E9)

        // Secondary ink and hairlines lifted from the canvas.
        val InkMute = Ink.copy(alpha = 0.45f)
        val InkFaint = Ink.copy(alpha = 0.35f)
        val InkSoft = Ink.copy(alpha = 0.6f)
        val Hairline = Ink.copy(alpha = 0.08f)
        val Divider = Ink.copy(alpha = 0.06f)
        val TextMuted = Color(0xFF6E7285)
        val TextFaint = Color(0xFF9EA1AF)
        val Border = Color(0xFFE6E4EC)

        // Alpha washes.
        val InkA07 = A.copy(alpha = 0.07f)
        val InkA08 = A.copy(alpha = 0.08f)
        val InkA14 = A.copy(alpha = 0.14f)
        val InkA35 = A.copy(alpha = 0.35f)
        val InkB09 = B.copy(alpha = 0.09f)
        val InkB14 = B.copy(alpha = 0.14f)
        val InkB35 = B.copy(alpha = 0.35f)
        val TileA = ALight.copy(alpha = 0.18f)
        val TileB = B.copy(alpha = 0.16f)
        val SwitchOff = Ink.copy(alpha = 0.16f)
    }

    object Fonts {
        /** Outfit — display, 700, tracking -0.02em. */
        val Display: FontFamily = FontFamily(
            Font(R.font.outfit_medium, FontWeight.Medium),
            Font(R.font.outfit_bold, FontWeight.Bold),
        )

        /** Space Grotesk — body. */
        val Body: FontFamily = FontFamily(
            Font(R.font.space_grotesk_regular, FontWeight.Normal),
            Font(R.font.space_grotesk_medium, FontWeight.Medium),
            Font(R.font.space_grotesk_semibold, FontWeight.SemiBold),
            Font(R.font.space_grotesk_bold, FontWeight.Bold),
        )

        /** JetBrains Mono — uppercase micro-labels, tracking .12–.16em. */
        val Mono: FontFamily = FontFamily(
            Font(R.font.jetbrains_mono_medium, FontWeight.Medium),
            Font(R.font.jetbrains_mono_bold, FontWeight.Bold),
        )
    }

    object Type {
        val Display = TextStyle(fontFamily = Fonts.Display, fontWeight = FontWeight.Bold, letterSpacing = (-0.02).em)
        val Body = TextStyle(fontFamily = Fonts.Body, fontWeight = FontWeight.Normal, fontSize = 14.sp)
        val BodyMedium = Body.copy(fontWeight = FontWeight.Medium)
        val BodySemi = Body.copy(fontWeight = FontWeight.SemiBold)

        /** Uppercase micro-label. Callers uppercase the text themselves. */
        val Micro = TextStyle(fontFamily = Fonts.Mono, fontWeight = FontWeight.Bold, fontSize = 10.sp, letterSpacing = 0.14.em)
        val MicroTight = Micro.copy(letterSpacing = 0.12.em)
        val MicroWide = Micro.copy(letterSpacing = 0.16.em)
    }

    object Radii {
        val Card = 18.dp
        val CardLarge = 22.dp
        val Button = 20.dp
        val Tile = 12.dp
        val MicDiameter = 104.dp
        val CardShape = RoundedCornerShape(Card)
        val CardLargeShape = RoundedCornerShape(CardLarge)
        val ButtonShape = RoundedCornerShape(Button)
    }
}

private val Double.em get() = androidx.compose.ui.unit.TextUnit(this.toFloat(), androidx.compose.ui.unit.TextUnitType.Em)

private val VocaColorScheme = lightColorScheme(
    primary = VocaTheme.Colors.A,
    onPrimary = Color.White,
    secondary = VocaTheme.Colors.B,
    onSecondary = Color.White,
    tertiary = VocaTheme.Colors.Accent,
    background = VocaTheme.Colors.Ground,
    onBackground = VocaTheme.Colors.Ink,
    surface = VocaTheme.Colors.Surface,
    onSurface = VocaTheme.Colors.Ink,
    surfaceVariant = VocaTheme.Colors.Surface2,
    onSurfaceVariant = VocaTheme.Colors.TextMuted,
    error = VocaTheme.Colors.Error,
    onError = Color.White,
    outline = VocaTheme.Colors.Border,
)

private val VocaTypography = Typography(
    displayLarge = VocaTheme.Type.Display.copy(fontSize = 34.sp, lineHeight = 36.sp),
    displayMedium = VocaTheme.Type.Display.copy(fontSize = 28.sp, lineHeight = 31.sp),
    displaySmall = VocaTheme.Type.Display.copy(fontSize = 24.sp, lineHeight = 27.sp),
    headlineMedium = VocaTheme.Type.Display.copy(fontSize = 20.sp),
    titleMedium = VocaTheme.Type.BodySemi.copy(fontSize = 16.sp),
    titleSmall = VocaTheme.Type.BodySemi.copy(fontSize = 14.5.sp),
    bodyLarge = VocaTheme.Type.Body.copy(fontSize = 15.5.sp, lineHeight = 22.sp),
    bodyMedium = VocaTheme.Type.Body.copy(fontSize = 14.sp, lineHeight = 21.sp),
    bodySmall = VocaTheme.Type.Body.copy(fontSize = 12.sp, lineHeight = 17.sp),
    labelLarge = VocaTheme.Type.BodySemi.copy(fontSize = 15.sp),
    labelMedium = VocaTheme.Type.Micro,
    labelSmall = VocaTheme.Type.Micro.copy(fontSize = 9.5.sp),
)

/** Light only — the Voca design commits to one look. */
@Composable
fun VocaTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = VocaColorScheme,
        typography = VocaTypography,
        content = content,
    )
}
