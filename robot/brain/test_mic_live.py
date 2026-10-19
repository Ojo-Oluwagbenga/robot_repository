"""
Microphone & Audio Input Diagnostic Tool for Raspberry Pi.
1. Checks and lists all available audio recording devices.
2. Inspects PipeWire / WirePlumber input sources (wpctl).
3. Ensures default input source is set to real microphone (e.g. AB13X Headset Adapter), NOT Bluetooth speaker.
4. Displays a live visual VU level meter to verify sound sensitivity.
5. Records 3 seconds of audio and plays it back to test clarity.
"""

import math
import shutil
import subprocess
import sys
import time
import numpy as np

try:
    import sounddevice as sd
except ImportError:
    print("[ERROR] 'sounddevice' package is missing. Install with: pip3 install sounddevice")
    sys.exit(1)


def fix_pipewire_default_source():
    """Ensure PipeWire default input source is NOT the Bluetooth speaker."""
    if not shutil.which("wpctl"):
        return

    print("\n--- PipeWire Audio Source Inspection ---")
    res = subprocess.run(["wpctl", "status"], capture_output=True, text=True)
    status_out = res.stdout

    in_sources = False
    source_candidates = []
    current_default = None

    for line in status_out.splitlines():
        clean = line.strip()
        if "Sources:" in clean:
            in_sources = True
            continue
        if in_sources and any(hdr in clean for hdr in ["Filters:", "Streams:", "Settings:"]):
            in_sources = False
            continue
        if in_sources and clean and ("." in clean or "bluez" in clean):
            is_default = "*" in clean
            # Extract number
            tokens = clean.split()
            num = None
            for t in tokens:
                t_clean = t.lstrip("*").rstrip(".:")
                if t_clean.isdigit():
                    num = t_clean
                    break
            name = clean
            if num:
                source_candidates.append((num, name, is_default))
                if is_default:
                    current_default = (num, name)

    print(f"Detected {len(source_candidates)} audio source(s):")
    for num, name, is_def in source_candidates:
        prefix = " [*] (CURRENT DEFAULT)" if is_def else " [ ]"
        print(f"  {prefix} ID {num}: {name}")

    # Check if bluetooth source has default '*' anywhere in PipeWire status
    is_bluez_default = False
    for line in status_out.splitlines():
        if "*" in line and ("bluez" in line.lower() or "speaker" in line.lower()):
            is_bluez_default = True
            break

    if is_bluez_default or not current_default or (current_default and "bluez" in current_default[1].lower()):
        # Find physical mic candidate (e.g. AB13X or Headset or USB)
        best_source = None
        for num, name, _ in source_candidates:
            if "bluez" not in name.lower() and "monitor" not in name.lower():
                best_source = num
                break

        if best_source:
            def_name = current_default[1] if current_default else "Bluetooth speaker input (bluez_input)"
            print(f"\n[!] Notice: Active/default source was: '{def_name}'.")
            print(f"[*] Re-routing default recording input to physical microphone (Source ID {best_source})...")
            try:
                subprocess.run(["wpctl", "set-default", best_source], check=True)
                print(f"[+] PipeWire default recording source set to ID {best_source} successfully!")
            except Exception as e:
                print(f"[!] Could not set default source via wpctl: {e}")
        else:
            print("[!] Could not auto-detect physical microphone source. Check connections.")


def list_sounddevice_inputs():
    """List all PortAudio / sounddevice input devices."""
    print("\n--- Sounddevice Input Devices ---")
    devices = sd.query_devices()
    default_input = sd.default.device[0]

    for idx, d in enumerate(devices):
        if d.get("max_input_channels", 0) > 0:
            is_def = (idx == default_input)
            marker = " [*] (DEFAULT)" if is_def else " [ ]"
            print(f"  {marker} Device Index {idx}: '{d['name']}' (Channels: {d['max_input_channels']}, Default Rate: {d['default_samplerate']}Hz)")


def find_best_mic():
    """Auto-detect physical USB / ALSA input device compatible with 16kHz."""
    devices = sd.query_devices()

    def supports_16k(dev_idx):
        try:
            sd.check_input_settings(device=dev_idx, samplerate=16000, channels=1, dtype="float32")
            return True
        except Exception:
            return False

    # Check PipeWire / default first (automatically routes from physical mic with resampling)
    for pref_name in ["default", "pipewire"]:
        for idx, d in enumerate(devices):
            if d.get("name", "").lower() == pref_name and d.get("max_input_channels", 0) > 0:
                if supports_16k(idx):
                    return idx, f"PipeWire ({d['name']})"

    usb_keywords = ["ab13x", "headset", "usb", "microphone", "mic", "c-media"]
    excluded = ["bluez", "bluetooth", "speaker", "sink", "monitor"]
    candidates = []
    for idx, d in enumerate(devices):
        if d.get("max_input_channels", 0) <= 0:
            continue
        name = d.get("name", "").lower()
        if any(ex in name for ex in excluded):
            continue
        if not supports_16k(idx):
            continue
        score = sum(3 for kw in usb_keywords if kw in name)
        candidates.append((score, idx, d["name"]))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1], candidates[0][2]

    # Fallback to default
    try:
        def_idx = sd.default.device[0]
        if def_idx is not None and def_idx >= 0 and supports_16k(def_idx):
            d_info = sd.query_devices(def_idx, "input")
            return def_idx, d_info.get("name", f"Default #{def_idx}")
    except Exception:
        pass

    return None, "Default System Input"


def run_live_vu_meter(device=None, duration_sec=10):
    """Run real-time terminal VU volume meter."""
    dev_name = "Default"
    if device is not None:
        try:
            d_info = sd.query_devices(device, "input")
            dev_name = d_info.get("name", str(device))
        except Exception:
            dev_name = str(device)

    print("\n" + "=" * 65)
    print(f"[*] LIVE MICROPHONE TEST ({duration_sec}s) on '{dev_name}' (device {device})")
    print("    Speak or tap your microphone now to see volume levels:")
    print("=" * 65 + "\n")

    rate = 16000
    block_size = int(rate * 0.1)  # 100ms blocks

    start_time = time.time()
    max_peak = 0.0

    def audio_callback(indata, frames, time_info, status):
        nonlocal max_peak
        rms = np.sqrt(np.mean(indata**2))
        max_peak = max(max_peak, rms)

        level = min(100, int((rms / 0.15) * 100))
        bars = int(level / 3)
        bar_str = "#" * bars + " " * (33 - bars)

        tag = "QUIET" if level < 15 else ("GOOD (Speaking)" if level < 60 else "LOUD")
        remaining = int(duration_sec - (time.time() - start_time))
        print(f"\rLevel: [{bar_str}] {level:3d}% | {tag:16s} | Time left: {remaining:2d}s ", end="", flush=True)

    with sd.InputStream(device=device, samplerate=rate, channels=1, dtype="float32", blocksize=block_size, callback=audio_callback):
        while time.time() - start_time < duration_sec:
            time.sleep(0.1)

    print("\n\n" + "-" * 65)
    if max_peak > 0.015:
        print("[+] SUCCESS! Microphone is receiving sound loud and clear.")
        print(f"    Peak RMS amplitude: {max_peak:.4f} (Above detection threshold)")
    else:
        print("[!] WARNING: Very low or zero sound detected (peak RMS: " + f"{max_peak:.4f}).")
        print("    Possible causes:")
        print("    1. Microphone hardware switch is muted.")
        print("    2. Input volume / gain is set too low (check alsamixer).")
        print("    3. PipeWire is still listening to Bluetooth speaker input.")
    print("-" * 65 + "\n")


def record_and_playback(device=None):
    """Record 3 seconds of audio and play it back to test audio quality."""
    print("[*] Recording 3 seconds test clip... Speak now!")
    rate = 16000
    recording = sd.rec(int(3 * rate), samplerate=rate, channels=1, dtype="int16", device=device)
    sd.wait()
    print("[+] Recording complete! Playing it back now...")

    try:
        sd.play(recording, samplerate=rate)
        sd.wait()
        print("[+] Playback complete!")
    except Exception as e:
        print(f"[!] Playback notice: {e}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Nova Robot Microphone Diagnostic Tool")
    parser.add_argument("--device", "--mic", dest="device", type=str, default=None, help="Device index or keyword")
    parser.add_argument("--duration", type=int, default=8, help="VU meter duration in seconds")
    parser.add_argument("--playback", action="store_true", help="Run 3s record and playback test")
    args = parser.parse_args()

    print("=" * 65)
    print("     NOVA ROBOT MICROPHONE & AUDIO INPUT DIAGNOSTIC TOOL")
    print("=" * 65)

    fix_pipewire_default_source()
    list_sounddevice_inputs()

    target_dev = None
    if args.device is not None:
        try:
            target_dev = int(args.device)
        except ValueError:
            target_dev = args.device
    else:
        target_dev, dev_name = find_best_mic()
        print(f"[*] Auto-selected USB mic: '{dev_name}' (device index: {target_dev})")

    run_live_vu_meter(device=target_dev, duration_sec=args.duration)

    if args.playback:
        record_and_playback(device=target_dev)

    print("\nDiagnostic complete!")
