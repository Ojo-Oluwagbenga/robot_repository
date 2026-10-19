"""
Nova Voice Web App - Network Launcher
Starts the FastAPI WebSocket server on 0.0.0.0:8000.
Accessible from any computer, tablet, or smartphone on your local Wi-Fi network.
"""

import sys
import socket
from pathlib import Path
import uvicorn

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Ensure the root directory is on the python path
ROOT_DIR = Path(__file__).parent.parent.resolve()
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import threading
from robot_voice_web.server import app, get_network_ips, DEFAULT_MODEL, GEMINI_API_KEY
from robot_voice_web.ssl_helper import get_or_create_ssl_cert


def main():
    http_port = 8000
    https_port = 8443
    host = "0.0.0.0"
    ips = get_network_ips()

    # Get or generate SSL certificates for mobile HTTPS support
    cert_path, key_path = get_or_create_ssl_cert()

    print("\n" + "=" * 70)
    print("      [*] NOVA ROBOT VOICE INTERFACE - GEMINI STREAMING WEB APP")
    print("=" * 70)
    print(f"  AI Backend   : Google Gemini ({DEFAULT_MODEL})")
    print(f"  API Status   : {'[ONLINE] Gemini Key Active' if GEMINI_API_KEY else '[MISSING] GEMINI_API_KEY'}")
    print(f"  HTTP Port    : {http_port} (Standard Web)")
    print(f"  HTTPS Port   : {https_port} (Secure Web / Mobile Phone Microphone)")
    print("-" * 70)
    print("  Connect from ANY device on your Wi-Fi network using these links:\n")
    print("  * ON YOUR PHONE (Requires HTTPS for Mobile Microphone):")
    for ip in ips:
        if not ip.startswith("127."):
            print(f"    --> https://{ip}:{https_port}  (Tap 'Advanced' -> 'Proceed' to accept SSL)")
    print(f"    --> https://localhost:{https_port}")

    print("\n  * ON YOUR PC / LAPTOP (Standard HTTP):")
    print(f"    --> http://localhost:{http_port}")
    for ip in ips:
        if not ip.startswith("127."):
            print(f"    --> http://{ip}:{http_port}")
    print("-" * 70)
    print("  Features:")
    print("    - Hands-Free Continuous Voice Conversation (Auto-VAD)")
    print("    - 60fps Holographic Bioluminescent Audio Visualizer")
    print("    - Sentence-by-sentence low-latency TTS speech playback")
    print("    - Push-to-Talk and Tap-to-Talk modes")
    print("    - Full-duplex Barge-In / Interruptibility")
    print("=" * 70 + "\n", flush=True)

    # Start HTTPS server in background daemon thread
    def run_https():
        config = uvicorn.Config(
            app,
            host=host,
            port=https_port,
            ssl_certfile=str(cert_path),
            ssl_keyfile=str(key_path),
            log_level="warning",
        )
        server = uvicorn.Server(config)
        server.run()

    https_thread = threading.Thread(target=run_https, daemon=True)
    https_thread.start()

    # Main thread runs HTTP on 8000
    uvicorn.run(app, host=host, port=http_port, log_level="warning")


if __name__ == "__main__":
    main()

