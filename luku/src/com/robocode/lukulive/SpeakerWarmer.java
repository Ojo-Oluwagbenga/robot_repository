package com.robocode.lukulive;

import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTrack;

/**
 * Wakes the phone's speaker before LUKU talks and keeps it awake while he talks.
 *
 * Phones power the speaker amplifier (and Bluetooth links) down when nothing is playing; the first
 * few hundred milliseconds of the next sound are lost while it powers back up - that is the clipped
 * first word. This plays an inaudible signal (tiny dither, far below hearing) on the media output and
 * reports "ready" only once the playback position shows audio really is being rendered.
 */
final class SpeakerWarmer {
    private static final int RATE = 24000;

    private volatile AudioTrack track;
    private volatile boolean running;
    private Thread thread;
    private long startedAt;
    /** Set when two soft "processing" beeps should play as soon as the speaker is live. */
    private volatile boolean chimeRequested;
    /** Frame position at which the chime has finished playing (0 = no chime pending/playing). */
    private volatile long chimeEndFrame;
    private volatile long framesWritten;

    /** Two soft beeps ("I heard you, working on it"), played once the speaker is confirmed on. */
    void chimeWhenReady() {
        chimeRequested = true;
    }

    /** ~70 ms tones at 880 Hz then 1175 Hz, gentle fades, about 12% of full scale. */
    private static short[] makeChime() {
        int tone = RATE * 70 / 1000, gap = RATE * 70 / 1000, fade = RATE * 12 / 1000;
        short[] out = new short[tone * 2 + gap];
        double[] freqs = {880.0, 1174.66};
        for (int b = 0; b < 2; b++) {
            int off = b * (tone + gap);
            for (int i = 0; i < tone; i++) {
                double env = Math.min(1.0, Math.min(i / (double) fade, (tone - 1 - i) / (double) fade));
                double v = Math.sin(2 * Math.PI * freqs[b] * i / RATE) * env * 0.12;
                out[off + i] = (short) (v * 32767);
            }
        }
        return out;
    }

    synchronized void start() {
        if (running) return;
        try {
            int min = AudioTrack.getMinBufferSize(RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT);
            final AudioTrack t = new AudioTrack.Builder()
                    .setAudioAttributes(new AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_MEDIA)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                            .build())
                    .setAudioFormat(new AudioFormat.Builder()
                            .setSampleRate(RATE)
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                            .build())
                    .setBufferSizeInBytes(Math.max(min, RATE / 5 * 2))
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .build();
            track = t;
            framesWritten = 0;
            chimeEndFrame = 0;
            running = true;
            startedAt = System.currentTimeMillis();
            t.play();
            thread = new Thread(new Runnable() {
                @Override public void run() {
                    // +-2 LSB dither: about -84 dBFS, inaudible, but not digital silence (which some
                    // phones detect and use as a reason to power the amplifier down anyway).
                    short[] buf = new short[RATE / 50]; // 20 ms
                    java.util.Random rnd = new java.util.Random();
                    while (running) {
                        try {
                            // the chime goes out only once the speaker is really rendering sound,
                            // so the first beep isn't swallowed while the amplifier powers up
                            if (chimeRequested && t.getPlaybackHeadPosition() >= RATE * 12 / 100) {
                                chimeRequested = false;
                                short[] chime = makeChime();
                                int w = t.write(chime, 0, chime.length);
                                if (w < 0) break;
                                framesWritten += w;
                                chimeEndFrame = framesWritten;
                                continue;
                            }
                            for (int i = 0; i < buf.length; i++) buf[i] = (short) (rnd.nextInt(5) - 2);
                            int w = t.write(buf, 0, buf.length);
                            if (w < 0) break;
                            framesWritten += w;
                        } catch (Exception e) {
                            break;
                        }
                    }
                }
            }, "speaker-warmer");
            thread.start();
        } catch (Exception e) {
            running = false;
            track = null;
        }
    }

    /** True once at least ~120 ms of the warm-up signal has actually been played out. */
    boolean isReady() {
        AudioTrack t = track;
        if (t == null) return false;
        try {
            if (t.getPlayState() != AudioTrack.PLAYSTATE_PLAYING) return false;
            long head = t.getPlaybackHeadPosition();
            if (head < RATE * 12 / 100) return false;
            // let the processing beeps finish before LUKU starts talking over them
            if (chimeRequested) return false;
            return chimeEndFrame == 0 || head >= chimeEndFrame;
        } catch (Exception e) {
            return false;
        }
    }

    boolean isRunning() { return running; }

    long runningForMs() { return running ? System.currentTimeMillis() - startedAt : 0; }

    synchronized void stop() {
        running = false;
        chimeRequested = false;
        AudioTrack t = track;
        track = null;
        if (thread != null) {
            try { thread.join(200); } catch (InterruptedException ignored) { }
            thread = null;
        }
        if (t != null) {
            try { t.pause(); t.flush(); } catch (Exception ignored) { }
            try { t.release(); } catch (Exception ignored) { }
        }
    }
}
