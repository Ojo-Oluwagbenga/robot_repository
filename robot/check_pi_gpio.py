import lgpio
import subprocess

print("=" * 60)
print("INVESTIGATING PI 5 GPIO 26 AND GPIOCHIPS")
print("=" * 60)

# Check gpioinfo using system tools
try:
    chips = subprocess.check_output(["ls", "-l", "/dev/gpiochip*"], text=True)
    print("Available gpiochips in /dev:\n", chips)
except Exception as e:
    print("Error listing /dev/gpiochip*:", e)

# Check gpiochip label via gpiodetect if available
try:
    detect = subprocess.check_output(["gpiodetect"], text=True)
    print("gpiodetect output:\n", detect)
except Exception as e:
    print("gpiodetect not available or error:", e)

# Test lgpio chips
for chip in [0, 4, 10, 11, 12, 13]:
    try:
        h = lgpio.gpiochip_open(chip)
        info = lgpio.gpio_get_chip_info(h)
        print(f"Chip {chip}: lines={info[1]}, name={info[0]}, label={info[2]}")
        lgpio.gpiochip_close(h)
    except Exception as e:
        print(f"Chip {chip} error:", e)

print("\n[*] Initial pinctrl state for pin 26:")
print(subprocess.check_output(["pinctrl", "get", "26"], text=True).strip())
