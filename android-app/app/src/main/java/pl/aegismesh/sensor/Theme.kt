package pl.aegismesh.sensor

import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.widget.Button
import android.widget.TextView

/** Black-and-gold palette shared by all screens. */
object Palette {
    val BLACK = Color.rgb(10, 10, 10)
    val PANEL = Color.rgb(24, 22, 18)
    val GOLD = Color.rgb(212, 175, 55)
    val GOLD_DIM = Color.argb(90, 212, 175, 55)
    val TEXT = Color.rgb(240, 230, 200)
    val MUTED = Color.rgb(160, 150, 120)
    val RED = Color.rgb(229, 57, 53)
    val ORANGE = Color.rgb(255, 143, 0)
    val GREEN = Color.rgb(67, 214, 110)

    fun status(s: Status) = when (s) {
        Status.SUSPICIOUS -> RED
        Status.HOTSPOT -> ORANGE
        Status.NEW -> GOLD
        Status.KNOWN -> GREEN
    }

    /** Red (far) → gold (middle) → green (close) for a distance factor 0..1 (0 = close). */
    fun heat(distance: Double): Int {
        val d = distance.coerceIn(0.0, 1.0)
        fun mix(a: Int, b: Int, t: Double) = Color.rgb(
            (Color.red(a) + (Color.red(b) - Color.red(a)) * t).toInt(),
            (Color.green(a) + (Color.green(b) - Color.green(a)) * t).toInt(),
            (Color.blue(a) + (Color.blue(b) - Color.blue(a)) * t).toInt())
        return if (d < 0.5) mix(GREEN, GOLD, d * 2) else mix(GOLD, RED, (d - 0.5) * 2)
    }
}

fun Button.gold(outline: Boolean = false) {
    isAllCaps = false
    setTextColor(if (outline) Palette.GOLD else Palette.BLACK)
    background = GradientDrawable().apply {
        cornerRadius = 18f
        setColor(if (outline) Palette.PANEL else Palette.GOLD)
        setStroke(3, Palette.GOLD)
    }
    backgroundTintList = null
}

fun TextView.ink(color: Int = Palette.TEXT) { setTextColor(color) }

/** Minimal ColorStateList helper for progress bars and inputs. */
fun tint(color: Int): ColorStateList = ColorStateList.valueOf(color)
