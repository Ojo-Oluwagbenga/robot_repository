package com.robocode.lukulive;

import android.app.Activity;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.CompoundButton;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.ArrayAdapter;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.speech.tts.TextToSpeech;
import android.speech.tts.Voice;
import java.util.ArrayList;
import java.util.List;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

public class SettingsActivity extends Activity {
    private static final int BG = 0xFF0B1020, CARD = 0xFF161D33, TEXT = 0xFFE8ECF8, MUTED = 0xFF8A94B2, ACCENT = 0xFF2EC4B6;

    private Prefs prefs;
    private EditText minVolume, freeCodes, leadIn;
    private EditText geminiKey, model, fastKey, fastModel, fastBase, instrUrl, fbKey, fbUrl, rate, endpoint, pitch;
    private Spinner voiceSpinner;
    private TextToSpeech tts;
    private final List<String> voiceNames = new ArrayList<String>();
    private RadioGroup provider;
    private TextView output;
    private final Handler main = new Handler(Looper.getMainLooper());

    private int dp(float v) { return (int) (v * getResources().getDisplayMetrics().density + 0.5f); }

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = new Prefs(this);

        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(BG);
        scroll.setFitsSystemWindows(true);
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(dp(18), dp(16), dp(18), dp(32));
        scroll.addView(col);

        TextView title = new TextView(this);
        title.setText("Settings");
        title.setTextColor(TEXT);
        title.setTextSize(22);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        col.addView(title);

        section(col, "AI engine");
        provider = new RadioGroup(this);
        provider.addView(radio(2, "Gemini (default \u2014 streamed, low-latency)"));
        provider.addView(radio(3, "Fast cloud engine (Groq / OpenAI-compatible, needs a key)"));
        provider.check(Prefs.PROVIDER_FAST.equals(prefs.provider()) ? 3 : 2);
        col.addView(provider);

        section(col, "Gemini");
        geminiKey = field(col, "API key", prefs.geminiKey(), true);
        model = field(col, "Model (full Flash with minimal thinking; falls back automatically if unavailable)", prefs.model(), false);

        section(col, "Fast engine (Groq — free key at console.groq.com)");
        fastKey = field(col, "API key (gsk_…)", prefs.fastKey(), true);
        fastModel = field(col, "Model", prefs.fastModel(), false);
        fastBase = field(col, "Base URL (any OpenAI-compatible API works)", prefs.fastBaseUrl(), false);

        section(col, "Live instructions");
        instrUrl = field(col, "Instruction URL (re-fetched in the background every minute)", prefs.instructionUrl(), false);
        Button reload = button("Fetch & preview instructions");
        col.addView(reload);

        section(col, "Firebase (Realtime Database + Anonymous Auth)");
        fbUrl = field(col, "Realtime Database URL, e.g. https://your-project-default-rtdb.firebaseio.com", prefs.firebaseDbUrl(), false);
        fbKey = field(col, "Web API key (Project settings → General). Leave empty for open rules", prefs.firebaseApiKey(), true);
        Button test = button("Save & test Firebase");
        col.addView(test);

        section(col, "Actions");
        Switch confirm = new Switch(this);
        confirm.setText("Ask \"should I ...?\" and wait for yes / no before every action (emergency STOP always runs at once)");
        confirm.setTextColor(TEXT);
        confirm.setChecked(prefs.confirmActions());
        confirm.setPadding(0, dp(12), 0, dp(12));
        confirm.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            @Override public void onCheckedChanged(CompoundButton c, boolean on) { prefs.putBool("confirm_actions", on); }
        });
        col.addView(confirm);
        freeCodes = field(col, "Codes that run without asking, comma separated (e.g. NOD_UP, WAVE_HAND). "
                + "Codes marked \"unrestricted\" on the instruction page are added automatically.", prefs.unrestrictedCodes(), false);
        freeCodes.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS);
        final TextView detected = new TextView(this);
        detected.setTextColor(MUTED);
        detected.setTextSize(12);
        detected.setPadding(0, dp(6), 0, 0);
        java.util.Set<String> onPage = Agent.parseUnrestricted(prefs.get("instr_cache", ""));
        detected.setText(onPage.isEmpty() ? "No codes are marked unrestricted on the instruction page yet."
                : "Marked unrestricted on the instruction page: " + android.text.TextUtils.join(", ", new java.util.TreeSet<String>(onPage)));
        col.addView(detected);

        section(col, "Voice");
        TextView vl = new TextView(this);
        vl.setText("Voice (\u2642 = male). Auto picks the best male voice on this phone.");
        vl.setTextColor(MUTED);
        vl.setTextSize(13);
        vl.setPadding(0, dp(8), 0, dp(4));
        col.addView(vl);
        voiceSpinner = new Spinner(this);
        voiceSpinner.setBackground(rounded(CARD, 12));
        voiceSpinner.setPadding(dp(8), dp(6), dp(8), dp(6));
        col.addView(voiceSpinner, new LinearLayout.LayoutParams(-1, dp(48)));
        pitch = field(col, "Pitch (lower = deeper; 0.85 is a natural male tone)", String.valueOf(prefs.pitch()), false);
        pitch.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL);
        minVolume = field(col, "Minimum speaking volume, % of max (0 = don't touch my volume)", String.valueOf(prefs.minVolumePercent()), false);
        minVolume.setInputType(InputType.TYPE_CLASS_NUMBER);
        leadIn = field(col, "Pause after the speaker is confirmed on, before speaking (ms)", String.valueOf(prefs.leadInMs()), false);
        leadIn.setInputType(InputType.TYPE_CLASS_NUMBER);
        Button preview = button("Preview voice");
        col.addView(preview);
        preview.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                saveAll();
                if (tts == null) return;
                String info = Voices.apply(tts, prefs);
                output.setText("Voice: " + info);
                tts.speak("Hey, I'm LUKU. Should I wave my hand?", TextToSpeech.QUEUE_FLUSH, null, "preview");
            }
        });
        loadVoices();

        section(col, "Speed");
        rate = field(col, "Speech rate (0.5 – 2.0)", String.valueOf(prefs.speechRate()), false);
        rate.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL);
        endpoint = field(col, "Reply after this much silence, ms (lower = faster, but may cut you off)", String.valueOf(prefs.endpointMs()), false);
        endpoint.setInputType(InputType.TYPE_CLASS_NUMBER);
        Switch chime = new Switch(this);
        chime.setText("Two soft beeps when I've heard you and start thinking");
        chime.setTextColor(TEXT);
        chime.setChecked(prefs.processingBeeps());
        chime.setPadding(0, dp(12), 0, dp(12));
        chime.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            @Override public void onCheckedChanged(CompoundButton c, boolean on) { prefs.putBool("processing_beeps", on); }
        });
        col.addView(chime);
        Switch mute = new Switch(this);
        mute.setText("Silence the listening beeps (mutes media sound while listening)");
        mute.setTextColor(TEXT);
        mute.setChecked(prefs.muteBeeps());
        mute.setPadding(0, dp(12), 0, dp(12));
        mute.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            @Override public void onCheckedChanged(CompoundButton c, boolean on) { prefs.putBool("mute_beeps", on); }
        });
        col.addView(mute);

        Button save = button("Save");
        col.addView(save);

        output = new TextView(this);
        output.setTextColor(MUTED);
        output.setTextSize(13);
        output.setTextIsSelectable(true);
        output.setPadding(dp(12), dp(12), dp(12), dp(12));
        output.setBackground(rounded(0xFF10172A, 12));
        LinearLayout.LayoutParams op = new LinearLayout.LayoutParams(-1, -2);
        op.topMargin = dp(14);
        col.addView(output, op);
        output.setText("Tip: restrict your API keys to this app in their consoles.");

        save.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                saveAll();
                Toast.makeText(SettingsActivity.this, "Saved", Toast.LENGTH_SHORT).show();
                finish();
            }
        });
        reload.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                saveAll();
                output.setText("Fetching " + prefs.instructionUrl() + " …");
                new Thread(new Runnable() {
                    @Override public void run() {
                        Agent a = new Agent(prefs);
                        String text = a.refreshInstruction(true);
                        final String msg = a.instructionStatus() + "\n\n" + (text == null ? "(nothing loaded)" : text);
                        main.post(new Runnable() { @Override public void run() { output.setText(msg); } });
                    }
                }).start();
            }
        });
        test.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                saveAll();
                output.setText("Testing Firebase…");
                new Thread(new Runnable() {
                    @Override public void run() {
                        final String msg = new FirebaseSync(SettingsActivity.this, prefs).test();
                        main.post(new Runnable() { @Override public void run() { output.setText(msg); showResult("Firebase", msg); } });
                    }
                }).start();
            }
        });

        setContentView(scroll);
    }

    /** Results pop up where the user is looking instead of at the bottom of this long page. */
    private void showResult(String title, String msg) {
        if (isFinishing()) return;
        output.setText(msg);
        new android.app.AlertDialog.Builder(this).setTitle(title).setMessage(msg)
                .setPositiveButton("OK", null).show();
    }

    private void saveAll() {
        int id = provider.getCheckedRadioButtonId();
        prefs.put("provider", id == 3 ? Prefs.PROVIDER_FAST : Prefs.PROVIDER_GEMINI);
        prefs.put("unrestricted_codes", freeCodes.getText().toString().trim().toUpperCase(java.util.Locale.US));

        prefs.put("gemini_key", geminiKey.getText().toString().trim());
        String m = model.getText().toString().trim();
        prefs.put("model", m.length() == 0 ? Prefs.DEFAULT_MODEL : m);
        prefs.put("fast_key", fastKey.getText().toString().trim());
        prefs.put("fast_model", fastModel.getText().toString().trim());
        prefs.put("fast_base", fastBase.getText().toString().trim());
        prefs.put("instr_url", instrUrl.getText().toString().trim());
        String oldKey = prefs.firebaseApiKey();
        String newKey = fbKey.getText().toString().trim();
        if (!oldKey.equals(newKey)) prefs.put("fb_id_token", "");
        prefs.put("fb_api_key", newKey);
        prefs.put("fb_db_url", fbUrl.getText().toString().trim());
        try {
            float r = Float.parseFloat(rate.getText().toString().trim());
            prefs.put("speech_rate", String.valueOf(Math.max(0.5f, Math.min(2.0f, r))));
        } catch (Exception ignored) { }
        try {
            float pv = Float.parseFloat(pitch.getText().toString().trim());
            prefs.put("voice_pitch", String.valueOf(Math.max(0.5f, Math.min(1.5f, pv))));
        } catch (Exception ignored) { }
        try {
            int mv = Integer.parseInt(minVolume.getText().toString().trim());
            prefs.put("min_volume", String.valueOf(Math.max(0, Math.min(100, mv))));
        } catch (Exception ignored) { }
        try {
            int li = Integer.parseInt(leadIn.getText().toString().trim());
            prefs.put("lead_in_ms", String.valueOf(Math.max(0, Math.min(1000, li))));
        } catch (Exception ignored) { }
        int vi = voiceSpinner.getSelectedItemPosition();
        if (voiceNames.size() > 1 && vi >= 0 && vi < voiceNames.size()) prefs.put("voice_name", voiceNames.get(vi));
        try {
            int e = Integer.parseInt(endpoint.getText().toString().trim());
            prefs.put("endpoint_ms", String.valueOf(Math.max(300, Math.min(3000, e))));
        } catch (Exception ignored) { }
        if (id == 3 && prefs.fastKey().length() == 0) {
            Toast.makeText(this, "No fast-engine key yet — using Gemini until you add one", Toast.LENGTH_LONG).show();
        }
    }

    private void loadVoices() {
        voiceNames.clear();
        voiceNames.add("");
        final List<String> labels = new ArrayList<String>();
        labels.add("Auto \u2014 best male voice");
        setSpinner(labels);
        tts = new TextToSpeech(this, new TextToSpeech.OnInitListener() {
            @Override public void onInit(int status) {
                if (status != TextToSpeech.SUCCESS) return;
                final List<String> names = new ArrayList<String>();
                final List<String> lbls = new ArrayList<String>();
                names.add("");
                lbls.add("Auto \u2014 best male voice");
                for (Voice v : Voices.englishVoices(tts)) {
                    names.add(v.getName());
                    lbls.add(Voices.label(v));
                }
                main.post(new Runnable() {
                    @Override public void run() {
                        voiceNames.clear();
                        voiceNames.addAll(names);
                        setSpinner(lbls);
                        int sel = voiceNames.indexOf(prefs.voiceName());
                        voiceSpinner.setSelection(sel < 0 ? 0 : sel);
                    }
                });
            }
        });
    }

    private void setSpinner(List<String> labels) {
        ArrayAdapter<String> a = new ArrayAdapter<String>(this, android.R.layout.simple_spinner_item, labels) {
            @Override public View getView(int pos, View convert, android.view.ViewGroup parent) {
                View v = super.getView(pos, convert, parent);
                if (v instanceof TextView) ((TextView) v).setTextColor(TEXT);
                return v;
            }
        };
        a.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        voiceSpinner.setAdapter(a);
    }

    @Override protected void onDestroy() {
        super.onDestroy();
        if (tts != null) { tts.stop(); tts.shutdown(); }
    }

    private RadioButton radio(int id, String text) {
        RadioButton r = new RadioButton(this);
        r.setId(id);
        r.setText(text);
        r.setTextColor(TEXT);
        r.setTextSize(14);
        r.setPadding(dp(4), dp(6), 0, dp(6));
        return r;
    }

    private void section(LinearLayout col, String name) {
        TextView t = new TextView(this);
        t.setText(name.toUpperCase());
        t.setTextColor(ACCENT);
        t.setTextSize(12);
        t.setLetterSpacing(0.08f);
        t.setPadding(0, dp(22), 0, dp(4));
        col.addView(t);
    }

    private EditText field(LinearLayout col, String label, String value, boolean secret) {
        TextView l = new TextView(this);
        l.setText(label);
        l.setTextColor(MUTED);
        l.setTextSize(13);
        l.setPadding(0, dp(8), 0, dp(4));
        col.addView(l);
        EditText e = new EditText(this);
        e.setText(value);
        e.setTextColor(TEXT);
        e.setTextSize(15);
        e.setSingleLine(true);
        e.setBackground(rounded(CARD, 12));
        e.setPadding(dp(12), dp(10), dp(12), dp(10));
        if (secret) e.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD);
        else e.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        col.addView(e, new LinearLayout.LayoutParams(-1, -2));
        return e;
    }

    private Button button(String text) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setTextColor(BG);
        b.setBackground(rounded(ACCENT, 12));
        b.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, dp(46));
        p.topMargin = dp(12);
        b.setLayoutParams(p);
        return b;
    }

    private GradientDrawable rounded(int color, float r) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(color);
        g.setCornerRadius(dp(r));
        return g;
    }
}
