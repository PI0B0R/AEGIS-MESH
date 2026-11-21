package pl.aegismesh.sensor

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.*
import android.view.MotionEvent
import android.view.View
import android.view.animation.LinearInterpolator
import kotlin.math.*

/**
 * Overview radar: every nearby router is a ping. Closer to the centre = stronger signal.
 * The angle only spreads pings apart (Wi-Fi gives no direction). Tap a ping to start hot-cold.
 */
class RadarView(context: Context) : View(context) {
    class Ping(val ap: SeenAp, val status: Status)
    var pings: List<Ping> = emptyList()
        set(value) { field = value; invalidate() }
    var onPing: ((Ping) -> Unit)? = null

    private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 2f; color = Palette.GOLD_DIM }
    private val dot = Paint(Paint.ANTI_ALIAS_FLAG)
    private val label = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Palette.TEXT; textSize = 26f }
    private val sweepPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var sweep = 0f
    private val spots = mutableListOf<Pair<PointF, Ping>>()
    private val animator = ValueAnimator.ofFloat(0f, 360f).apply {
        duration = 3000; repeatCount = ValueAnimator.INFINITE; interpolator = LinearInterpolator()
        addUpdateListener { sweep = it.animatedValue as Float; invalidate() }
    }

    override fun onAttachedToWindow() { super.onAttachedToWindow(); animator.start() }
    override fun onDetachedFromWindow() { animator.cancel(); super.onDetachedFromWindow() }

    override fun onDraw(canvas: Canvas) {
        canvas.drawColor(Palette.BLACK)
        val cx = width / 2f; val cy = height / 2f; val r = min(cx, cy) - 12f
        for (i in 1..4) canvas.drawCircle(cx, cy, r * i / 4, ring)
        canvas.drawLine(cx - r, cy, cx + r, cy, ring); canvas.drawLine(cx, cy - r, cx, cy + r, ring)
        sweepPaint.shader = SweepGradient(cx, cy, intArrayOf(Color.TRANSPARENT, Color.TRANSPARENT, Palette.GOLD_DIM, Palette.GOLD),
            floatArrayOf(0f, 0.75f, 0.97f, 1f)).also { s -> s.setLocalMatrix(Matrix().apply { setRotate(sweep, cx, cy) }) }
        canvas.drawCircle(cx, cy, r, sweepPaint)
        spots.clear()
        for (p in pings) {
            // Stable angle per BSSID; radius from signal strength.
            val angle = Math.toRadians(((p.ap.bssid.hashCode().toLong() and 0xffff) % 360).toDouble())
            val dist = (0.12 + 0.85 * distanceFactor(p.ap.rssi.toDouble())) * r
            val x = cx + (dist * cos(angle)).toFloat(); val y = cy + (dist * sin(angle)).toFloat()
            val deg = ((Math.toDegrees(angle) + 360) % 360).toFloat()
            val behind = ((sweep - deg) + 360) % 360       // fades after the sweep passes
            val glow = (1f - behind / 360f).coerceIn(0.35f, 1f)
            dot.color = Palette.status(p.status); dot.alpha = (255 * glow).toInt()
            canvas.drawCircle(x, y, if (p.status == Status.SUSPICIOUS) 14f else 10f, dot)
            dot.alpha = (70 * glow).toInt(); canvas.drawCircle(x, y, 26f, dot)
            if (p.status.rank <= Status.HOTSPOT.rank)
                canvas.drawText(p.ap.ssid.ifEmpty { "(ukryta)" }.take(14), x + 16, y - 12, label)
            spots.add(PointF(x, y) to p)
        }
        dot.color = Palette.GOLD; dot.alpha = 255; canvas.drawCircle(cx, cy, 7f, dot)
    }

    override fun onTouchEvent(e: MotionEvent): Boolean {
        if (e.action == MotionEvent.ACTION_DOWN) return true
        if (e.action == MotionEvent.ACTION_UP) {
            val hit = spots.minByOrNull { hypot(it.first.x - e.x, it.first.y - e.y) }
            if (hit != null && hypot(hit.first.x - e.x, hit.first.y - e.y) < 60) { onPing?.invoke(hit.second); performClick() }
        }
        return true
    }
    override fun performClick(): Boolean { super.performClick(); return true }
}
