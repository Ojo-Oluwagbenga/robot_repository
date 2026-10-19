package com.robocode.lukulive;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.Locale;
import java.util.Random;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * Always-listening voice conversation screen.
 * Loop: listen -> (partial transcript stable for ~650 ms) -> stream AI reply -> speak sentence by
 * sentence as it arrives -> listen again. No tapping needed.
 */
public class MainActivity extends Activity implements FirebaseSync.StatusListener {

    private static final int REQ_MIC = 42;
    private static final int BG = 0xFF0B1020, CARD = 0xFF161D33, TEXT = 0xFFE8ECF8, MUTED = 0xFF8A94B2, ACCENT = 0xFF2EC4B6;
    private static final int USER = 0, ASSISTANT = 1, TOOL = 2, ERROR = 3;

    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();

    private Prefs prefs;
    private Agent agent;
    private FirebaseSync firebase;
    private AudioManager audio;

    private SpeechRecognizer recognizer;
    private TextToSpeech tts;
    private boolean ttsReady;
    private PhoneSpeechReceiver phoneSpeechReceiver;

    private OrbView orb;
    private TextView stateLabel, partialView, instrStatus, fbStatus, emptyHint;
    private LinearLayout transcript;
    private ScrollView scroll;
    private EditText input;
    private TextView lastToolView, liveBubble;

    private int state = OrbView.IDLE;
    private boolean paused;          // user paused listening
    private boolean resumed;         // activity in foreground
    private volatile int generation; // bumps on every new turn / interruption
    private String sessionId;

    // speech endpointing
    private String lastPartial = "";
    private boolean finalizing;
    private final Runnable endpointRunnable = new Runnable() {
        @Override public void run() {
            if (state == OrbView.LISTENING && lastPartial.trim().length() > 0 && recognizer != null && !finalizing) {
                finalizing = true;
                try { recognizer.stopListening(); } catch (Exception ignored) { }
                // Some recognizers never deliver results after stopListening(); use the partial.
                main.postDelayed(finalizeFallback, 900);
            }
        }
    };
    private final Runnable finalizeFallback = new Runnable() {
        @Override public void run() {
            if (state == OrbView.LISTENING && finalizing && lastPartial.trim().length() > 0) {
                String text = lastPartial.trim();
                try { recognizer.cancel(); } catch (Exception ignored) { }
                onUserUtterance(text);
            }
        }
    };

    // streamed speech bookkeeping (main thread only)
    private final StringBuilder pendingSpeech = new StringBuilder();
    private int utterQueued, utterDone;
    private boolean replyStreamDone;
    private int speakingGen = -1;

    // beep muting
    private boolean beepMuted;

    // action confirmation (main thread only, except the latch/result)
    private static final class PendingConfirm {
        int gen;
        String doWhat, doing;
        int attempts;
        final CountDownLatch latch = new CountDownLatch(1);
        volatile Agent.Confirmation result;
    }
    private PendingConfirm pending;
    private final Runnable confirmTimeout = new Runnable() {
        @Override public void run() {
            if (pending == null) return;
            PendingConfirm pc = pending;
            if (recognizer != null) {
                try { recognizer.cancel(); } catch (Exception ignored) { }
            }
            finishConfirm(pc, new Agent.Confirmation(false, "no answer from the user", null));
            say("I didn't hear a yes, so I'll hold off.");
        }
    };

    // ------------------------------------------------------------------ lifecycle

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = new Prefs(this);
        audio = (AudioManager) getSystemService(AUDIO_SERVICE);
        if (prefs.getBool("beep_muted", false)) { beepMuted = true; unmuteBeeps(); } // recover after a crash
        agent = new Agent(prefs);
        firebase = new FirebaseSync(this, prefs);
        firebase.setListener(this);
        sessionId = newSessionId();
        buildUi();
        initTts();
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        phoneSpeechReceiver = new PhoneSpeechReceiver(prefs, new PhoneSpeechReceiver.Listener() {
            @Override public void onIncomingSpeech(final String id, final String text, final String source) {
                speakFromExternal(text, source);
            }
        });
        phoneSpeechReceiver.start();

        worker.execute(new Runnable() {
            @Override public void run() {
                agent.refreshInstruction(true);
                postInstructionStatus();
            }
        });
    }

    @Override protected void onResume() {
        super.onResume();
        resumed = true;
        agent.onSettingsChanged();
        agent.warmUpAsync();
        agent.refreshInstructionInBackground();
        if (tts != null && ttsReady) Voices.apply(tts, prefs);
        updateFirebaseLabel(null, true);
        postInstructionStatus();
        if (!paused && state == OrbView.IDLE) {
            main.postDelayed(new Runnable() {
                @Override public void run() { if (resumed && !paused && state == OrbView.IDLE) startListening(); }
            }, 300);
        }
    }

    @Override protected void onPause() {
        super.onPause();
        resumed = false;
        stopEverything();
        restoreVolume();
        setUiState(OrbView.IDLE, "Paused");
    }

    @Override protected void onDestroy() {
        super.onDestroy();
        if (phoneSpeechReceiver != null) {
            phoneSpeechReceiver.stop();
            phoneSpeechReceiver = null;
        }
        closeSpeakerGate();
        unmuteBeeps();
        if (recognizer != null) { recognizer.destroy(); recognizer = null; }
        if (tts != null) { tts.stop(); tts.shutdown(); }
        worker.shutdownNow();
    }

    // ------------------------------------------------------------------ UI

    private int dp(float v) { return (int) (v * getResources().getDisplayMetrics().density + 0.5f); }

    private GradientDrawable rounded(int color, float radiusDp) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(color);
        g.setCornerRadius(dp(radiusDp));
        return g;
    }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(BG);
        root.setFitsSystemWindows(true);

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(dp(18), dp(14), dp(10), dp(6));
        LinearLayout titles = new LinearLayout(this);
        titles.setOrientation(LinearLayout.VERTICAL);
        TextView title = new TextView(this);
        title.setText("LUKU Live");
        title.setTextColor(TEXT);
        title.setTextSize(22);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        instrStatus = small("Loading instructions…");
        fbStatus = small("");
        titles.addView(title);
        titles.addView(instrStatus);
        titles.addView(fbStatus);
        header.addView(titles, new LinearLayout.LayoutParams(0, -2, 1f));
        header.addView(iconButton("↻", "New conversation", new View.OnClickListener() {
            @Override public void onClick(View v) { newConversation(); }
        }));
        header.addView(iconButton("⚙", "Settings", new View.OnClickListener() {
            @Override public void onClick(View v) { startActivity(new Intent(MainActivity.this, SettingsActivity.class)); }
        }));
        root.addView(header);

        scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        transcript = new LinearLayout(this);
        transcript.setOrientation(LinearLayout.VERTICAL);
        transcript.setPadding(dp(14), dp(6), dp(14), dp(10));
        emptyHint = new TextView(this);
        emptyHint.setText("I'm listening — just talk.\n\nAsk me anything. I can search the web, read pages and sum them up, and carry out the actions in my live instructions.\n\nTap the orb to interrupt me or to pause listening.");
        emptyHint.setTextColor(MUTED);
        emptyHint.setTextSize(15);
        emptyHint.setGravity(Gravity.CENTER);
        emptyHint.setPadding(dp(24), dp(40), dp(24), dp(24));
        transcript.addView(emptyHint, new LinearLayout.LayoutParams(-1, -2));
        scroll.addView(transcript, new ScrollView.LayoutParams(-1, -2));
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1f));

        partialView = new TextView(this);
        partialView.setTextColor(0xFFB9C3E0);
        partialView.setTextSize(17);
        partialView.setTypeface(Typeface.DEFAULT, Typeface.ITALIC);
        partialView.setGravity(Gravity.CENTER);
        partialView.setPadding(dp(20), dp(4), dp(20), 0);
        partialView.setMaxLines(3);
        partialView.setEllipsize(TextUtils.TruncateAt.START);
        root.addView(partialView, new LinearLayout.LayoutParams(-1, -2));

        orb = new OrbView(this);
        orb.setContentDescription("Voice orb");
        orb.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) { onOrbTap(); }
        });
        root.addView(orb, new LinearLayout.LayoutParams(-1, dp(170)));

        stateLabel = new TextView(this);
        stateLabel.setText("Starting…");
        stateLabel.setTextColor(MUTED);
        stateLabel.setTextSize(14);
        stateLabel.setGravity(Gravity.CENTER);
        root.addView(stateLabel, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(12), dp(10), dp(12), dp(12));
        input = new EditText(this);
        input.setHint("Or type a message…");
        input.setHintTextColor(MUTED);
        input.setTextColor(TEXT);
        input.setTextSize(15);
        input.setSingleLine(true);
        input.setImeOptions(EditorInfo.IME_ACTION_SEND);
        input.setBackground(rounded(CARD, 22));
        input.setPadding(dp(16), dp(10), dp(16), dp(10));
        input.setOnEditorActionListener(new TextView.OnEditorActionListener() {
            @Override public boolean onEditorAction(TextView v, int actionId, KeyEvent event) {
                if (actionId == EditorInfo.IME_ACTION_SEND || (event != null && event.getKeyCode() == KeyEvent.KEYCODE_ENTER)) {
                    sendTyped();
                    return true;
                }
                return false;
            }
        });
        row.addView(input, new LinearLayout.LayoutParams(0, dp(46), 1f));
        Button send = new Button(this);
        send.setText("➤");
        send.setTextColor(BG);
        send.setTextSize(18);
        send.setBackground(rounded(ACCENT, 23));
        send.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) { sendTyped(); }
        });
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(dp(46), dp(46));
        sp.leftMargin = dp(8);
        row.addView(send, sp);
        root.addView(row);

        setContentView(root);
    }

    private TextView small(String s) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextColor(MUTED);
        t.setTextSize(12);
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        return t;
    }

    private TextView iconButton(String glyph, String desc, View.OnClickListener l) {
        TextView b = new TextView(this);
        b.setText(glyph);
        b.setContentDescription(desc);
        b.setTextColor(TEXT);
        b.setTextSize(22);
        b.setGravity(Gravity.CENTER);
        b.setBackground(rounded(CARD, 20));
        b.setOnClickListener(l);
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(dp(42), dp(42));
        p.leftMargin = dp(8);
        b.setLayoutParams(p);
        return b;
    }

    private TextView addBubble(String text, int kind) {
        if (emptyHint.getParent() != null) transcript.removeView(emptyHint);
        TextView t = new TextView(this);
        t.setText(text);
        t.setTextIsSelectable(kind == ASSISTANT || kind == USER);
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-2, -2);
        p.topMargin = dp(kind == TOOL ? 4 : 10);
        t.setMaxWidth((int) (getResources().getDisplayMetrics().widthPixels * 0.82f));
        switch (kind) {
            case USER:
                t.setTextColor(0xFF06121F);
                t.setTextSize(16);
                t.setBackground(rounded(ACCENT, 18));
                t.setPadding(dp(14), dp(10), dp(14), dp(10));
                p.gravity = Gravity.END;
                break;
            case ASSISTANT:
                t.setTextColor(TEXT);
                t.setTextSize(16);
                t.setLineSpacing(0, 1.15f);
                t.setBackground(rounded(CARD, 18));
                t.setPadding(dp(14), dp(10), dp(14), dp(10));
                p.gravity = Gravity.START;
                break;
            case TOOL:
                t.setTextColor(0xFF9FB0DA);
                t.setTextSize(13);
                t.setBackground(rounded(0xFF10172A, 12));
                t.setPadding(dp(10), dp(5), dp(10), dp(5));
                p.gravity = Gravity.START;
                break;
            default:
                t.setTextColor(0xFFFFB4B4);
                t.setTextSize(14);
                t.setBackground(rounded(0xFF3A1620, 12));
                t.setPadding(dp(12), dp(8), dp(12), dp(8));
                p.gravity = Gravity.START;
        }
        transcript.addView(t, p);
        scrollToEnd();
        return t;
    }

    private void scrollToEnd() {
        scroll.post(new Runnable() {
            @Override public void run() { scroll.fullScroll(View.FOCUS_DOWN); }
        });
    }

    private void setUiState(int s, String label) {
        state = s;
        orb.setState(s);
        stateLabel.setText(label);
        stateLabel.setTextColor(s == OrbView.IDLE ? MUTED : TEXT);
    }

    private void toast(String s) { Toast.makeText(this, s, Toast.LENGTH_SHORT).show(); }

    private void postInstructionStatus() {
        String eng = agent.engineLabel();
        final String s = agent.instructionStatus() + " · " + eng;
        main.post(new Runnable() {
            @Override public void run() { instrStatus.setText("● " + s); }
        });
    }

    private void updateFirebaseLabel(String s, boolean ok) {
        if (!firebase.isConfigured()) {
            fbStatus.setText("○ Firebase not set up (Settings)");
            fbStatus.setTextColor(MUTED);
            return;
        }
        fbStatus.setText("● " + (s == null ? "Firebase ready" : s));
        fbStatus.setTextColor(ok ? 0xFF7FD8A8 : 0xFFFF9A9A);
    }

    @Override public void onFirebaseStatus(final String status, final boolean ok) {
        main.post(new Runnable() {
            @Override public void run() { updateFirebaseLabel(status, ok); }
        });
    }

    // ------------------------------------------------------------------ interaction

    private void onOrbTap() {
        switch (state) {
            case OrbView.LISTENING:
                paused = true;
                stopEverything();
                setUiState(OrbView.IDLE, "Paused — tap to talk");
                break;
            case OrbView.IDLE:
                paused = false;
                startListening();
                break;
            default: // thinking or speaking: interrupt and listen right away
                interruptAndListen();
        }
    }

    private void interruptAndListen() {
        generation++;
        stopSpeech();
        paused = false;
        startListening();
    }

    private void stopEverything() {
        generation++;
        if (pending != null) finishConfirm(pending, new Agent.Confirmation(false, "cancelled", null));
        main.removeCallbacks(endpointRunnable);
        main.removeCallbacks(finalizeFallback);
        if (recognizer != null) {
            try { recognizer.cancel(); } catch (Exception ignored) { }
        }
        stopSpeech();
        unmuteBeeps();
        partialView.setText("");
        orb.setRms(-2);
    }

    private void stopSpeech() {
        closeSpeakerGate();
        releaseAudioFocus();
        speakingGen = -1;
        pendingSpeech.setLength(0);
        if (tts != null) tts.stop();
    }

    private void newConversation() {
        stopEverything();
        agent.resetConversation();
        transcript.removeAllViews();
        transcript.addView(emptyHint, new LinearLayout.LayoutParams(-1, -2));
        sessionId = newSessionId();
        agent.refreshInstructionInBackground();
        toast("New conversation");
        paused = false;
        startListening();
    }

    private void sendTyped() {
        String text = input.getText().toString().trim();
        if (text.length() == 0) return;
        input.setText("");
        InputMethodManager imm = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
        if (imm != null) imm.hideSoftInputFromWindow(input.getWindowToken(), 0);
        if (pending != null) {
            if (recognizer != null) {
                try { recognizer.cancel(); } catch (Exception ignored) { }
            }
        } else {
            stopEverything();
        }
        typedInput = true;
        onUserUtterance(text);
    }

    /** A finished user utterance (spoken or typed): stream the reply and speak it as it arrives. */
    /** True unless the current utterance was typed (typed messages don't get the processing beeps). */
    private boolean typedInput;

    private void onUserUtterance(final String text) {
        final boolean voiceInput = !typedInput;
        typedInput = false;
        main.removeCallbacks(endpointRunnable);
        main.removeCallbacks(finalizeFallback);
        finalizing = false;
        lastPartial = "";
        partialView.setText("");
        orb.setRms(-2);
        if (pending != null) {
            handleConfirmReply(text);
            return;
        }
        addBubble(text, USER);
        firebase.log(sessionId, "user", text, agent.engineLabel());

        setUiState(OrbView.THINKING, "Thinking\u2026");

        final int gen = ++generation;
        speakingGen = gen;
        utterQueued = 0;
        utterDone = 0;
        replyStreamDone = false;
        pendingSpeech.setLength(0);
        liveBubble = null;
        unmuteBeeps(); // TTS shares the media stream
        warmSpeaker(); // wake the speaker while the AI is thinking, so the reply can start instantly
        if (voiceInput && prefs.processingBeeps()) warmer.chimeWhenReady(); // two soft beeps: "got it, thinking"

        final Agent.Listener listener = new Agent.Listener() {
            @Override public void onText(final String delta) {
                main.post(new Runnable() {
                    @Override public void run() {
                        if (gen != generation) return;
                        if (liveBubble == null) liveBubble = addBubble("", ASSISTANT);
                        liveBubble.append(delta);
                        scrollToEnd();
                        pendingSpeech.append(delta);
                        flushSpeech(false);
                    }
                });
            }
            @Override public void onTool(final String kind, final String label) {
                main.post(new Runnable() {
                    @Override public void run() {
                        if (gen != generation) return;
                        flushSpeech(true);
                        liveBubble = null;
                        showTool(kind, label);
                    }
                });
            }
            @Override public void onToolResult(final String kind, final String label, final boolean ok) {
                firebase.log(sessionId, "tool", kind + ": " + label + (ok ? " [ok]" : " [failed]"), agent.engineLabel());
                main.post(new Runnable() {
                    @Override public void run() {
                        if (gen != generation || lastToolView == null) return;
                        lastToolView.append(ok ? "  ✓" : "  ✗");
                    }
                });
            }
            @Override public boolean isCancelled() { return gen != generation; }
            @Override public Agent.Confirmation confirmAction(final String doWhat, final String doing) {
                final PendingConfirm pc = new PendingConfirm();
                pc.gen = gen;
                pc.doWhat = doWhat;
                pc.doing = doing;
                main.post(new Runnable() {
                    @Override public void run() {
                        if (gen != generation) {
                            pc.result = new Agent.Confirmation(false, "cancelled", null);
                            pc.latch.countDown();
                            return;
                        }
                        beginConfirmation(pc);
                    }
                });
                try {
                    while (!pc.latch.await(200, TimeUnit.MILLISECONDS)) {
                        if (gen != generation) return new Agent.Confirmation(false, "cancelled", null);
                    }
                } catch (InterruptedException e) {
                    return new Agent.Confirmation(false, "cancelled", null);
                }
                return pc.result;
            }
        };

        worker.execute(new Runnable() {
            @Override public void run() {
                try {
                    final String reply = agent.respond(text, listener);
                    firebase.log(sessionId, "assistant", reply, agent.lastEngine());
                    main.post(new Runnable() {
                        @Override public void run() {
                            if (gen != generation) return;
                            if (liveBubble == null && utterQueued == 0 && pendingSpeech.length() == 0) {
                                addBubble(reply, ASSISTANT);
                                pendingSpeech.append(reply);
                            }
                            flushSpeech(true);
                            replyStreamDone = true;
                            checkSpeechFinished();
                        }
                    });
                } catch (Agent.Cancelled c) {
                    // user interrupted; nothing to do
                } catch (final Exception e) {
                    main.post(new Runnable() {
                        @Override public void run() {
                            if (gen != generation) return;
                            String msg = e.getMessage() == null ? e.toString() : e.getMessage();
                            addBubble("⚠ " + msg, ERROR);
                            firebase.log(sessionId, "error", msg, agent.engineLabel());
                            pendingSpeech.setLength(0);
                            pendingSpeech.append("Sorry, ").append(msg);
                            flushSpeech(true);
                            replyStreamDone = true;
                            checkSpeechFinished();
                        }
                    });
                }
            }
        });
    }

    private void showTool(String kind, String label) {
        String text;
        String url = null;
        if ("search".equals(kind)) {
            text = "🔎 Searching the web: " + label;
            if (utterQueued == 0) setUiState(OrbView.THINKING, "Searching the web…");
        } else if ("browse".equals(kind)) {
            int bar = label.indexOf('|');
            String host = bar >= 0 ? label.substring(0, bar) : label;
            url = bar >= 0 ? label.substring(bar + 1) : null;
            text = "🌐 Reading " + host;
            if (utterQueued == 0) setUiState(OrbView.THINKING, "Reading " + host + "…");
        } else if ("info".equals(kind)) {
            text = "ℹ " + label;
        } else {
            text = "⚡ Action: " + label;
        }
        lastToolView = addBubble(text, TOOL);
        if (url != null) {
            final String open = url.startsWith("http") ? url : "https://" + url;
            lastToolView.setOnClickListener(new View.OnClickListener() {
                @Override public void onClick(View v) {
                    try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(open))); } catch (Exception ignored) { }
                }
            });
        }
    }

    // ------------------------------------------------------------------ action confirmation

    private void beginConfirmation(PendingConfirm pc) {
        pending = pc;
        flushSpeech(true);
        liveBubble = null;
        String question = "Just to confirm, should I " + pc.doWhat + "?";
        firebase.log(sessionId, "assistant", question, agent.engineLabel());
        main.removeCallbacks(confirmTimeout);
        main.postDelayed(confirmTimeout, 40000);
        say(question);
    }

    /** Adds an assistant line to the transcript and speaks it within the current turn. */
    private void say(String line) {
        TextView b = addBubble(line, ASSISTANT);
        if (pending != null) b.setBackground(rounded(0xFF2A2350, 18));
        int before = utterQueued;
        speakChunk(line);
        if (utterQueued == before) checkSpeechFinished(); // no TTS: move on immediately
    }

    /**
     * Voices out incoming text dispatched from the Raspberry Pi or Bridge Site over the connected speaker.
     */
    void speakFromExternal(final String text, final String source) {
        if (text == null || text.trim().isEmpty()) return;
        main.post(new Runnable() {
            @Override public void run() {
                final String clean = text.trim();
                // Stop microphone if currently listening to prevent audio loopback
                if (recognizer != null) {
                    try { recognizer.cancel(); } catch (Exception ignored) { }
                }
                main.removeCallbacks(endpointRunnable);
                main.removeCallbacks(finalizeFallback);
                finalizing = false;
                lastPartial = "";
                partialView.setText("");

                // Add bubble to conversation transcript
                TextView bubble = addBubble(clean, ASSISTANT);
                bubble.setBackground(rounded(0xFF1E284A, 18)); // subtle distinct tint for robot hardware voice
                if (firebase != null) {
                    firebase.log(sessionId, "assistant", clean, "pi:" + (source != null ? source : "bridge"));
                }

                // Bump generation and configure speech turn
                final int gen = ++generation;
                speakingGen = gen;
                utterQueued = 0;
                utterDone = 0;
                replyStreamDone = true;
                pendingSpeech.setLength(0);
                liveBubble = null;

                unmuteBeeps();
                warmSpeaker();
                prepareAudioForSpeech();
                setUiState(OrbView.SPEAKING, "Speaking from Pi \u2014 tap to interrupt");

                speakChunk(clean);
                if (utterQueued == 0) {
                    checkSpeechFinished();
                }
            }
        });
    }

    private void handleConfirmReply(String text) {
        PendingConfirm pc = pending;
        addBubble(text, USER);
        firebase.log(sessionId, "user", text, agent.engineLabel());
        int verdict = classifyYesNo(text);
        if (verdict > 0) {
            finishConfirm(pc, new Agent.Confirmation(true, "approved by the user", null));
            setUiState(OrbView.THINKING, "Working\u2026");
            say("Alright, " + pc.doing + " now.");
        } else if (verdict < 0) {
            finishConfirm(pc, new Agent.Confirmation(false, "the user said no", null));
            setUiState(OrbView.THINKING, "Okay\u2026");
            say("Okay, I won't.");
        } else if (pc.attempts == 0) {
            pc.attempts++;
            say("Sorry, was that a yes or a no?");
        } else {
            finishConfirm(pc, new Agent.Confirmation(false, "the user did not clearly confirm", text));
            setUiState(OrbView.THINKING, "Thinking\u2026");
        }
    }

    private void finishConfirm(PendingConfirm pc, Agent.Confirmation c) {
        main.removeCallbacks(confirmTimeout);
        if (pending == pc) pending = null;
        if (pc.result == null) {
            pc.result = c;
            pc.latch.countDown();
            firebase.log(sessionId, "tool", "confirmation: " + pc.doWhat + " -> " + (c.approved ? "approved" : c.reason),
                    agent.engineLabel());
        }
    }

    /** 1 = yes, -1 = no, 0 = unclear. Includes a few Nigerian/Yoruba replies. */
    static int classifyYesNo(String text) {
        String s = " " + text.toLowerCase(Locale.US).replace("\u2019", "'").replaceAll("[^a-z' ]", " ")
                .replaceAll("\\s+", " ").trim() + " ";
        String[] yesDespiteNegation = {" why not ", " no problem ", " no wahala ", " not a problem ", " no doubt "};
        for (String w : yesDespiteNegation) if (s.contains(w)) return 1;
        String[] no = {" no ", " nope ", " nah ", " don't ", " dont ", " do not ", " cancel ", " never mind ",
                " nevermind ", " not now ", " wait ", " hold on ", " abort ", " negative ", " stop ", " rara ",
                " not ", " skip ", " forget it ", " leave it "};
        for (String w : no) if (s.contains(w)) return -1;
        String[] yes = {" yes ", " yeah ", " yea ", " yep ", " yup ", " ya ", " sure ", " ok ", " okay ", " go ahead ",
                " do it ", " proceed ", " confirm ", " confirmed ", " affirmative ", " please do ", " of course ",
                " absolutely ", " alright ", " all right ", " go on ", " carry on ", " go for it ", " beeni ", " correct ",
                " definitely ", " certainly ", " sounds good ", " let's go ", " lets go ", " oya ", " you can "};
        for (String w : yes) if (s.contains(w)) return 1;
        return 0;
    }

    // ------------------------------------------------------------------ streamed speech

    /** Speaks every complete sentence in the pending buffer (or everything when force is true). */
    private void flushSpeech(boolean force) {
        while (true) {
            int cut = sentenceCut(pendingSpeech, force);
            if (cut <= 0) break;
            String chunk = pendingSpeech.substring(0, cut);
            pendingSpeech.delete(0, cut);
            speakChunk(chunk);
            if (force && pendingSpeech.length() == 0) break;
        }
    }

    /** Index just past the end of the first speakable chunk, or 0 when none is ready yet. */
    private int sentenceCut(CharSequence s, boolean force) {
        int n = s.length();
        if (n == 0) return 0;
        for (int i = 0; i < n - 1; i++) {
            char c = s.charAt(i);
            if (c == '\n') return i + 1;
            if ((c == '.' || c == '!' || c == '?' || c == ';' || c == '…') && Character.isWhitespace(s.charAt(i + 1))) {
                // avoid cutting "e.g. " / "Dr. " style abbreviations with very short fragments
                if (i >= 3 || force) return i + 1;
            }
        }
        // first chunk: start talking early on a comma once there is enough text
        if (utterQueued == 0 && n > 60) {
            for (int i = n - 2; i >= 25; i--) {
                if (s.charAt(i) == ',' && Character.isWhitespace(s.charAt(i + 1))) return i + 1;
            }
        }
        if (n > 220) {
            for (int i = 200; i > 40; i--) if (Character.isWhitespace(s.charAt(i))) return i + 1;
        }
        return force ? n : 0;
    }

    private void speakChunk(String raw) {
        String text = forSpeech(raw);
        if (text.length() == 0 || !ttsReady || speakingGen != generation) return;
        if (state != OrbView.SPEAKING) {
            // make sure the mic is closed while we talk (prevents hearing ourselves)
            if (recognizer != null) {
                try { recognizer.cancel(); } catch (Exception ignored) { }
            }
            prepareAudioForSpeech();
            setUiState(OrbView.SPEAKING, "Speaking \u2014 tap to interrupt");
        }
        String id = "g" + speakingGen + "_" + utterQueued;
        utterQueued++;
        if (gateOpen) {
            sayNow(text, id);
        } else {
            // Hold the words until the speaker is confirmed live, so the first word isn't clipped.
            gated.add(new String[]{text, id});
            openGateWhenSpeakerReady();
        }
    }

    // ------------------------------------------------------------------ speaker gate

    private final SpeakerWarmer warmer = new SpeakerWarmer();
    private final ArrayList<String[]> gated = new ArrayList<String[]>();
    private boolean gateOpen;
    private boolean gateCheckScheduled;
    private long gateStartedAt, speakerReadySince;

    private void sayNow(String text, String id) {
        Bundle params = new Bundle();
        params.putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, 1.0f);
        params.putInt(TextToSpeech.Engine.KEY_PARAM_STREAM, AudioManager.STREAM_MUSIC);
        tts.speak(text, TextToSpeech.QUEUE_ADD, params, id);
    }

    /** Starts waking the speaker early (e.g. while the AI is still thinking) so there is no extra wait later. */
    private void warmSpeaker() {
        warmer.start();
    }

    private void openGateWhenSpeakerReady() {
        warmer.start();
        if (gateCheckScheduled) return;
        gateCheckScheduled = true;
        gateStartedAt = System.currentTimeMillis();
        speakerReadySince = 0;
        main.post(gateCheck);
    }

    /** Media output un-muted, audible, normal (not call) audio mode. Fixes what it can. */
    private boolean speakerAudible() {
        if (audio == null) return true;
        try {
            if (audio.isStreamMute(AudioManager.STREAM_MUSIC)) {
                beepMuted = true;
                unmuteBeeps();
                return false;
            }
            if (audio.getStreamVolume(AudioManager.STREAM_MUSIC) == 0) {
                prepareAudioForSpeech();
                return false;
            }
            if (audio.getMode() != AudioManager.MODE_NORMAL) {
                audio.setMode(AudioManager.MODE_NORMAL);
                return false;
            }
        } catch (Exception ignored) { }
        return true;
    }

    private final Runnable gateCheck = new Runnable() {
        @Override public void run() {
            if (gateOpen || gated.isEmpty() || speakingGen != generation) {
                gateCheckScheduled = false;
                return;
            }
            long now = System.currentTimeMillis();
            boolean ready = warmer.isReady() && speakerAudible();
            if (ready) {
                if (speakerReadySince == 0) speakerReadySince = now;
            } else {
                speakerReadySince = 0;
            }
            boolean settled = ready && now - speakerReadySince >= prefs.leadInMs();
            boolean timedOut = now - gateStartedAt > 1800; // never hold speech back for long
            if (settled || timedOut) {
                gateCheckScheduled = false;
                gateOpen = true;
                for (String[] g : gated) sayNow(g[0], g[1]);
                gated.clear();
            } else {
                main.postDelayed(this, 20);
            }
        }
    };

    /** Speech finished or cancelled: let the speaker sleep again and close the gate for next time. */
    private void closeSpeakerGate() {
        main.removeCallbacks(gateCheck);
        gateCheckScheduled = false;
        gateOpen = false;
        gated.clear();
        warmer.stop();
    }

    private int savedVolume = -1;

    /** Loud, unclipped speech: normal audio mode, stream un-muted, and a comfortable minimum volume. */
    private AudioFocusRequest focusRequest;

    /** Holding audio focus while talking stops other apps (and the recognizer) from ducking our voice. */
    private void takeAudioFocus() {
        if (audio == null || focusRequest != null) return;
        try {
            focusRequest = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE)
                    .setAudioAttributes(new AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_MEDIA)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                    .setWillPauseWhenDucked(false)
                    .build();
            audio.requestAudioFocus(focusRequest);
        } catch (Exception e) {
            focusRequest = null;
        }
    }

    private void releaseAudioFocus() {
        if (audio == null || focusRequest == null) return;
        try { audio.abandonAudioFocusRequest(focusRequest); } catch (Exception ignored) { }
        focusRequest = null;
    }

    private void prepareAudioForSpeech() {
        main.removeCallbacks(unmuteRunnable);
        unmuteBeeps();
        if (audio == null) return;
        takeAudioFocus();
        try {
            // Speech recognition can leave the phone in "call" mode, which routes audio quietly.
            if (audio.getMode() != AudioManager.MODE_NORMAL) audio.setMode(AudioManager.MODE_NORMAL);
            if (audio.isSpeakerphoneOn() && !audio.isWiredHeadsetOn() && !audio.isBluetoothA2dpOn()) {
                audio.setSpeakerphoneOn(false);
            }
        } catch (Exception ignored) { }
        int minPct = prefs.minVolumePercent();
        if (minPct <= 0) return;
        try {
            int max = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
            int cur = audio.getStreamVolume(AudioManager.STREAM_MUSIC);
            int target = (int) Math.ceil(max * minPct / 100.0);
            if (cur < target) {
                if (savedVolume < 0) savedVolume = cur;
                audio.setStreamVolume(AudioManager.STREAM_MUSIC, target, 0);
            }
        } catch (Exception ignored) { }
    }

    /** Gives the user back their own media volume when they leave the app. */
    private void restoreVolume() {
        if (savedVolume < 0 || audio == null) return;
        try { audio.setStreamVolume(AudioManager.STREAM_MUSIC, savedVolume, 0); } catch (Exception ignored) { }
        savedVolume = -1;
    }

    private void onUtteranceFinished(String id) {
        if (!id.startsWith("g" + speakingGen + "_") || speakingGen != generation) return;
        utterDone++;
        checkSpeechFinished();
    }

    private void checkSpeechFinished() {
        if (utterDone < utterQueued || speakingGen != generation) return;
        if (pending != null) {
            // question asked - listen for the yes / no
            closeSpeakerGate();
            if (resumed) {
                startListening();
                stateLabel.setText("Waiting for yes or no\u2026");
            }
            return;
        }
        if (!replyStreamDone) {
            if (state == OrbView.SPEAKING) setUiState(OrbView.THINKING, "Working\u2026");
            return;
        }
        closeSpeakerGate();
        releaseAudioFocus();
        speakingGen = -1;
        if (resumed && !paused) {
            startListening();
        } else {
            setUiState(OrbView.IDLE, paused ? "Paused — tap to talk" : "Paused");
        }
    }

    static String forSpeech(String s) {
        s = s.replaceAll("\\[([^\\]]*)\\]\\([^)]*\\)", "$1");
        s = s.replaceAll("https?://\\S+", "");
        s = s.replaceAll("(?m)^\\s*[-*•]\\s+", "");
        s = s.replaceAll("[*_#`>|~]+", "");
        return s.replaceAll("\\s+", " ").trim();
    }

    // ------------------------------------------------------------------ speech recognition (always on)

    private boolean hasMic() {
        return checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
    }

    @Override public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        super.onRequestPermissionsResult(code, perms, results);
        if (code != REQ_MIC) return;
        if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
            paused = false;
            startListening();
        } else {
            paused = true;
            setUiState(OrbView.IDLE, "Microphone permission needed — tap to allow");
        }
    }

    private void ensureRecognizer() {
        if (recognizer != null) return;
        recognizer = SpeechRecognizer.createSpeechRecognizer(this);
        recognizer.setRecognitionListener(new RecognitionListener() {
            @Override public void onReadyForSpeech(Bundle params) {
                if (state == OrbView.LISTENING) stateLabel.setText(pending != null ? "Waiting for yes or no\u2026" : "Listening\u2026");
                // The start beep has played by now (muted). Un-mute straight away, so the speaker is
                // already live long before the reply starts - un-muting at the last moment clipped words.
                main.removeCallbacks(unmuteRunnable);
                main.postDelayed(unmuteRunnable, 450);
            }
            @Override public void onBeginningOfSpeech() {
                agent.warmUpAsync(); // open the AI connection while the user is still talking
            }
            @Override public void onRmsChanged(float rmsdB) { orb.setRms(rmsdB); }
            @Override public void onBufferReceived(byte[] buffer) { }
            @Override public void onEndOfSpeech() { }
            @Override public void onError(int error) { onRecognizerError(error); }
            @Override public void onResults(Bundle results) {
                main.removeCallbacks(endpointRunnable);
                main.removeCallbacks(finalizeFallback);
                if (state != OrbView.LISTENING) return;
                ArrayList<String> list = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                String text = list != null && !list.isEmpty() ? list.get(0).trim() : "";
                if (text.length() == 0) text = lastPartial.trim();
                if (text.length() == 0) { restartListeningSoon(0); return; }
                onUserUtterance(text);
            }
            @Override public void onPartialResults(Bundle partial) {
                if (state != OrbView.LISTENING || finalizing) return;
                ArrayList<String> list = partial.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                if (list == null || list.isEmpty()) return;
                String p = list.get(0);
                if (p == null || p.trim().length() == 0 || p.equals(lastPartial)) return;
                lastPartial = p;
                partialView.setText(p);
                main.removeCallbacks(endpointRunnable);
                main.postDelayed(endpointRunnable, prefs.endpointMs());
            }
            @Override public void onEvent(int eventType, Bundle params) { }
        });
    }

    private void startListening() {
        if (!resumed) return;
        releaseAudioFocus();
        if (!hasMic()) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC);
            return;
        }
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            paused = true;
            setUiState(OrbView.IDLE, "Voice unavailable — type below");
            addBubble("Speech recognition isn't available on this phone. Install or enable the Google app / Speech Services.", ERROR);
            return;
        }
        ensureRecognizer();
        main.removeCallbacks(endpointRunnable);
        main.removeCallbacks(finalizeFallback);
        finalizing = false;
        lastPartial = "";
        Intent i = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        i.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        i.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);
        i.putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, getPackageName());
        i.putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 800L);
        i.putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 700L);
        i.putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS, 60000L);
        setUiState(OrbView.LISTENING, "Listening…");
        muteBeeps();
        try {
            recognizer.startListening(i);
        } catch (Exception e) {
            recreateRecognizer();
            restartListeningSoon(300);
        }
    }

    private void restartListeningSoon(long delayMs) {
        final int gen = generation;
        main.postDelayed(new Runnable() {
            @Override public void run() {
                if (gen == generation && resumed && !paused && state == OrbView.LISTENING) startListening();
            }
        }, delayMs);
    }

    private void recreateRecognizer() {
        if (recognizer != null) {
            try { recognizer.destroy(); } catch (Exception ignored) { }
            recognizer = null;
        }
    }

    private int networkErrors;

    private void onRecognizerError(int error) {
        if (state != OrbView.LISTENING) return;
        orb.setRms(-2);
        if (finalizing && lastPartial.trim().length() > 0) {
            // stopListening() on some devices reports an error instead of results; use what we heard
            onUserUtterance(lastPartial.trim());
            return;
        }
        switch (error) {
            case SpeechRecognizer.ERROR_NO_MATCH:
            case SpeechRecognizer.ERROR_SPEECH_TIMEOUT:
                networkErrors = 0;
                restartListeningSoon(0); // silence is fine - keep listening forever
                break;
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY:
            case SpeechRecognizer.ERROR_CLIENT:
                recreateRecognizer();
                restartListeningSoon(250);
                break;
            case SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS:
                paused = true;
                setUiState(OrbView.IDLE, "Microphone permission needed — tap to allow");
                break;
            case SpeechRecognizer.ERROR_NETWORK:
            case SpeechRecognizer.ERROR_NETWORK_TIMEOUT:
            case 10: // ERROR_TOO_MANY_REQUESTS
            case 11: // ERROR_SERVER_DISCONNECTED
                networkErrors++;
                if (networkErrors == 3) addBubble("Speech recognition can't reach the network. I'll keep retrying.", ERROR);
                stateLabel.setText("Reconnecting…");
                recreateRecognizer();
                restartListeningSoon(Math.min(5000, 700L * networkErrors));
                break;
            default:
                recreateRecognizer();
                restartListeningSoon(400);
        }
    }

    // ------------------------------------------------------------------ beep muting

    private final Runnable unmuteRunnable = new Runnable() {
        @Override public void run() { unmuteBeeps(); }
    };

    private void muteBeeps() {
        if (!prefs.muteBeeps() || beepMuted || audio == null) return;
        try {
            audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_MUTE, 0);
            audio.adjustStreamVolume(AudioManager.STREAM_SYSTEM, AudioManager.ADJUST_MUTE, 0);
            beepMuted = true;
            prefs.putBool("beep_muted", true);
        } catch (Exception ignored) { }
    }

    private void unmuteBeeps() {
        if (!beepMuted || audio == null) return;
        try {
            audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_UNMUTE, 0);
            audio.adjustStreamVolume(AudioManager.STREAM_SYSTEM, AudioManager.ADJUST_UNMUTE, 0);
        } catch (Exception ignored) { }
        beepMuted = false;
        prefs.putBool("beep_muted", false);
    }

    // ------------------------------------------------------------------ text to speech

    private void initTts() {
        tts = new TextToSpeech(this, new TextToSpeech.OnInitListener() {
            @Override public void onInit(int status) {
                ttsReady = status == TextToSpeech.SUCCESS;
                if (!ttsReady) {
                    toast("Text-to-speech isn't available; replies will only be shown.");
                    return;
                }
                try {
                    tts.setAudioAttributes(new AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_MEDIA)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                            .build());
                } catch (Exception ignored) { }
                final String voice = Voices.apply(tts, prefs);
                main.post(new Runnable() { @Override public void run() { toast("Voice: " + voice); } });
                tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                    @Override public void onStart(String id) { }
                    @Override public void onDone(final String id) {
                        main.post(new Runnable() { @Override public void run() { onUtteranceFinished(id); } });
                    }
                    @Override public void onError(final String id) {
                        main.post(new Runnable() { @Override public void run() { onUtteranceFinished(id); } });
                    }
                });
            }
        });
    }

    private static String newSessionId() {
        return new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(new Date())
                + "-" + Integer.toHexString(new Random().nextInt(0xFFFF));
    }
}
