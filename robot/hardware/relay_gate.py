"""
Relay Gate Control for Motor Power (VM) on Raspberry Pi 5.
=============================================================================
GPIO 26 (Physical Pin 37, BCM 26) drives the motor power relay gate.
Driving GPIO 26 HIGH (3.3V) energizes/closes the relay gate so VM enters
the motor drivers.

Supports:
  1. Direct hardware execution on Raspberry Pi 5 (via pinctrl & lgpio).
  2. Remote execution from PC over SSH (if running test scripts from PC).
"""

import os
import sys
import subprocess
import time
from typing import Optional

PIN_POWER_ENABLE = 26
DEFAULT_PI_HOST = os.environ.get("ROBOT_PI_HOST", "10.128.135.92")
DEFAULT_PI_USER = os.environ.get("ROBOT_PI_USER", "pi")

_lgpio_handle = None
_relay_state = False


def _is_running_on_pi() -> bool:
    """Check if this script is executing natively on the Raspberry Pi."""
    if os.name != "posix":
        return False
    try:
        if os.path.exists("/proc/device-tree/model"):
            with open("/proc/device-tree/model", "r") as f:
                model = f.read().lower()
                if "raspberry pi" in model:
                    return True
    except Exception:
        pass
    # Check if pinctrl tool exists
    import shutil
    return shutil.which("pinctrl") is not None


def enable_motor_power_relay(host: Optional[str] = None) -> bool:
    """
    Drive GPIO 26 HIGH to close the relay gate and supply VM to motor drivers.
    If running natively on the Pi, drives the pin directly.
    If running from PC, executes the command on the Pi via SSH.
    """
    global _lgpio_handle, _relay_state

    # Path A: Running natively on Raspberry Pi 5
    if _is_running_on_pi():
        # 1. Native Raspberry Pi pinctrl tool (sets hardware register on Pi 5 RP1)
        # This persists even when individual python processes exit.
        try:
            res = subprocess.run(
                ["pinctrl", "set", str(PIN_POWER_ENABLE), "op", "dh"],
                capture_output=True, text=True, timeout=2
            )
            if res.returncode == 0:
                _relay_state = True
        except Exception:
            pass

        # 2. lgpio driver claim
        try:
            import lgpio
            if _lgpio_handle is None:
                for chip_num in [0, 4]:
                    try:
                        h = lgpio.gpiochip_open(chip_num)
                        lgpio.gpio_claim_output(h, PIN_POWER_ENABLE, 1)
                        _lgpio_handle = h
                        break
                    except Exception:
                        pass
            if _lgpio_handle is not None:
                lgpio.gpio_write(_lgpio_handle, PIN_POWER_ENABLE, 1)
                _relay_state = True
        except Exception:
            pass

        # 3. Fallbacks if needed
        if not _relay_state:
            try:
                from gpiozero import OutputDevice
                dev = OutputDevice(PIN_POWER_ENABLE, active_high=True, initial_value=True)
                dev.on()
                _relay_state = True
            except Exception:
                pass

        time.sleep(0.05)  # Allow relay contacts to debounce and settle
        return _relay_state

    # Path B: Running on PC / laptop (remote invocation over SSH)
    pi_ip = host or DEFAULT_PI_HOST
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(pi_ip, username=DEFAULT_PI_USER, timeout=3)
        stdin, stdout, stderr = client.exec_command(f"pinctrl set {PIN_POWER_ENABLE} op dh", timeout=3)
        status = stdout.channel.recv_exit_status()
        client.close()
        if status == 0:
            _relay_state = True
            return True
    except Exception:
        pass

    # Try system ssh binary
    try:
        res = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=2", f"{DEFAULT_PI_USER}@{pi_ip}", f"pinctrl set {PIN_POWER_ENABLE} op dh"],
            capture_output=True, timeout=3
        )
        if res.returncode == 0:
            _relay_state = True
            return True
    except Exception:
        pass

    return False


def disable_motor_power_relay(host: Optional[str] = None) -> bool:
    """Drive GPIO 26 LOW to open relay gate and cut VM to motor drivers."""
    global _lgpio_handle, _relay_state

    if _is_running_on_pi():
        try:
            res = subprocess.run(
                ["pinctrl", "set", str(PIN_POWER_ENABLE), "op", "dl"],
                capture_output=True, text=True, timeout=2
            )
            if res.returncode == 0:
                _relay_state = False
        except Exception:
            pass

        try:
            import lgpio
            if _lgpio_handle is not None:
                lgpio.gpio_write(_lgpio_handle, PIN_POWER_ENABLE, 0)
                _relay_state = False
        except Exception:
            pass

        return True

    # Remote over SSH
    pi_ip = host or DEFAULT_PI_HOST
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(pi_ip, username=DEFAULT_PI_USER, timeout=3)
        stdin, stdout, stderr = client.exec_command(f"pinctrl set {PIN_POWER_ENABLE} op dl", timeout=3)
        status = stdout.channel.recv_exit_status()
        client.close()
        if status == 0:
            _relay_state = False
            return True
    except Exception:
        pass

    return False


def is_relay_enabled(host: Optional[str] = None) -> bool:
    """Check if GPIO 26 is currently driving HIGH."""
    if _is_running_on_pi():
        try:
            res = subprocess.run(
                ["pinctrl", "get", str(PIN_POWER_ENABLE)],
                capture_output=True, text=True, timeout=2
            )
            if "hi" in res.stdout.lower():
                return True
            elif "lo" in res.stdout.lower():
                return False
        except Exception:
            pass
        return _relay_state

    # Remote check over SSH
    pi_ip = host or DEFAULT_PI_HOST
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(pi_ip, username=DEFAULT_PI_USER, timeout=3)
        stdin, stdout, stderr = client.exec_command(f"pinctrl get {PIN_POWER_ENABLE}", timeout=3)
        out = stdout.read().decode("utf-8", errors="ignore")
        client.close()
        if "hi" in out.lower():
            return True
        elif "lo" in out.lower():
            return False
    except Exception:
        pass

    return _relay_state
