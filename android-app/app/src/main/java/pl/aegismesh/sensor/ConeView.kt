package pl.aegismesh.sensor

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.*
import android.view.MotionEvent
import android.view.View
import kotlin.math.*

/**
 * Hot-cold cone. The triangle starts at the phone (bottom) and points at the router (top ping).
 * Far away: wide and red. Close: narrow and green. The width animates smoothly between readings.
 * Tapping the ping at the top toggles fast mode (scan every 2 s).
 */
class ConeView(context: Context) : View(context) {
    var onPingTap: (() -> Unit)? = null
    var fast = false
        set(value) { field = value; invalidate() }
    private var shownAngle = 70.0
    private var shownDistance = 1.0
    private var pulse = 0f
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val edge = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 4f }
    private val ping = Paint(Paint.ANTI_ALIAS_FLAG)
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Palette.GOLD; textSize = 30f; textAlign = Paint.Align.CENTER }
    private val pulser = ValueAnimator.ofFloat(0f, 1f).apply {
        repeatCount = ValueAnimator.INFINITE; duration = 2000
        addUpdateListener { pulse = it.animatedValue as Float; invalidate() }
    }
    private var target = RectF()

    /** Called with the smoothed RSSI; animates the cone to the new width and colour. */
    fun show(rssi: Double) {
        val toAngle = coneHalfAngle(rssi); val toDist = distanceFactor(rssi)
        val fromAngle = shownAngle; val fromDist = shownDistance
        ValueAnimator.ofFloat(0f, 1f).apply {
            duration = 600
            addUpdateListener { val t = it.animatedValue as Float
                shownAngle = fromAngle + (toAngle - fromAngle) * t
                shownDistance = fromDist + (toDist - fromDist) * t
                invalidate() }
        }.start()
    }

    override fun onAttachedToWindow() { super.onAttachedToWindow(); pulser.start() }
    override fun onDetachedFromWindow() { pulser.cancel(); super.onDetachedFromWindow() }

    override fun onDraw(canvas: Canvas) {
        canvas.drawColor(Palette.BLACK)
        val apexX = width / 2f; val apexY = height - 40f
        val tipY = 90f
        val length = apexY - tipY
        val half = (tan(Math.toRadians(shownAngle)) * length).toFloat().coerceAtMost(width / 2f - 8f)
        val color = Palette.heat(shownDistance)
        val path = Path().apply { moveTo(apexX, apexY); lineTo(apexX - half, tipY); lineTo(apexX + half, tipY); close() }
        fill.shader = LinearGradient(apexX, apexY, apexX, tipY, Color.argb(30, Color.red(color), Color.green(color), Color.blue(color)),
            Color.argb(170, Color.red(color), Color.green(color), Color.blue(color)), Shader.TileMode.CLAMP)
        canvas.drawPath(path, fill)
        edge.color = color; canvas.drawPath(path, edge)
        // Router ping at the top of the cone, pulsing at the scan rhythm.
        ping.color = color; ping.style = Paint.Style.FILL; canvas.drawCircle(apexX, tipY, 18f, ping)
        ping.style = Paint.Style.STROKE; ping.strokeWidth = 4f; ping.alpha = ((1 - pulse) * 255).toInt()
        canvas.drawCircle(apexX, tipY, 18f + pulse * 70f, ping); ping.alpha = 255
        target.set(apexX - 90, tipY - 90, apexX + 90, tipY + 90)
        canvas.drawText(if (fast) "PING co 2 s · dotknij, by zwolnić" else "Dotknij pinga: tryb szybki (2 s)", apexX, tipY + 70, text)
        // Phone at the apex.
        ping.style = Paint.Style.FILL; ping.color = Palette.GOLD; canvas.drawCircle(apexX, apexY, 12f, ping)
    }

    override fun onTouchEvent(e: MotionEvent): Boolean {
        if (e.action == MotionEvent.ACTION_DOWN) return true
        if (e.action == MotionEvent.ACTION_UP && target.contains(e.x, e.y)) { onPingTap?.invoke(); performClick() }
        return true
    }
    override fun performClick(): Boolean { super.performClick(); return true }
}
