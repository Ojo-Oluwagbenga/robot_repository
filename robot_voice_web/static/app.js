/**
 * Nova Robot Brain - Real-Time Voice Streaming Client
 * Bidirectional WebSocket, Web Audio API VAD, 60fps Holographic Visualizer,
 * and Sentence-Level Fast Neural TTS playback.
 */

// Global App State
const state = {
  mode: "hands-free", // "hands-free" or "push-to-talk"
  status: "idle",     // "idle", "listening", "transcribing", "thinking", "speaking"
  ws: null,
  wsConnected: false,
  
  // Audio Context & VAD
  audioCtx: null,
  analyser: null,
  mediaStream: null,
  scriptProcessor: null,
  isRecording: false,
  
  // VAD Parameters
  vadThreshold: 22,
  silenceTimeoutMs: 1300,
  silenceTimer: null,
  audioChunks: [],
  preSpeechBuffer: [],
  speechStarted: false,
  sampleRate: 16000,
  
  // Device Speech-to-Text (Client Microphone STT)
  recognition: null,
  hasDeviceStt: false,
  sttListening: false,
  recognitionLang: "en-US",
  
  // Device Text-to-Speech (Client Speaker TTS)
  voices: [],
  selectedVoice: null,
  speechRate: 1.05,
  speechPitch: 1.0,
  speechVolume: 1.0,
  isMuted: false,
  speechQueue: [],
  isSpeaking: false,
  activeSentenceBuffer: "",
  deviceTtsPrimed: false,
  
  // Stats & Transcripts
  turnsCount: 0,
  currentAssistantBubble: null,
  streamActive: false,
};

// DOM Elements
const DOM = {
  connectionBadge: document.getElementById("connectionBadge"),
  connectionText: document.getElementById("connectionText"),
  modeHandsFree: document.getElementById("modeHandsFree"),
  modePushToTalk: document.getElementById("modePushToTalk"),
  networkIpPill: document.getElementById("networkIpPill"),
  networkIpText: document.getElementById("networkIpText"),
  toggleDrawerBtn: document.getElementById("toggleDrawerBtn"),
  unreadBadge: document.getElementById("unreadBadge"),
  settingsBtn: document.getElementById("settingsBtn"),
  
  stateIndicator: document.getElementById("stateIndicator"),
  stateLabel: document.getElementById("stateLabel"),
  visualizerCanvas: document.getElementById("visualizerCanvas"),
  micOrbBtn: document.getElementById("micOrbBtn"),
  micIcon: document.getElementById("micIcon"),
  speakingWaves: document.getElementById("speakingWaves"),
  
  streamSpeaker: document.getElementById("streamSpeaker"),
  audioMeterText: document.getElementById("audioMeterText"),
  audioMeterBar: document.getElementById("audioMeterBar"),
  streamText: document.getElementById("streamText"),
  quickPrompts: document.getElementById("quickPrompts"),
  
  muteBtn: document.getElementById("muteBtn"),
  muteLabel: document.getElementById("muteLabel"),
  interruptBtn: document.getElementById("interruptBtn"),
  clearChatBtn: document.getElementById("clearChatBtn"),
  
  transcriptDrawer: document.getElementById("transcriptDrawer"),
  closeDrawerBtn: document.getElementById("closeDrawerBtn"),
  chatMessageList: document.getElementById("chatMessageList"),
  turnCounter: document.getElementById("turnCounter"),
  textChatForm: document.getElementById("textChatForm"),
  textChatInput: document.getElementById("textChatInput"),
  
  settingsModal: document.getElementById("settingsModal"),
  closeSettingsBtn: document.getElementById("closeSettingsBtn"),
  saveSettingsBtn: document.getElementById("saveSettingsBtn"),
  testVoiceBtn: document.getElementById("testVoiceBtn"),
  sttEngineBadge: document.getElementById("sttEngineBadge"),
  sttEngineText: document.getElementById("sttEngineText"),
  voiceSelect: document.getElementById("voiceSelect"),
  speechVolume: document.getElementById("speechVolume"),
  speechVolumeVal: document.getElementById("speechVolumeVal"),
  vadThreshold: document.getElementById("vadThreshold"),
  vadThresholdVal: document.getElementById("vadThresholdVal"),
  silenceTimeout: document.getElementById("silenceTimeout"),
  silenceTimeoutVal: document.getElementById("silenceTimeoutVal"),
  speechRate: document.getElementById("speechRate"),
  speechRateVal: document.getElementById("speechRateVal"),
};

// ==============================================================================
// 1. INITIALIZATION & NETWORK SETUP
// ==============================================================================

async function initApp() {
  checkHttpsNotice();
  setupCanvas();
  setupEventListeners();
  loadSpeechVoices();
  initDeviceSpeechRecognition();
  await fetchServerStatus();
  connectWebSocket();
  startVisualizerLoop();
}

function checkHttpsNotice() {
  const isHttp = window.location.protocol === "http:";
  const isNotLocal = window.location.hostname !== "localhost" && window.location.hostname !== "127.0.0.1";
  const banner = document.getElementById("httpsBanner");
  const link = document.getElementById("httpsSwitchLink");
  if (isHttp && isNotLocal && banner && link) {
    const secureUrl = `https://${window.location.hostname}:8443`;
    link.href = secureUrl;
    banner.classList.remove("hidden");
  }
}

async function fetchServerStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    if (data.network_ips && data.network_ips.length > 0) {
      // Pick external Wi-Fi IP if present
      const netIp = data.network_ips.find(ip => !ip.startsWith("127.")) || data.network_ips[0];
      DOM.networkIpText.textContent = `${netIp}:8000`;
    }
  } catch (e) {
    console.warn("Could not fetch server status:", e);
  }
}

// ==============================================================================
// 2. WEBSOCKET REAL-TIME CONNECTION
// ==============================================================================

function connectWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws/voice`;
  
  updateConnectionStatus("connecting", "CONNECTING");
  
  try {
    state.ws = new WebSocket(wsUrl);
    state.ws.binaryType = "arraybuffer";

    state.ws.onopen = () => {
      state.wsConnected = true;
      updateConnectionStatus("online", "ONLINE");
      console.log("[WebSocket] Voice streaming tunnel established.");
      if (state.pendingWsMessages && state.pendingWsMessages.length > 0) {
        while (state.pendingWsMessages.length > 0) {
          state.ws.send(state.pendingWsMessages.shift());
        }
      }
    };

    state.ws.onmessage = (event) => {
      handleServerMessage(event.data);
    };

    state.ws.onclose = () => {
      state.wsConnected = false;
      updateConnectionStatus("offline", "DISCONNECTED");
      console.log("[WebSocket] Connection lost. Reconnecting in 2.5s...");
      setTimeout(connectWebSocket, 2500);
    };

    state.ws.onerror = (err) => {
      console.error("[WebSocket Error]", err);
    };
  } catch (e) {
    console.error("WebSocket initialization error:", e);
  }
}

function updateConnectionStatus(statusClass, label) {
  DOM.connectionBadge.className = `badge badge-${statusClass}`;
  DOM.connectionText.textContent = label;
}

// ==============================================================================
// 3. SERVER MESSAGE ROUTING & FAST TTS STREAMING
// ==============================================================================

function handleServerMessage(data) {
  try {
    const msg = JSON.parse(data);

    switch (msg.type) {
      case "status":
        updateAppStatus(msg.state, msg.message);
        break;

      case "transcript":
        // User's transcribed speech arrived
        displayUserSpeech(msg.text);
        updateAppStatus("thinking", "Nova is thinking...");
        break;

      case "token":
        // Streaming token from Gemini
        handleIncomingToken(msg.token);
        break;

      case "complete":
        // Streaming finished
        finishAssistantTurn(msg.text);
        break;

      case "error":
        console.error("[Server Error]", msg.message);
        updateAppStatus("idle", `Error: ${msg.message}`);
        DOM.streamText.textContent = `Error: ${msg.message}`;
        break;
    }
  } catch (e) {
    console.error("Message parsing error:", e, data);
  }
}

let lastUserMessageText = "";
let lastUserMessageTime = 0;

function displayUserSpeech(text) {
  const clean = (text || "").trim();
  if (!clean) return;
  
  // Prevent duplicate bubble if echoed back by server within 3 seconds
  if (lastUserMessageText === clean && (Date.now() - lastUserMessageTime) < 3000) {
    return;
  }
  lastUserMessageText = clean;
  lastUserMessageTime = Date.now();

  DOM.streamSpeaker.textContent = "YOU";
  DOM.streamSpeaker.className = "speaker-tag speaker-user";
  DOM.streamText.textContent = `"${clean}"`;
  
  // Add to conversation log
  appendChatMessage("user", clean);
  
  // Reset assistant active sentence buffer and mark assistant stream as pending
  state.activeSentenceBuffer = "";
  state.streamActive = false;
}

function handleIncomingToken(token) {
  if (!state.streamActive) {
    state.streamActive = true;
    DOM.streamSpeaker.textContent = "NOVA";
    DOM.streamSpeaker.className = "speaker-tag speaker-nova";
    DOM.streamText.textContent = "";
  }

  // Append token to UI stream
  DOM.streamText.textContent += token;
  updateActiveChatBubble(DOM.streamText.textContent);

  // Feed token into Sentence-by-Sentence TTS Engine
  state.activeSentenceBuffer += token;

  // Split at sentence boundaries: period, exclamation, question mark, or newline
  const match = state.activeSentenceBuffer.match(/([^\.\!\?\n]+[\.\!\?]+|\n+)/);
  if (match) {
    const completeSentence = match[0].trim();
    state.activeSentenceBuffer = state.activeSentenceBuffer.substring(match[0].length);

    if (completeSentence && !state.isMuted) {
      queueSpeechSentence(completeSentence);
    }
  }
}

function finishAssistantTurn(fullText) {
  state.streamActive = false;
  
  // Flush any remaining text in sentence buffer
  const remaining = state.activeSentenceBuffer.trim();
  if (remaining && !state.isMuted) {
    queueSpeechSentence(remaining);
  }
  state.activeSentenceBuffer = "";

  // If no speech is playing, return to listening (in hands-free mode)
  if (!state.isSpeaking && state.speechQueue.length === 0) {
    onSpeechPlaybackComplete();
  }
}

// ==============================================================================
// 4. DEVICE TEXT-TO-SPEECH (CLIENT SPEAKER) ENGINE
// ==============================================================================

let ttsWatchdog = null;

function loadSpeechVoices() {
  if (!("speechSynthesis" in window)) {
    console.warn("[Device TTS] Speech Synthesis not supported in this browser.");
    DOM.voiceSelect.innerHTML = "<option value=''>Speech Synthesis not supported</option>";
    return;
  }

  function updateVoices() {
    state.voices = window.speechSynthesis.getVoices();
    DOM.voiceSelect.innerHTML = "";

    // Prefer natural English voices (Google, Natural, Siri, Samantha, Ryan, Jenny)
    const englishVoices = state.voices.filter(v => v.lang && v.lang.toLowerCase().startsWith("en"));
    const voiceList = englishVoices.length > 0 ? englishVoices : state.voices;

    let selectedIdx = 0;
    voiceList.forEach((voice, i) => {
      const opt = document.createElement("option");
      opt.value = voice.name;
      const cleanName = voice.name.replace(/Microsoft |Google |Apple /gi, "");
      opt.textContent = `${cleanName} [${voice.lang}]`;
      
      const lower = voice.name.toLowerCase();
      if (lower.includes("natural") || lower.includes("siri") || lower.includes("google") || lower.includes("samantha") || lower.includes("jenny")) {
        selectedIdx = i;
      }
      DOM.voiceSelect.appendChild(opt);
    });

    if (voiceList.length > 0) {
      DOM.voiceSelect.selectedIndex = selectedIdx;
      state.selectedVoice = voiceList[selectedIdx];
    }
  }

  updateVoices();
  if (window.speechSynthesis.onvoiceschanged !== undefined) {
    window.speechSynthesis.onvoiceschanged = updateVoices;
  }
}

function queueSpeechSentence(text) {
  // Strip markdown symbols, asterisks, brackets, quotes, etc., for natural spoken speech
  const clean = text
    .replace(/[*_#`~\[\]\(\)\{\}\<\>]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!clean) return;

  state.speechQueue.push(clean);
  if (!state.isSpeaking) {
    playNextSentence();
  }
}

function playNextSentence() {
  if (state.speechQueue.length === 0) {
    state.isSpeaking = false;
    onSpeechPlaybackComplete();
    return;
  }

  if (state.isMuted) {
    state.speechQueue = [];
    state.isSpeaking = false;
    onSpeechPlaybackComplete();
    return;
  }

  const sentence = state.speechQueue.shift();
  if (!sentence || !sentence.trim()) {
    playNextSentence();
    return;
  }

  state.isSpeaking = true;
  updateAppStatus("speaking", "Nova is speaking...");
  DOM.micOrbBtn.classList.add("speaking-active");
  DOM.micIcon.classList.add("hidden");
  DOM.speakingWaves.classList.remove("hidden");

  // Device Web Speech API
  if (!("speechSynthesis" in window)) {
    console.warn("[Device TTS] Speech synthesis is not supported on this browser.");
    state.isSpeaking = false;
    onSpeechPlaybackComplete();
    return;
  }

  // Ensure synthesizer is not in a paused state (Chrome / iOS bug fix)
  try {
    window.speechSynthesis.resume();
  } catch (e) {}

  const utterance = new SpeechSynthesisUtterance(sentence);
  window._activeUtterance = utterance; // Retain global reference to avoid Chromium/WebKit GC bug

  if (state.selectedVoice) {
    utterance.voice = state.selectedVoice;
    utterance.lang = state.selectedVoice.lang || "en-US";
  } else {
    utterance.lang = "en-US";
  }

  utterance.rate = state.speechRate || 1.05;
  utterance.pitch = state.speechPitch || 1.0;
  utterance.volume = state.speechVolume !== undefined ? state.speechVolume : 1.0;

  let hasEnded = false;
  const finishSentence = () => {
    if (hasEnded) return;
    hasEnded = true;
    if (ttsWatchdog) {
      clearTimeout(ttsWatchdog);
      ttsWatchdog = null;
    }
    window._activeUtterance = null;
    playNextSentence();
  };

  utterance.onend = finishSentence;
  utterance.onerror = (e) => {
    console.warn("[Device TTS] Utterance error:", e);
    finishSentence();
  };

  // Watchdog safety timer: if browser drops onend, automatically advance queue
  const words = sentence.trim().split(/\s+/).length;
  const maxWaitMs = Math.max(3000, (words / (1.5 * (state.speechRate || 1.0))) * 1000 + 4000);
  ttsWatchdog = setTimeout(() => {
    if (!hasEnded && state.isSpeaking) {
      console.warn("[Device TTS] Watchdog advancing stuck sentence:", sentence);
      finishSentence();
    }
  }, maxWaitMs);

  try {
    window.speechSynthesis.speak(utterance);
    window.speechSynthesis.resume();
  } catch (err) {
    console.error("[Device TTS] Speak exception:", err);
    finishSentence();
  }
}

function cancelSpeechOutput() {
  if (ttsWatchdog) {
    clearTimeout(ttsWatchdog);
    ttsWatchdog = null;
  }
  if ("speechSynthesis" in window) {
    window.speechSynthesis.cancel();
  }
  window._activeUtterance = null;
  state.speechQueue = [];
  state.isSpeaking = false;
  DOM.micOrbBtn.classList.remove("speaking-active");
  DOM.micIcon.classList.remove("hidden");
  DOM.speakingWaves.classList.add("hidden");
}

function testDeviceSpeech() {
  primeDeviceTTS();
  cancelSpeechOutput();
  const testText = "Hello! This is your device text-to-speech speaking for Nova.";
  queueSpeechSentence(testText);
}

function onSpeechPlaybackComplete() {
  DOM.micOrbBtn.classList.remove("speaking-active");
  DOM.micIcon.classList.remove("hidden");
  DOM.speakingWaves.classList.add("hidden");

  // In Hands-Free mode: re-arm device speech recognition after speech finishes
  if (state.mode === "hands-free" && state.isRecording) {
    setTimeout(() => {
      if (!state.isSpeaking) {
        if (state.hasDeviceStt) {
          startDeviceSpeechRecognition();
        }
        updateAppStatus("listening", "Listening... Speak now!");
      }
    }, 350);
  } else {
    updateAppStatus("idle", "Tap mic to speak");
  }
}

// ==============================================================================
// 5. DEVICE SPEECH-TO-TEXT (CLIENT MICROPHONE STT)
// ==============================================================================

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let activeLiveText = "";

function escapeHtml(str) {
  if (!str) return "";
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function handleSpeechResult(interimTranscript, finalTranscript) {
  const currentSpeech = (finalTranscript + " " + interimTranscript).trim();
  if (!currentSpeech) return;

  activeLiveText = currentSpeech;

  // Barge-in: if user starts speaking while Nova is speaking, stop speech output immediately
  if (state.isSpeaking && activeLiveText.length > 2) {
    console.log("[Device STT] User speech interrupted assistant voice output.");
    cancelSpeechOutput();
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ type: "interrupt" }));
    }
  }

  // SHOW TEXT AS USER IS SAYING IT (Word-by-word real-time display with glowing cursor)
  DOM.streamSpeaker.textContent = "YOU (Speaking...)";
  DOM.streamSpeaker.className = "speaker-tag speaker-user speaking-pulse";
  DOM.streamText.innerHTML = `"${escapeHtml(activeLiveText)}<span class=\"live-cursor\"></span>"`;
  DOM.stateIndicator.className = "state-pill state-listening";
  DOM.stateLabel.textContent = "LISTENING TO YOU...";

  // Silence detection: when user finishes saying it (pauses ~850ms), send to Gemini!
  clearTimeout(state.sttSilenceTimer);
  state.sttSilenceTimer = setTimeout(() => {
    if (activeLiveText && !state.isSpeaking) {
      const userText = activeLiveText;
      activeLiveText = "";
      console.log("[Device STT] User finished speaking. Sending to Gemini:", userText);
      pauseDeviceSpeechRecognition();
      sendTextMessage(userText);
    }
  }, 850);
}

function initDeviceSpeechRecognition() {
  if (!SpeechRecognition) {
    console.warn("[Device STT] webkitSpeechRecognition not supported in this browser. Falling back to server VAD.");
    state.hasDeviceStt = false;
    if (DOM.sttEngineText) {
      DOM.sttEngineText.textContent = "Server-Side VAD Transcription (Browser Fallback)";
    }
    return false;
  }

  try {
    state.hasDeviceStt = true;
    if (DOM.sttEngineText) {
      DOM.sttEngineText.textContent = "Device Native Speech Recognition (Phone Microphone)";
    }
    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = state.recognitionLang || "en-US";
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      state.sttListening = true;
      activeLiveText = "";
      console.log("[Device STT] Phone/Device microphone Speech-to-Text active.");
    };

    recognition.onresult = (event) => {
      let interimTranscript = "";
      let finalTranscript = "";

      for (let i = 0; i < event.results.length; i++) {
        const res = event.results[i];
        const text = res[0].transcript;
        if (res.isFinal) {
          finalTranscript += text + " ";
        } else {
          interimTranscript += text;
        }
      }

      handleSpeechResult(interimTranscript, finalTranscript);
    };

    recognition.onerror = (e) => {
      if (e.error === "no-speech") {
        return; // Continuous mode normal idle pause
      }
      console.warn("[Device STT] Recognition error:", e.error);
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        alert("Microphone permission was denied. Please allow microphone access in your browser settings.");
        stopAudioCapture();
      }
    };

    recognition.onend = () => {
      state.sttListening = false;
      clearTimeout(state.sttSilenceTimer);

      // If browser closed utterance boundary and user spoke, send text immediately
      if (activeLiveText && !state.isSpeaking) {
        const userText = activeLiveText;
        activeLiveText = "";
        console.log("[Device STT] Utterance boundary reached. Sending to Gemini:", userText);
        pauseDeviceSpeechRecognition();
        sendTextMessage(userText);
        return;
      }

      // In hands-free mode, keep recognition open ONLY while in listening state (not while Nova is thinking or speaking!)
      if (state.mode === "hands-free" && state.isRecording && state.status === "listening" && !state.isSpeaking) {
        setTimeout(() => {
          if (state.isRecording && state.status === "listening" && !state.sttListening && !state.isSpeaking) {
            try {
              recognition.start();
            } catch (err) {}
          }
        }, 150);
      }
    };

    state.recognition = recognition;
    console.log("[Device STT] Device SpeechRecognition engine initialized successfully.");
    return true;
  } catch (err) {
    console.error("[Device STT] Initialization failed:", err);
    state.hasDeviceStt = false;
    return false;
  }
}

function startDeviceSpeechRecognition() {
  if (!state.recognition) {
    initDeviceSpeechRecognition();
  }
  if (state.recognition && !state.sttListening) {
    try {
      state.recognition.start();
      state.sttListening = true;
    } catch (e) {
      // Already active
    }
  }
}

function pauseDeviceSpeechRecognition() {
  if (state.recognition && state.sttListening) {
    try {
      state.recognition.stop();
      state.sttListening = false;
    } catch (e) {}
  }
}

function stopDeviceSpeechRecognition() {
  pauseDeviceSpeechRecognition();
}

// ==============================================================================
// 6. WEB AUDIO API & VISUALIZER CAPTURE
// ==============================================================================

async function startAudioCapture() {
  try {
    state.isRecording = true;
    updateAppStatus("listening", "Listening... Speak now!");
    DOM.micOrbBtn.classList.add("active-recording");

    // 1. Activate Speech Recognition immediately
    if (state.hasDeviceStt) {
      startDeviceSpeechRecognition();
    }

    // 2. Best-effort Web Audio API for holographic visualizer and volume meter
    try {
      if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
        state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        if (state.audioCtx.state === "suspended") {
          await state.audioCtx.resume();
        }
        state.sampleRate = state.audioCtx.sampleRate;

        state.mediaStream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          }
        });

        const source = state.audioCtx.createMediaStreamSource(state.mediaStream);
        state.analyser = state.audioCtx.createAnalyser();
        state.analyser.fftSize = 512;
        state.analyser.smoothingTimeConstant = 0.8;
        source.connect(state.analyser);

        const bufferSize = 2048;
        state.scriptProcessor = state.audioCtx.createScriptProcessor(bufferSize, 1, 1);
        state.scriptProcessor.onaudioprocess = processAudioFrame;
        
        source.connect(state.scriptProcessor);
        state.scriptProcessor.connect(state.audioCtx.destination);
      }
    } catch (mediaErr) {
      console.warn("[AudioContext] Visualizer audio stream warning:", mediaErr);
    }

  } catch (err) {
    console.error("Audio capture start error:", err);
    stopAudioCapture();
  }
}

function stopAudioCapture() {
  state.isRecording = false;
  state.speechStarted = false;
  clearTimeout(state.silenceTimer);
  clearTimeout(state.sttFinalTimer);

  if (state.hasDeviceStt) {
    stopDeviceSpeechRecognition();
  }

  if (state.scriptProcessor) {
    state.scriptProcessor.disconnect();
    state.scriptProcessor = null;
  }
  if (state.mediaStream) {
    state.mediaStream.getTracks().forEach(t => t.stop());
    state.mediaStream = null;
  }
  if (state.audioCtx && state.audioCtx.state !== "closed") {
    state.audioCtx.close();
    state.audioCtx = null;
  }

  DOM.micOrbBtn.classList.remove("active-recording");
  updateAppStatus("idle", "Tap mic to speak");
  DOM.audioMeterBar.style.width = "0%";
}

function primeDeviceTTS() {
  if (!("speechSynthesis" in window)) return;
  try {
    window.speechSynthesis.resume();
    if (!state.deviceTtsPrimed) {
      const silent = new SpeechSynthesisUtterance(" ");
      silent.volume = 0.01;
      silent.rate = 2.0;
      window.speechSynthesis.speak(silent);
      state.deviceTtsPrimed = true;
      console.log("[Device TTS] Speech synthesis primed on user gesture.");
    }
  } catch (e) {
    console.warn("[Device TTS] Primer notice:", e);
  }
}

function unlockMobileAudioSession() {
  primeDeviceTTS();
  if (!state.audioUnlocked) {
    state.audioUnlocked = true;
    try {
      // Prime Web Audio API destination for loud speaker / BT routing
      if (state.audioCtx && state.audioCtx.state === "suspended") {
        state.audioCtx.resume();
      }
    } catch (e) {
      console.warn("Audio unlock notice:", e);
    }
  }
}

function processAudioFrame(e) {
  if (!state.isRecording) return;

  const inputData = e.inputBuffer.getChannelData(0);

  // Calculate RMS energy (volume level)
  let sum = 0;
  for (let i = 0; i < inputData.length; i++) {
    sum += inputData[i] * inputData[i];
  }
  const rms = Math.sqrt(sum / inputData.length) * 1000;

  // Update meter UI
  const meterPercent = Math.min(100, Math.round((rms / 80) * 100));
  DOM.audioMeterBar.style.width = `${meterPercent}%`;
  DOM.audioMeterText.textContent = `Input: ${Math.round(rms)} dB`;

  const frameCopy = new Float32Array(inputData);

  // BARGE-IN INTERRUPTION: If Nova is speaking and user speaks over her
  if (state.isSpeaking) {
    // Interruption threshold with acoustic echo margin
    if (rms > state.vadThreshold * 1.3) {
      console.log("[Barge-in] User interrupted Nova speech output!");
      cancelSpeechOutput();
      
      // Notify server to cancel active LLM streaming task
      if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ type: "interrupt" }));
      }

      state.isSpeaking = false;
      state.speechStarted = true;
      updateAppStatus("listening", "Listening to you...");

      state.audioChunks = [frameCopy];
      clearTimeout(state.silenceTimer);
      state.silenceTimer = setTimeout(() => {
        onSpeechFinished();
      }, state.silenceTimeoutMs);
      return;
    } else {
      // While speaking, discard ambient echo frames below threshold
      return;
    }
  }

  // If device STT is active, SpeechRecognition directly transcribes and sends text.
  // Skip raw WAV buffer accumulation to save device battery and CPU.
  if (state.hasDeviceStt) {
    return;
  }

  // Voice Activity Detection (VAD) Logic (Fallback for browsers without webkitSpeechRecognition)
  const isSpeech = rms > state.vadThreshold;

  if (isSpeech) {
    // User is actively speaking
    if (!state.speechStarted) {
      state.speechStarted = true;
      updateAppStatus("listening", "Listening to you...");

      // Prepend rolling pre-speech buffer so initial consonants are intact
      state.audioChunks = [...state.preSpeechBuffer];
      state.preSpeechBuffer = [];
    }

    state.audioChunks.push(frameCopy);
    clearTimeout(state.silenceTimer);

    // In Hands-Free mode, start silence countdown
    if (state.mode === "hands-free") {
      state.silenceTimer = setTimeout(() => {
        onSpeechFinished();
      }, state.silenceTimeoutMs);
    }

  } else {
    // Silence / Ambient noise
    if (state.speechStarted) {
      state.audioChunks.push(frameCopy);
    } else {
      // Keep ~350ms of rolling pre-speech audio
      state.preSpeechBuffer.push(frameCopy);
      if (state.preSpeechBuffer.length > 5) {
        state.preSpeechBuffer.shift();
      }
    }
  }
}

function onSpeechFinished() {
  if (!state.speechStarted || state.audioChunks.length === 0) {
    state.speechStarted = false;
    return;
  }

  state.speechStarted = false;
  clearTimeout(state.silenceTimer);

  updateAppStatus("transcribing", "Transcribing speech with Gemini...");

  // Concatenate Float32 PCM chunks and encode to 16kHz Mono WAV
  const totalLength = state.audioChunks.reduce((acc, chunk) => acc + chunk.length, 0);
  const fullPcm = new Float32Array(totalLength);
  let offset = 0;
  for (const chunk of state.audioChunks) {
    fullPcm.set(chunk, offset);
    offset += chunk.length;
  }
  state.audioChunks = [];

  // Encode to 16-bit PCM WAV
  const wavBuffer = encodeWav(fullPcm, state.sampleRate);

  // Send binary audio directly over WebSocket
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(wavBuffer);
  } else {
    console.warn("WebSocket not open. Falling back to REST transcribe...");
    uploadWavRest(wavBuffer);
  }
}

// REST fallback if WebSocket disconnects
async function uploadWavRest(wavBuffer) {
  try {
    const blob = new Blob([wavBuffer], { type: "audio/wav" });
    const formData = new FormData();
    formData.append("file", blob, "voice.wav");

    const res = await fetch("/api/transcribe", { method: "POST", body: formData });
    const data = await res.json();
    if (data.transcript) {
      displayUserSpeech(data.transcript);
    }
  } catch (e) {
    console.error("REST transcribe failed:", e);
  }
}

// Convert Float32 samples to 16-bit Mono WAV format
function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  // RIFF header
  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(view, 8, 'WAVE');
  // fmt chunk
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM format
  view.setUint16(22, 1, true); // 1 Channel (Mono)
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // Byte rate
  view.setUint16(32, 2, true); // Block align
  view.setUint16(34, 16, true); // Bits per sample
  // data chunk
  writeString(view, 36, 'data');
  view.setUint32(40, samples.length * 2, true);

  // Write 16-bit PCM samples with clipping protection
  let index = 44;
  for (let i = 0; i < samples.length; i++) {
    let s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(index, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    index += 2;
  }

  return buffer;
}

function writeString(view, offset, string) {
  for (let i = 0; i < string.length; i++) {
    view.setUint8(offset + i, string.charCodeAt(i));
  }
}

// ==============================================================================
// 6. HOLOGRAPHIC 60FPS CANVAS VISUALIZER
// ==============================================================================

let canvasCtx = null;
let animAngle = 0;

function setupCanvas() {
  const canvas = DOM.visualizerCanvas;
  canvasCtx = canvas.getContext("2d");
  resizeCanvas();
  window.addEventListener("resize", resizeCanvas);
}

function resizeCanvas() {
  const canvas = DOM.visualizerCanvas;
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width * window.devicePixelRatio;
  canvas.height = rect.height * window.devicePixelRatio;
  canvasCtx.scale(window.devicePixelRatio, window.devicePixelRatio);
}

function startVisualizerLoop() {
  requestAnimationFrame(renderVisualizer);
}

function renderVisualizer() {
  const canvas = DOM.visualizerCanvas;
  const rect = canvas.parentElement.getBoundingClientRect();
  const width = rect.width;
  const height = rect.height;
  const centerX = width / 2;
  const centerY = height / 2;
  const radius = width * 0.32;

  canvasCtx.clearRect(0, 0, width, height);

  animAngle += 0.02;

  let freqData = new Uint8Array(64);
  if (state.analyser && state.isRecording) {
    state.analyser.getByteFrequencyData(freqData);
  }

  // Draw concentric glow rings
  const ringCount = 3;
  for (let i = 0; i < ringCount; i++) {
    const ringRadius = radius + i * 16 + Math.sin(animAngle + i) * 6;
    canvasCtx.beginPath();
    canvasCtx.arc(centerX, centerY, Math.max(10, ringRadius), 0, Math.PI * 2);

    let strokeColor = "rgba(0, 240, 255, 0.12)";
    if (state.status === "listening") {
      strokeColor = "rgba(0, 240, 255, 0.35)";
    } else if (state.status === "thinking") {
      strokeColor = "rgba(139, 92, 246, 0.4)";
    } else if (state.status === "speaking") {
      strokeColor = "rgba(16, 185, 129, 0.35)";
    }

    canvasCtx.strokeStyle = strokeColor;
    canvasCtx.lineWidth = 1.5;
    canvasCtx.stroke();
  }

  // Draw radial frequency spectrum bars around the core
  const bars = 48;
  const step = (Math.PI * 2) / bars;

  for (let i = 0; i < bars; i++) {
    const angle = i * step + animAngle * 0.2;
    const freqVal = freqData[i % freqData.length] || 0;
    
    // Scale bar height based on state & audio energy
    let barHeight = 8;
    if (state.status === "listening") {
      barHeight = 10 + (freqVal / 255) * 45;
    } else if (state.status === "speaking") {
      barHeight = 12 + Math.sin(animAngle * 4 + i) * 16 + (Math.random() * 10);
    } else if (state.status === "thinking") {
      barHeight = 10 + Math.sin(animAngle * 6 + i * 2) * 8;
    }

    const x1 = centerX + Math.cos(angle) * (radius - 6);
    const y1 = centerY + Math.sin(angle) * (radius - 6);
    const x2 = centerX + Math.cos(angle) * (radius + barHeight);
    const y2 = centerY + Math.sin(angle) * (radius + barHeight);

    canvasCtx.beginPath();
    canvasCtx.moveTo(x1, y1);
    canvasCtx.lineTo(x2, y2);

    // Color gradient based on state
    if (state.status === "listening") {
      canvasCtx.strokeStyle = `rgba(0, 240, 255, ${0.4 + (barHeight / 60)})`;
    } else if (state.status === "speaking") {
      canvasCtx.strokeStyle = `rgba(16, 185, 129, ${0.4 + (barHeight / 50)})`;
    } else if (state.status === "thinking") {
      canvasCtx.strokeStyle = `rgba(139, 92, 246, ${0.3 + (barHeight / 40)})`;
    } else {
      canvasCtx.strokeStyle = "rgba(255, 255, 255, 0.08)";
    }

    canvasCtx.lineWidth = 2.5;
    canvasCtx.lineCap = "round";
    canvasCtx.stroke();
  }

  requestAnimationFrame(renderVisualizer);
}

// ==============================================================================
// 7. UI HELPERS & EVENT LISTENERS
// ==============================================================================

function updateAppStatus(status, label) {
  state.status = status;
  DOM.stateIndicator.className = `state-pill state-${status}`;
  DOM.stateLabel.textContent = label.toUpperCase();
}

function appendChatMessage(role, text) {
  state.turnsCount++;
  DOM.turnCounter.textContent = `${state.turnsCount} turns`;

  const turnEl = document.createElement("div");
  turnEl.className = `chat-turn ${role}-turn`;

  const bubble = document.createElement("div");
  bubble.className = "turn-bubble";

  const author = document.createElement("span");
  author.className = "turn-author";
  author.textContent = role === "user" ? "YOU" : "NOVA";

  const content = document.createElement("p");
  content.textContent = text;

  const time = document.createElement("span");
  time.className = "turn-time";
  time.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  bubble.appendChild(author);
  bubble.appendChild(content);
  bubble.appendChild(time);
  turnEl.appendChild(bubble);

  DOM.chatMessageList.appendChild(turnEl);
  DOM.chatMessageList.scrollTop = DOM.chatMessageList.scrollHeight;

  if (role === "assistant") {
    state.currentAssistantBubble = content;
  }
}

function updateActiveChatBubble(text) {
  if (state.currentAssistantBubble) {
    state.currentAssistantBubble.textContent = text;
    DOM.chatMessageList.scrollTop = DOM.chatMessageList.scrollHeight;
  } else {
    appendChatMessage("assistant", text);
  }
}

function setupEventListeners() {
  // Mic Core Button Click
  DOM.micOrbBtn.addEventListener("click", () => {
    unlockMobileAudioSession();
    if (state.mode === "hands-free") {
      if (state.isRecording) {
        stopAudioCapture();
      } else {
        startAudioCapture();
      }
    }
  });

  // Push-to-Talk Mouse & Touch Events
  DOM.micOrbBtn.addEventListener("mousedown", () => {
    unlockMobileAudioSession();
    if (state.mode === "push-to-talk") {
      startAudioCapture();
    }
  });

  function flushSpeechAndSend() {
    clearTimeout(state.sttSilenceTimer);
    if (state.hasDeviceStt && activeLiveText.trim()) {
      const textToSend = activeLiveText.trim();
      activeLiveText = "";
      pauseDeviceSpeechRecognition();
      sendTextMessage(textToSend);
    } else if (!state.hasDeviceStt) {
      onSpeechFinished();
    }
  }

  DOM.micOrbBtn.addEventListener("mouseup", () => {
    if (state.mode === "push-to-talk") {
      flushSpeechAndSend();
      setTimeout(stopAudioCapture, 300);
    }
  });

  DOM.micOrbBtn.addEventListener("touchstart", (e) => {
    unlockMobileAudioSession();
    if (state.mode === "push-to-talk") {
      e.preventDefault();
      startAudioCapture();
    }
  }, { passive: false });

  DOM.micOrbBtn.addEventListener("touchend", () => {
    if (state.mode === "push-to-talk") {
      flushSpeechAndSend();
      setTimeout(stopAudioCapture, 300);
    }
  });

  // Spacebar hotkey
  window.addEventListener("keydown", (e) => {
    if (e.code === "Space" && e.target === document.body) {
      e.preventDefault();
      if (!state.isRecording) startAudioCapture();
    }
  });

  window.addEventListener("keyup", (e) => {
    if (e.code === "Space" && state.mode === "push-to-talk" && e.target === document.body) {
      flushSpeechAndSend();
      setTimeout(stopAudioCapture, 300);
    }
  });

  // Mode Selector
  DOM.modeHandsFree.addEventListener("click", () => {
    state.mode = "hands-free";
    DOM.modeHandsFree.classList.add("active");
    DOM.modePushToTalk.classList.remove("active");
  });

  DOM.modePushToTalk.addEventListener("click", () => {
    state.mode = "push-to-talk";
    DOM.modePushToTalk.classList.add("active");
    DOM.modeHandsFree.classList.remove("active");
    stopAudioCapture();
  });

  // Quick Prompts
  DOM.quickPrompts.addEventListener("click", (e) => {
    const chip = e.target.closest(".prompt-chip");
    if (chip) {
      unlockMobileAudioSession();
      const promptText = chip.getAttribute("data-prompt");
      sendTextMessage(promptText);
    }
  });

  // Drawer Toggles
  DOM.toggleDrawerBtn.addEventListener("click", () => {
    DOM.transcriptDrawer.classList.toggle("open");
  });

  DOM.closeDrawerBtn.addEventListener("click", () => {
    DOM.transcriptDrawer.classList.remove("open");
  });

  // Text Chat Form
  DOM.textChatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = DOM.textChatInput.value.trim();
    if (text) {
      sendTextMessage(text);
      DOM.textChatInput.value = "";
    }
  });

  // Control Dock Buttons
  DOM.muteBtn.addEventListener("click", () => {
    state.isMuted = !state.isMuted;
    if (state.isMuted) {
      cancelSpeechOutput();
      DOM.muteLabel.textContent = "Muted";
      DOM.muteBtn.classList.add("dock-btn-warning");
    } else {
      DOM.muteLabel.textContent = "Sound On";
      DOM.muteBtn.classList.remove("dock-btn-warning");
    }
  });

  DOM.interruptBtn.addEventListener("click", () => {
    cancelSpeechOutput();
    if (!state.isRecording) {
      startAudioCapture();
    }
  });

  DOM.clearChatBtn.addEventListener("click", async () => {
    cancelSpeechOutput();
    DOM.chatMessageList.innerHTML = "";
    state.turnsCount = 0;
    DOM.turnCounter.textContent = "0 turns";
    DOM.streamText.textContent = "Conversation history cleared. Ready for your questions.";
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ type: "clear" }));
    }
  });

  // Global user gesture unlock for Device Text-to-Speech (mobile phones & browsers)
  document.addEventListener("click", primeDeviceTTS, { passive: true });
  document.addEventListener("touchstart", primeDeviceTTS, { passive: true });

  // Settings Modal
  DOM.settingsBtn.addEventListener("click", () => {
    primeDeviceTTS();
    DOM.settingsModal.classList.remove("hidden");
  });

  DOM.closeSettingsBtn.addEventListener("click", () => {
    DOM.settingsModal.classList.add("hidden");
  });

  if (DOM.testVoiceBtn) {
    DOM.testVoiceBtn.addEventListener("click", (e) => {
      e.preventDefault();
      // Apply chosen voice before testing
      const voiceName = DOM.voiceSelect.value;
      state.selectedVoice = state.voices.find(v => v.name === voiceName) || state.selectedVoice;
      state.speechRate = parseFloat(DOM.speechRate.value) || 1.05;
      if (DOM.speechVolume) {
        state.speechVolume = parseFloat(DOM.speechVolume.value);
      }
      testDeviceSpeech();
    });
  }

  DOM.saveSettingsBtn.addEventListener("click", () => {
    const voiceName = DOM.voiceSelect.value;
    state.selectedVoice = state.voices.find(v => v.name === voiceName) || null;
    state.vadThreshold = parseFloat(DOM.vadThreshold.value);
    state.silenceTimeoutMs = parseFloat(DOM.silenceTimeout.value) * 1000;
    state.speechRate = parseFloat(DOM.speechRate.value);
    if (DOM.speechVolume) {
      state.speechVolume = parseFloat(DOM.speechVolume.value);
    }
    DOM.settingsModal.classList.add("hidden");
  });

  if (DOM.speechVolume) {
    DOM.speechVolume.addEventListener("input", (e) => {
      const val = parseFloat(e.target.value);
      state.speechVolume = val;
      DOM.speechVolumeVal.textContent = `${Math.round(val * 100)}%`;
    });
  }

  DOM.vadThreshold.addEventListener("input", (e) => {
    DOM.vadThresholdVal.textContent = e.target.value;
  });

  DOM.silenceTimeout.addEventListener("input", (e) => {
    DOM.silenceTimeoutVal.textContent = `${e.target.value}s`;
  });

  DOM.speechRate.addEventListener("input", (e) => {
    DOM.speechRateVal.textContent = `${e.target.value}x`;
  });

  // Network IP Pill Copy Link
  if (DOM.networkIpPill) {
    DOM.networkIpPill.addEventListener("click", () => {
      const currentUrl = window.location.href;
      navigator.clipboard.writeText(currentUrl).then(() => {
        const originalText = DOM.networkIpText.textContent;
        DOM.networkIpText.textContent = "Copied to Clipboard!";
        setTimeout(() => {
          DOM.networkIpText.textContent = originalText;
        }, 2000);
      }).catch(() => {
        prompt("Copy this URL to connect from your phone:", currentUrl);
      });
    });
  }
}

function sendTextMessage(text) {
  displayUserSpeech(text);
  updateAppStatus("thinking", "Nova is thinking...");
  
  const payload = JSON.stringify({ type: "chat", text: text });
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(payload);
  } else {
    console.log("[WebSocket] Queuing message until connection opens:", text);
    state.pendingWsMessages = state.pendingWsMessages || [];
    state.pendingWsMessages.push(payload);
  }
}

// Start application safely across all browser ready states
if (document.readyState === "loading") {
  window.addEventListener("DOMContentLoaded", initApp);
} else {
  initApp();
}
