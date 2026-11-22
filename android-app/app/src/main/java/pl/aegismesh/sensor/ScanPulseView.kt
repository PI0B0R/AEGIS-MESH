package pl.aegismesh.sensor

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.*
import android.view.View
import android.view.animation.LinearInterpolator
import kotlin.math.min

/**
 * The big scan icon on the Pulpit: a shield inside a progress ring.
 * While a scan runs, rings pulse outwards, a beam sweeps round and the ring fills with the percentage.
 */
internal class ScanPulseView(context: Context) : View(context) {
    enum class Mode { NONE, RUNNING, QUEUED, DONE, PAUSED, FAILED, OFFLINE }

    var mode = Mode.NONE
        set(value) { field = value; spin(value == Mode.RUNNING || value == Mode.QUEUED); invalidate() }
    /** 0..100, drawn only while running. */
    var progress = 0f
        set(value) { field = value.coerceIn(0f, 100f); invalidate() }

    private val dp = resources.displayMetrics.density
    private val track = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 9 * dp; color = Palette.GOLD_DIM }
    private val arc = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 9 * dp; strokeCap = Paint.Cap.ROUND }
    private val pulse = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 3 * dp }
    private val beam = Paint(Paint.ANTI_ALIAS_FLAG)
    private val shieldFill = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Palette.PANEL }
    private val shieldEdge = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 4 * dp; strokeJoin = Paint.Join.ROUND }
    private val symbol = Paint(Paint.ANTI_ALIAS_FLAG).apply { textAlign = Paint.Align.CENTER; typeface = Typeface.DEFAULT_BOLD }
    private val shield = Path()
    private val box = RectF()
    private var phase = 0f
    private val animator = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = 2200; repeatCount = ValueAnimator.INFINITE; interpolator = LinearInterpolator()
        addUpdateListener { phase = it.animatedValue as Float; invalidate() }
    }

    private fun spin(on: Boolean) {
        if (on && isAttachedToWindow && !animator.isStarted) animator.start()
        if (!on) animator.cancel()
    }

    override fun onAttachedToWindow() { super.onAttachedToWindow(); spin(mode == Mode.RUNNING || mode == Mode.QUEUED) }
    override fun onDetachedFromWindow() { animator.cancel(); super.onDetachedFromWindow() }

    override fun onMeasure(widthSpec: Int, heightSpec: Int) {
        val size = (190 * dp).toInt()
        setMeasuredDimension(resolveSize(size, widthSpec), resolveSize(size, heightSpec))
    }

    private fun color() = when (mode) {
        Mode.RUNNING, Mode.QUEUED, Mode.NONE -> Palette.GOLD
        Mode.DONE -> Palette.GREEN
        Mode.PAUSED -> Palette.ORANGE
        Mode.FAILED, Mode.OFFLINE -> Palette.RED
    }

    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f; val cy = height / 2f
        val r = min(cx, cy) - 8 * dp
        val ring = r * 0.78f
        val c = color()
        val busy = mode == Mode.RUNNING || mode == Mode.QUEUED

        if (busy) {
            // three rings that grow and fade, one after another
            for (i in 0 until 3) {
                val t = (phase + i / 3f) % 1f
                pulse.color = c; pulse.alpha = ((1 - t) * 170).toInt()
                canvas.drawCircle(cx, cy, ring + (r - ring) * t + 2 * dp, pulse)
            }
            // the sweeping beam
            beam.shader = SweepGradient(cx, cy, intArrayOf(Color.TRANSPARENT, Color.TRANSPARENT, Color.argb(110, Color.red(c), Color.green(c), Color.blue(c))),
                floatArrayOf(0f, 0.8f, 1f))
            canvas.save(); canvas.rotate(phase * 360f, cx, cy)
            canvas.drawCircle(cx, cy, ring - 6 * dp, beam)
            canvas.restore()
        }

        box.set(cx - ring, cy - ring, cx + ring, cy + ring)
        canvas.drawCircle(cx, cy, ring, track)
        arc.color = c
        when (mode) {
            Mode.RUNNING -> canvas.drawArc(box, -90f, 3.6f * progress.coerceAtLeast(1f), false, arc)
            Mode.QUEUED -> canvas.drawArc(box, phase * 360f - 90f, 50f, false, arc)
            Mode.NONE -> {}
            else -> canvas.drawCircle(cx, cy, ring, arc)
        }

        // the shield
        val s = ring * 0.62f
        shield.reset()
        shield.moveTo(cx, cy - s)
        shield.lineTo(cx + s * 0.82f, cy - s * 0.62f)
        shield.cubicTo(cx + s * 0.82f, cy + s * 0.25f, cx + s * 0.45f, cy + s * 0.75f, cx, cy + s)
        shield.cubicTo(cx - s * 0.45f, cy + s * 0.75f, cx - s * 0.82f, cy + s * 0.25f, cx - s * 0.82f, cy - s * 0.62f)
        shield.close()
        canvas.drawPath(shield, shieldFill)
        shieldEdge.color = c
        canvas.drawPath(shield, shieldEdge)

        val (text, size) = when (mode) {
            Mode.RUNNING -> "${progress.toInt()}%" to s * 0.5f
            Mode.QUEUED -> "…" to s * 0.7f
            Mode.DONE -> "✓" to s * 0.85f
            Mode.PAUSED -> "II" to s * 0.6f
            Mode.FAILED, Mode.OFFLINE -> "!" to s * 0.85f
            Mode.NONE -> "◆" to s * 0.7f
        }
        symbol.color = c; symbol.textSize = size
        canvas.drawText(text, cx, cy - (symbol.descent() + symbol.ascent()) / 2, symbol)
    }
}
