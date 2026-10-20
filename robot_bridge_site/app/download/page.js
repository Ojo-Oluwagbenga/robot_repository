"use client";

import { useState, useEffect } from "react";
import QRCode from "qrcode";

export default function DownloadPage() {
  const [origin, setOrigin] = useState("http://10.36.113.172:3000");
  const [apkQr, setApkQr] = useState("");
  const [pageQr, setPageQr] = useState("");
  const [activeTab, setActiveTab] = useState("apk"); // "apk" | "page"
  const [copiedUrl, setCopiedUrl] = useState(false);
  const [copiedInstr, setCopiedInstr] = useState(false);
  const [serverStatus, setServerStatus] = useState("checking"); // "online" | "checking" | "offline"
  const [pingMs, setPingMs] = useState(null);
  const [instructionCount, setInstructionCount] = useState(0);

  useEffect(() => {
    if (typeof window !== "undefined") {
      const currentOrigin = window.location.origin;
      setOrigin(currentOrigin);

      const apkDownloadUrl = `${currentOrigin}/LUKU-Live.apk`;
      const currentUrl = window.location.href;

      QRCode.toDataURL(apkDownloadUrl, {
        width: 280,
        margin: 2,
        color: {
          dark: "#0b0f14",
          light: "#ffffff",
        },
      })
        .then(setApkQr)
        .catch(console.error);

      QRCode.toDataURL(currentUrl, {
        width: 280,
        margin: 2,
        color: {
          dark: "#0b0f14",
          light: "#ffffff",
        },
      })
        .then(setPageQr)
        .catch(console.error);

      // Check server status
      const t0 = performance.now();
      fetch("/api/db", { cache: "no-store" })
        .then((res) => {
          const t1 = performance.now();
          setPingMs(Math.round(t1 - t0));
          if (res.ok) {
            setServerStatus("online");
            return res.json();
          } else {
            setServerStatus("error");
          }
        })
        .then((data) => {
          if (data && data.pi_instructions) {
            setInstructionCount(data.pi_instructions.length);
          }
        })
        .catch(() => {
          setServerStatus("offline");
        });
    }
  }, []);

  const apkUrl = `${origin}/LUKU-Live.apk`;
  const geminiUrl = `${origin}/gemini`;

  const copyToClipboard = (text, type) => {
    navigator.clipboard.writeText(text);
    if (type === "apk") {
      setCopiedUrl(true);
      setTimeout(() => setCopiedUrl(false), 2000);
    } else {
      setCopiedInstr(true);
      setTimeout(() => setCopiedInstr(false), 2000);
    }
  };

  return (
    <main style={{ padding: "32px 20px", maxWidth: "980px", margin: "0 auto", color: "var(--text-main)" }}>
      {/* Top Navigation */}
      <nav style={{ marginBottom: "24px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <a
          href="/"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "8px",
            color: "var(--accent-cyan)",
            fontSize: "13px",
            textDecoration: "none",
            background: "#11161d",
            padding: "8px 14px",
            borderRadius: "6px",
            border: "1px solid var(--border-color)",
          }}
        >
          ← Back to Bridge Dashboard
        </a>

        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span
            style={{
              display: "inline-block",
              width: "8px",
              height: "8px",
              borderRadius: "50%",
              background: serverStatus === "online" ? "#10b981" : "#f59e0b",
              boxShadow: serverStatus === "online" ? "0 0 8px #10b981" : "none",
            }}
          />
          <span style={{ fontSize: "12px", color: "var(--text-dim)" }}>
            Bridge:{" "}
            <strong style={{ color: serverStatus === "online" ? "#10b981" : "#f59e0b" }}>
              {serverStatus.toUpperCase()}
            </strong>{" "}
            {pingMs !== null && `(${pingMs}ms)`}
          </span>
        </div>
      </nav>

      {/* Hero Header */}
      <div
        style={{
          background: "linear-gradient(135deg, #0e1726 0%, #111e33 100%)",
          border: "1px solid #1e3a5f",
          borderRadius: "12px",
          padding: "32px 28px",
          marginBottom: "28px",
          boxShadow: "0 8px 30px rgba(0, 0, 0, 0.4)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "16px", marginBottom: "12px" }}>
          <div
            style={{
              width: "52px",
              height: "52px",
              borderRadius: "14px",
              background: "linear-gradient(135deg, #2ec4b6 0%, #06b6d4 100%)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: "26px",
              boxShadow: "0 0 20px rgba(46, 196, 182, 0.4)",
            }}
          >
            🤖
          </div>
          <div>
            <h1 style={{ fontSize: "24px", fontWeight: "700", color: "#f8fafc", margin: 0 }}>
              LUKU Live for Android
            </h1>
            <p style={{ color: "#94a3b8", fontSize: "13px", marginTop: "4px" }}>
              Voice companion APK configured for Nova Robot Bridge • Version 6.3
            </p>
          </div>
        </div>

        <p style={{ color: "#cbd5e1", fontSize: "14px", lineHeight: "1.6", maxWidth: "780px", margin: "16px 0 24px" }}>
          This updated build is pre-wired to communicate directly with your local system at{" "}
          <strong style={{ color: "#38bdf8" }}>robot_bridge_site</strong>. All AI instructions and physical motor
          triggers (nodding, turning, waving, emergency stop) go straight to this local bridge.
        </p>

        {/* Primary Download Buttons */}
        <div style={{ display: "flex", gap: "12px", flexWrap: "wrap", alignItems: "center" }}>
          <a
            href="/LUKU-Live.apk"
            download="LUKU-Live.apk"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "10px",
              background: "linear-gradient(135deg, #10b981 0%, #059669 100%)",
              color: "#ffffff",
              padding: "14px 28px",
              borderRadius: "8px",
              fontSize: "15px",
              fontWeight: "700",
              textDecoration: "none",
              boxShadow: "0 4px 18px rgba(16, 185, 129, 0.4)",
              transition: "transform 0.15s ease",
            }}
          >
            <span style={{ fontSize: "18px" }}>⬇</span>
            Download APK (74 KB)
          </a>

          <button
            onClick={() => copyToClipboard(apkUrl, "apk")}
            style={{
              background: "#1e293b",
              color: "#e2e8f0",
              border: "1px solid #334155",
              padding: "14px 20px",
              borderRadius: "8px",
              fontSize: "13px",
              fontWeight: "600",
              cursor: "pointer",
            }}
          >
            {copiedUrl ? "✓ APK URL Copied!" : "📋 Copy Direct Download Link"}
          </button>
        </div>
      </div>

      {/* Main Grid: QR Code & Android Instructions */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
          gap: "24px",
          marginBottom: "28px",
        }}
      >
        {/* QR Code Card */}
        <div
          style={{
            background: "#11161d",
            border: "1px solid var(--border-color)",
            borderRadius: "12px",
            padding: "24px",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            textAlign: "center",
          }}
        >
          <div style={{ marginBottom: "16px", width: "100%" }}>
            <h2 style={{ fontSize: "16px", fontWeight: "700", color: "#f8fafc", marginBottom: "8px" }}>
              Scan with Android Phone
            </h2>
            <p style={{ color: "var(--text-dim)", fontSize: "12px" }}>
              Point your phone camera to download instantly over Wi-Fi
            </p>

            <div style={{ display: "flex", justifyContent: "center", gap: "8px", marginTop: "12px" }}>
              <button
                onClick={() => setActiveTab("apk")}
                style={{
                  background: activeTab === "apk" ? "#06b6d4" : "#1f2937",
                  color: activeTab === "apk" ? "#0b0f14" : "#94a3b8",
                  border: "none",
                  padding: "5px 12px",
                  borderRadius: "4px",
                  fontSize: "11px",
                  fontWeight: "600",
                }}
              >
                Direct APK Link
              </button>
              <button
                onClick={() => setActiveTab("page")}
                style={{
                  background: activeTab === "page" ? "#06b6d4" : "#1f2937",
                  color: activeTab === "page" ? "#0b0f14" : "#94a3b8",
                  border: "none",
                  padding: "5px 12px",
                  borderRadius: "4px",
                  fontSize: "11px",
                  fontWeight: "600",
                }}
              >
                Open Download Page
              </button>
            </div>
          </div>

          <div
            style={{
              background: "#ffffff",
              padding: "16px",
              borderRadius: "12px",
              display: "inline-block",
              boxShadow: "0 4px 16px rgba(0, 0, 0, 0.5)",
              marginBottom: "16px",
            }}
          >
            {activeTab === "apk" && apkQr ? (
              <img src={apkQr} alt="QR Code for APK" style={{ width: "220px", height: "220px", display: "block" }} />
            ) : pageQr ? (
              <img src={pageQr} alt="QR Code for Page" style={{ width: "220px", height: "220px", display: "block" }} />
            ) : (
              <div style={{ width: "220px", height: "220px", display: "flex", alignItems: "center", justifyContent: "center", color: "#475569" }}>
                Generating QR...
              </div>
            )}
          </div>

          <code
            style={{
              fontSize: "11px",
              color: "#38bdf8",
              background: "#0b0f14",
              padding: "6px 12px",
              borderRadius: "4px",
              border: "1px solid #1e293b",
              wordBreak: "break-all",
              maxWidth: "100%",
            }}
          >
            {activeTab === "apk" ? apkUrl : `${origin}/download`}
          </code>
        </div>

        {/* Installation Steps Card */}
        <div
          style={{
            background: "#11161d",
            border: "1px solid var(--border-color)",
            borderRadius: "12px",
            padding: "24px",
          }}
        >
          <h2 style={{ fontSize: "16px", fontWeight: "700", color: "#f8fafc", marginBottom: "16px" }}>
            How to Install on Android
          </h2>

          <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
            <div style={{ display: "flex", gap: "12px" }}>
              <span
                style={{
                  width: "24px",
                  height: "24px",
                  borderRadius: "50%",
                  background: "#06b6d4",
                  color: "#0b0f14",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontWeight: "700",
                  fontSize: "12px",
                  flexShrink: 0,
                }}
              >
                1
              </span>
              <div>
                <strong style={{ color: "#f1f5f9", fontSize: "13px" }}>Download the APK</strong>
                <p style={{ color: "var(--text-dim)", fontSize: "12px", marginTop: "2px" }}>
                  Scan the QR code above or tap Download APK directly from Chrome or your phone browser.
                </p>
              </div>
            </div>

            <div style={{ display: "flex", gap: "12px" }}>
              <span
                style={{
                  width: "24px",
                  height: "24px",
                  borderRadius: "50%",
                  background: "#06b6d4",
                  color: "#0b0f14",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontWeight: "700",
                  fontSize: "12px",
                  flexShrink: 0,
                }}
              >
                2
              </span>
              <div>
                <strong style={{ color: "#f1f5f9", fontSize: "13px" }}>Allow Unknown Sources</strong>
                <p style={{ color: "var(--text-dim)", fontSize: "12px", marginTop: "2px" }}>
                  If Android warns <em>"File might be harmful"</em>, tap <strong>Download anyway</strong>. If prompted
                  to allow installation, open Settings and enable <strong>Allow from this source</strong>.
                </p>
              </div>
            </div>

            <div style={{ display: "flex", gap: "12px" }}>
              <span
                style={{
                  width: "24px",
                  height: "24px",
                  borderRadius: "50%",
                  background: "#06b6d4",
                  color: "#0b0f14",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontWeight: "700",
                  fontSize: "12px",
                  flexShrink: 0,
                }}
              >
                3
              </span>
              <div>
                <strong style={{ color: "#f1f5f9", fontSize: "13px" }}>Open and Grant Mic Permission</strong>
                <p style={{ color: "var(--text-dim)", fontSize: "12px", marginTop: "2px" }}>
                  Open LUKU Live and allow microphone access so it can hear your spoken voice commands.
                </p>
              </div>
            </div>

            <div style={{ display: "flex", gap: "12px" }}>
              <span
                style={{
                  width: "24px",
                  height: "24px",
                  borderRadius: "50%",
                  background: "#10b981",
                  color: "#0b0f14",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontWeight: "700",
                  fontSize: "12px",
                  flexShrink: 0,
                }}
              >
                ✓
              </span>
              <div>
                <strong style={{ color: "#10b981", fontSize: "13px" }}>Ready & Connected!</strong>
                <p style={{ color: "var(--text-dim)", fontSize: "12px", marginTop: "2px" }}>
                  The APK is already preset with this bridge URL. Simply ensure your phone is connected to the same
                  Wi-Fi network.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Preset Bridge Configuration Card */}
      <div
        style={{
          background: "#0d131a",
          border: "1px solid #1e293b",
          borderRadius: "12px",
          padding: "24px",
          marginBottom: "32px",
        }}
      >
        <h2 style={{ fontSize: "15px", fontWeight: "700", color: "#f8fafc", marginBottom: "8px" }}>
          📡 Pre-Configured Bridge Parameters
        </h2>
        <p style={{ color: "var(--text-dim)", fontSize: "12px", marginBottom: "16px" }}>
          These parameters are baked into this build. You can also view or modify them anytime inside the LUKU Settings
          screen.
        </p>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "12px" }}>
          <div style={{ background: "#11161d", padding: "12px 16px", borderRadius: "8px", border: "1px solid #1e2633" }}>
            <div style={{ fontSize: "11px", color: "#94a3b8" }}>INSTRUCTION URL (FETCHED BY LUKU)</div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginTop: "6px",
                gap: "8px",
              }}
            >
              <code style={{ color: "#38bdf8", fontSize: "12px", wordBreak: "break-all" }}>{geminiUrl}</code>
              <button
                onClick={() => copyToClipboard(geminiUrl, "instr")}
                style={{
                  background: "#1f2937",
                  color: "#cbd5e1",
                  border: "none",
                  padding: "4px 8px",
                  borderRadius: "4px",
                  fontSize: "11px",
                  cursor: "pointer",
                  flexShrink: 0,
                }}
              >
                {copiedInstr ? "Copied" : "Copy"}
              </button>
            </div>
          </div>

          <div style={{ background: "#11161d", padding: "12px 16px", borderRadius: "8px", border: "1px solid #1e2633" }}>
            <div style={{ fontSize: "11px", color: "#94a3b8" }}>ROBOT ACTION QUEUE</div>
            <div style={{ marginTop: "6px" }}>
              <code style={{ color: "#10b981", fontSize: "12px" }}>{origin}/instruct?code=...</code>
              <div style={{ fontSize: "11px", color: "#64748b", marginTop: "4px" }}>
                Total instructions logged: {instructionCount}
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
