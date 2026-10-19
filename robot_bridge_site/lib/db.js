/**
 * Google Firebase Realtime Database Connector
 * Connects directly to Google Realtime Database using its high-speed native REST API.
 * Includes local in-memory fallback cache so the application works seamlessly
 * both before and after FIREBASE_DATABASE_URL is configured in Vercel.
 */

import { INITIAL_BIO_DATA, INITIAL_PI_INSTRUCTIONS, INITIAL_PHONE_SPEECH } from "./default_db";

// Global in-memory cache (persists across hot reloads in dev / local environment)
const globalCache = globalThis.__robot_db_cache || {
  bio_data: INITIAL_BIO_DATA,
  pi_instructions: [...INITIAL_PI_INSTRUCTIONS],
  phone_speech: [...INITIAL_PHONE_SPEECH],
};

// Ensure all standard initial instructions exist in cache
if (!globalCache.pi_instructions || globalCache.pi_instructions.length < 5) {
  globalCache.pi_instructions = [...INITIAL_PI_INSTRUCTIONS];
}

if (!globalCache.phone_speech) {
  globalCache.phone_speech = [...INITIAL_PHONE_SPEECH];
}

if (process.env.NODE_ENV !== "production") {
  globalThis.__robot_db_cache = globalCache;
}

function getFirebaseUrl(path = "") {
  let rawUrl = process.env.FIREBASE_DATABASE_URL || "";
  if (!rawUrl) return null;

  // Clean trailing slashes
  rawUrl = rawUrl.replace(/\/+$/, "");
  
  // Format standard Firebase Realtime DB URL if project ID only was passed
  if (!rawUrl.startsWith("http://") && !rawUrl.startsWith("https://")) {
    rawUrl = `https://${rawUrl}.firebaseio.com`;
  }

  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  let finalUrl = `${rawUrl}${cleanPath}.json`;

  // Optional authentication token or database secret
  const authSecret = process.env.FIREBASE_DATABASE_SECRET || process.env.FIREBASE_AUTH_TOKEN;
  if (authSecret) {
    finalUrl += `?auth=${authSecret}`;
  }

  return finalUrl;
}

/**
 * Fetch bio_data from Google Realtime DB or local fallback.
 */
export async function getBioData() {
  const url = getFirebaseUrl("bio_data");
  if (url) {
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        if (data) {
          globalCache.bio_data = data;
          return data;
        } else {
          // If empty, seed Google Realtime DB with INITIAL_BIO_DATA
          await setBioData(INITIAL_BIO_DATA);
          return INITIAL_BIO_DATA;
        }
      }
    } catch (e) {
      console.warn("[RealtimeDB] Failed to fetch bio_data from Firebase, using cache:", e.message);
    }
  }
  return globalCache.bio_data;
}

/**
 * Save / Update bio_data in Google Realtime DB.
 */
export async function setBioData(data) {
  globalCache.bio_data = data;
  const url = getFirebaseUrl("bio_data");
  if (url) {
    try {
      await fetch(url, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
    } catch (e) {
      console.warn("[RealtimeDB] Failed to write bio_data to Firebase:", e.message);
    }
  }
  return globalCache.bio_data;
}

/**
 * Get pi_instructions list from Google Realtime DB or local fallback.
 */
export async function getPiInstructions(limit = 50, pendingOnly = false) {
  const url = getFirebaseUrl("pi_instructions");
  if (url) {
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        if (data) {
          // Firebase stores items as { [firebase_key]: item } or Array
          let list = Array.isArray(data)
            ? data.filter(Boolean)
            : Object.entries(data).map(([key, val]) => ({
                id: val.id || key,
                _firebase_key: key,
                ...val,
              }));

          // Filter pending if requested
          if (pendingOnly) {
            list = list.filter((i) => i.status === "pending");
          }

          // Sort newest first
          list.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
          globalCache.pi_instructions = list;
          return list.slice(0, limit);
        }
      }
    } catch (e) {
      console.warn("[RealtimeDB] Failed to fetch pi_instructions from Firebase, using cache:", e.message);
    }
  }

  let list = [...globalCache.pi_instructions];
  if (pendingOnly) {
    list = list.filter((i) => i.status === "pending");
  }
  list.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
  return list.slice(0, limit);
}

/**
 * Add a new instruction packet to pi_instructions in Google Realtime DB.
 */
export async function addPiInstruction(code, value, source = "gemini") {
  const bio = await getBioData();
  const normalizedCode = (code || "").toUpperCase().trim();

  // Determine validity against bio_data.codes
  let isValid = false;
  let codeEntry = null;

  if (Array.isArray(bio.codes)) {
    codeEntry = bio.codes.find((c) => (c.code || "").toUpperCase() === normalizedCode);
    isValid = Boolean(codeEntry);
  } else if (bio.codes && typeof bio.codes === "object") {
    codeEntry = bio.codes[normalizedCode];
    isValid = Boolean(codeEntry);
  }

  const packet = {
    id: `inst_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
    code: normalizedCode,
    value: String(value ?? ""),
    timestamp: new Date().toISOString(),
    status: "pending",
    source: source,
    valid_code: isValid,
    meaning: codeEntry?.meaning || codeEntry?.action || "Custom instruction",
    hardware: codeEntry?.hardware || "ESP32",
  };

  // 1. Update local cache
  globalCache.pi_instructions.unshift(packet);
  if (globalCache.pi_instructions.length > 200) {
    globalCache.pi_instructions = globalCache.pi_instructions.slice(0, 200);
  }

  // 2. Push to Google Realtime DB
  const url = getFirebaseUrl("pi_instructions");
  if (url) {
    try {
      await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(packet),
      });
    } catch (e) {
      console.warn("[RealtimeDB] Failed to push instruction to Firebase:", e.message);
    }
  }

  return packet;
}

/**
 * Acknowledge / Update instruction status (e.g. marked as 'executed' by Raspberry Pi).
 */
export async function acknowledgeInstruction(id, status = "executed") {
  const item = globalCache.pi_instructions.find((i) => i.id === id);
  if (item) {
    item.status = status;
    item.executed_at = new Date().toISOString();
  }

  // Update in Firebase if using key-based mapping
  const url = getFirebaseUrl(`pi_instructions/${id}`);
  if (url) {
    try {
      await fetch(url, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, executed_at: new Date().toISOString() }),
      });
    } catch (e) {
      console.warn("[RealtimeDB] Failed to update instruction in Firebase:", e.message);
    }
  }

  return item || { id, status };
}

/**
 * Clear all instructions (reset database).
 */
export async function clearAllInstructions() {
  globalCache.pi_instructions = [];
  const url = getFirebaseUrl("pi_instructions");
  if (url) {
    try {
      await fetch(url, {
        method: "DELETE",
      });
    } catch (e) {
      console.warn("[RealtimeDB] Failed to clear instructions in Firebase:", e.message);
    }
  }
  return { status: "cleared" };
}

/**
 * Get phone_speech queue from Google Realtime DB or local fallback.
 */
export async function getPhoneSpeech(limit = 50, pendingOnly = false) {
  const url = getFirebaseUrl("phone_speech");
  if (url) {
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        if (data) {
          let list = Array.isArray(data)
            ? data.filter(Boolean)
            : Object.entries(data).map(([key, val]) => ({
                id: val.id || key,
                _firebase_key: key,
                ...val,
              }));

          if (pendingOnly) {
            list = list.filter((i) => i.status === "pending");
          }

          list.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
          globalCache.phone_speech = list;
          return list.slice(0, limit);
        }
      }
    } catch (e) {
      console.warn("[RealtimeDB] Failed to fetch phone_speech from Firebase, using cache:", e.message);
    }
  }

  let list = [...(globalCache.phone_speech || [])];
  if (pendingOnly) {
    list = list.filter((i) => i.status === "pending");
  }
  list.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
  return list.slice(0, limit);
}

/**
 * Add a new text packet to phone_speech queue.
 */
export async function addPhoneSpeech(text, source = "pi") {
  const cleanText = String(text || "").trim();
  if (!cleanText) {
    throw new Error("Text cannot be empty");
  }

  const packet = {
    id: `speech_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
    text: cleanText,
    timestamp: new Date().toISOString(),
    status: "pending",
    source: source,
  };

  // 1. Update local cache
  if (!globalCache.phone_speech) globalCache.phone_speech = [];
  globalCache.phone_speech.unshift(packet);
  if (globalCache.phone_speech.length > 200) {
    globalCache.phone_speech = globalCache.phone_speech.slice(0, 200);
  }

  // 2. Push to Google Realtime DB
  const url = getFirebaseUrl("phone_speech");
  if (url) {
    try {
      await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(packet),
      });
    } catch (e) {
      console.warn("[RealtimeDB] Failed to push phone_speech to Firebase:", e.message);
    }
  }

  return packet;
}

/**
 * Acknowledge / Update phone_speech status (marked as 'spoken' by Phone).
 */
export async function acknowledgePhoneSpeech(id, status = "spoken") {
  if (!globalCache.phone_speech) globalCache.phone_speech = [];
  const item = globalCache.phone_speech.find((i) => i.id === id);
  if (item) {
    item.status = status;
    item.spoken_at = new Date().toISOString();
  }

  const url = getFirebaseUrl(`phone_speech/${id}`);
  if (url) {
    try {
      await fetch(url, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, spoken_at: new Date().toISOString() }),
      });
    } catch (e) {
      console.warn("[RealtimeDB] Failed to update phone_speech in Firebase:", e.message);
    }
  }

  return item || { id, status };
}

/**
 * Clear all phone speech history.
 */
export async function clearAllPhoneSpeech() {
  globalCache.phone_speech = [];
  const url = getFirebaseUrl("phone_speech");
  if (url) {
    try {
      await fetch(url, { method: "DELETE" });
    } catch (e) {
      console.warn("[RealtimeDB] Failed to clear phone_speech in Firebase:", e.message);
    }
  }
  return { status: "cleared" };
}

/**
 * Get entire database text/object.
 */
export async function getFullDb() {
  const bio_data = await getBioData();
  const pi_instructions = await getPiInstructions(100);
  const phone_speech = await getPhoneSpeech(100);
  return {
    database_provider: process.env.FIREBASE_DATABASE_URL ? "Google Realtime Database" : "Local Database Mode (Add FIREBASE_DATABASE_URL in Vercel to sync with Google RealtimeDB)",
    firebase_connected: Boolean(process.env.FIREBASE_DATABASE_URL),
    firebase_url: process.env.FIREBASE_DATABASE_URL ? process.env.FIREBASE_DATABASE_URL.replace(/(https?:\/\/)([^@]+@)?/, "$1") : null,
    total_instructions: pi_instructions.length,
    pending_instructions: pi_instructions.filter(i => i.status === "pending").length,
    total_phone_speech: phone_speech.length,
    pending_phone_speech: phone_speech.filter(i => i.status === "pending").length,
    bio_data,
    pi_instructions,
    phone_speech,
  };
}
