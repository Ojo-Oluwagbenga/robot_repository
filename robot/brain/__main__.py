import sys

if __name__ == "__main__":
    if "--voice" in sys.argv:
        try:
            from robot.brain.voice import run_voice_loop
        except ImportError:
            from brain.voice import run_voice_loop
        run_voice_loop()
    else:
        try:
            from robot.brain.interactive import run_interactive_loop
        except ImportError:
            from brain.interactive import run_interactive_loop
        run_interactive_loop()
