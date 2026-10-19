package com.robocode.lukulive;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RadialGradient;
import android.graphics.RectF;
import android.graphics.Shader;
import android.view.View;
import android.view.animation.LinearInterpolator;

/** Animated voice orb: reacts to microphone level, thinking and speaking states. */
final class OrbView extends View {
    static final int IDLE = 0, LISTENING = 1, THINKING = 2, SPEAKING = 3;

    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint ring = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint icon = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF rect = new RectF();
    private final ValueAnimator anim;

    private int state = IDLE;
    private float level, targetLevel, phase;
    private int color = 0xFF5B6B8C;
    private float density;

    OrbView(Context c) {
        super(c);
        density = c.getResources().getDisplayMetrics().density;
        ring.setStyle(Paint.Style.STROKE);
        icon.setColor(0xFFFFFFFF);
        icon.setStrokeCap(Paint.Cap.ROUND);
        anim = ValueAnimator.ofFloat(0f, 1f);
        anim.setDuration(2600);
        anim.setRepeatCount(ValueAnimator.INFINITE);
        anim.setInterpolator(new LinearInterpolator());
        anim.addUpdateListener(new ValueAnimator.AnimatorUpdateListener() {
            @Override public void onAnimationUpdate(ValueAnimator a) {
                phase = (Float) a.getAnimatedValue();
                float t = targetLevel;
                if (state == SPEAKING) t = 0.35f + 0.3f * (float) Math.abs(Math.sin(phase * Math.PI * 9));
                else if (state == THINKING) t = 0.25f;
                else if (state == IDLE) t = 0f;
                level += (t - level) * 0.18f;
                invalidate();
            }
        });
    }

    void setState(int s) {
        state = s;
        switch (s) {
            case LISTENING: color = 0xFF2EC4B6; break;
            case THINKING: color = 0xFFA855F7; break;
            case SPEAKING: color = 0xFF3B82F6; break;
            default: color = 0xFF5B6B8C; targetLevel = 0; break;
        }
        invalidate();
    }

    /** Microphone RMS in dB as reported by SpeechRecognizer (-2..10 typical). */
    void setRms(float db) {
        float v = (db + 2f) / 12f;
        targetLevel = Math.max(0f, Math.min(1f, v));
    }

    @Override protected void onAttachedToWindow() {
        super.onAttachedToWindow();
        anim.start();
    }

    @Override protected void onDetachedFromWindow() {
        anim.cancel();
        super.onDetachedFromWindow();
    }

    @Override protected void onDraw(Canvas canvas) {
        float w = getWidth(), h = getHeight();
        float cx = w / 2f, cy = h / 2f;
        float r = Math.min(w, h) * 0.30f;
        double tp = phase * Math.PI * 2;

        // halos
        for (int i = 3; i >= 1; i--) {
            float breath = (float) (0.04 * Math.sin(tp + i));
            float rr = r * (1f + 0.16f * i * (0.35f + level) + breath);
            fill.setShader(null);
            fill.setColor(withAlpha(color, (int) (26 + 14 * (3 - i) + 40 * level)));
            canvas.drawCircle(cx, cy, rr, fill);
        }

        // thinking arc
        if (state == THINKING) {
            ring.setStrokeWidth(3.5f * density);
            ring.setColor(withAlpha(0xFFFFFFFF, 200));
            float rr = r * 1.12f;
            rect.set(cx - rr, cy - rr, cx + rr, cy + rr);
            canvas.drawArc(rect, phase * 720f, 90f, false, ring);
        }

        // core
        fill.setShader(new RadialGradient(cx - r * 0.3f, cy - r * 0.35f, r * 1.4f,
                lighten(color), color, Shader.TileMode.CLAMP));
        canvas.drawCircle(cx, cy, r * (1f + 0.06f * level), fill);
        fill.setShader(null);

        drawIcon(canvas, cx, cy, r);
    }

    private void drawIcon(Canvas c, float cx, float cy, float r) {
        float s = r * 0.42f;
        icon.setStyle(Paint.Style.FILL);
        if (state == SPEAKING) {
            // equalizer bars
            float bw = s * 0.22f;
            for (int i = -2; i <= 2; i++) {
                float amp = (float) (0.35 + 0.65 * Math.abs(Math.sin(phase * Math.PI * 6 + i * 1.1)));
                float bh = s * (1.4f - Math.abs(i) * 0.3f) * amp;
                float x = cx + i * bw * 1.7f;
                rect.set(x - bw / 2, cy - bh / 2, x + bw / 2, cy + bh / 2);
                c.drawRoundRect(rect, bw / 2, bw / 2, icon);
            }
            return;
        }
        if (state == THINKING) {
            for (int i = -1; i <= 1; i++) {
                float a = (float) (0.4 + 0.6 * Math.abs(Math.sin(phase * Math.PI * 4 + i)));
                icon.setAlpha((int) (255 * a));
                c.drawCircle(cx + i * s * 0.55f, cy, s * 0.14f, icon);
            }
            icon.setAlpha(255);
            return;
        }
        // microphone
        float mw = s * 0.46f, mh = s * 0.9f;
        rect.set(cx - mw / 2, cy - mh * 0.75f, cx + mw / 2, cy + mh * 0.25f);
        c.drawRoundRect(rect, mw / 2, mw / 2, icon);
        icon.setStyle(Paint.Style.STROKE);
        icon.setStrokeWidth(s * 0.1f);
        rect.set(cx - mw * 0.95f, cy - mh * 0.45f, cx + mw * 0.95f, cy + mh * 0.5f);
        c.drawArc(rect, 20, 140, false, icon);
        c.drawLine(cx, cy + mh * 0.5f, cx, cy + mh * 0.72f, icon);
        c.drawLine(cx - mw * 0.45f, cy + mh * 0.72f, cx + mw * 0.45f, cy + mh * 0.72f, icon);
        icon.setStyle(Paint.Style.FILL);
    }

    private static int withAlpha(int c, int a) {
        a = Math.max(0, Math.min(255, a));
        return (c & 0x00FFFFFF) | (a << 24);
    }

    private static int lighten(int c) {
        int r = (c >> 16) & 0xFF, g = (c >> 8) & 0xFF, b = c & 0xFF;
        r = r + (255 - r) / 2; g = g + (255 - g) / 2; b = b + (255 - b) / 2;
        return 0xFF000000 | (r << 16) | (g << 8) | b;
    }
}
