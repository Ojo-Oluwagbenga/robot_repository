#!/usr/bin/env python3
"""
Unit Tests for Gentle Head Controller & Ambient Idle Behavior
=============================================================================
"""

import sys
import time
from pathlib import Path

ROBOT_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROBOT_DIR.parent
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from robot.control.gentle_head import (
    send_profile_move,
    gentle_head_turn_and_return,
    gentle_head_random_idle_glance,
    IdleHeadManager,
    ESP_HEAD,
    MOTOR_HEAD_YAW,
)


def test_gentle_profile_move():
    """Verify single profiled move execution in simulation mode."""
    ok = send_profile_move(
        esp=ESP_HEAD,
        motor=MOTOR_HEAD_YAW,
        pulse=150,
        direction=0,
        brake_count=25,
        brake_ms=6,
        simulation=True,
    )
    assert ok is True


def test_gentle_turn_and_return():
    """Verify full back-and-forth oscillation in simulation mode."""
    ok = gentle_head_turn_and_return(amplitude=120, cycles=1, simulation=True)
    assert ok is True


def test_ambient_idle_glance():
    """Verify subtle random glance and return in simulation mode."""
    ok = gentle_head_random_idle_glance(simulation=True)
    assert ok is True


def test_idle_head_manager_lifecycle():
    """Verify IdleHeadManager activity notifications and thread lifecycle."""
    mgr = IdleHeadManager(min_idle_sec=0.2, max_idle_sec=0.4, simulation=True)
    assert mgr.is_active_busy is False

    mgr.set_busy(True)
    assert mgr.is_active_busy is True

    mgr.set_busy(False)
    assert mgr.is_active_busy is False

    t_before = mgr.last_activity_time
    time.sleep(0.05)
    mgr.notify_activity()
    assert mgr.last_activity_time > t_before

    # Start and stop background manager cleanly
    mgr.start()
    assert mgr._thread is not None and mgr._thread.is_alive()
    time.sleep(0.3)
    mgr.stop()
    assert mgr._thread is None


if __name__ == "__main__":
    test_gentle_profile_move()
    test_gentle_turn_and_return()
    test_ambient_idle_glance()
    test_idle_head_manager_lifecycle()
    print("\n[GENTLE HEAD TESTS PASSED SUCCESSFULLY!]")
