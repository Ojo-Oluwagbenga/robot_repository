"""
Binocular Stereo Camera Driver and Vision Handler for Raspberry Pi 5.
Supports synchronized side-by-side dual-lens USB stereo cameras.
Hardware Model: Sunplus TB2 USB Stereo Camera (USB ID: 1bcf:0b15)
Default Resolution: 1280x480 (640x480 VGA per eye at 30fps)
HD Resolution: 2560x720 (1280x720 per eye at 30fps)
"""

import os
import sys
import time
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, Union

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    cv2 = None
    np = None
    CV2_AVAILABLE = False

try:
    from ..config import (
        CAMERA_DEVICE_INDEX,
        CAMERA_FOURCC,
        CAMERA_DEFAULT_WIDTH,
        CAMERA_DEFAULT_HEIGHT,
        CAMERA_HD_WIDTH,
        CAMERA_HD_HEIGHT,
        CAMERA_FPS,
        CAMERA_WARMUP_FRAMES,
        CAMERA_SAVE_DIR,
    )
except (ImportError, ValueError):
    try:
        from robot.config import (
            CAMERA_DEVICE_INDEX,
            CAMERA_FOURCC,
            CAMERA_DEFAULT_WIDTH,
            CAMERA_DEFAULT_HEIGHT,
            CAMERA_HD_WIDTH,
            CAMERA_HD_HEIGHT,
            CAMERA_FPS,
            CAMERA_WARMUP_FRAMES,
            CAMERA_SAVE_DIR,
        )
    except (ImportError, ValueError):
        from config import (
            CAMERA_DEVICE_INDEX,
            CAMERA_FOURCC,
            CAMERA_DEFAULT_WIDTH,
            CAMERA_DEFAULT_HEIGHT,
            CAMERA_HD_WIDTH,
            CAMERA_HD_HEIGHT,
            CAMERA_FPS,
            CAMERA_WARMUP_FRAMES,
            CAMERA_SAVE_DIR,
        )

logger = logging.getLogger("robot.hardware.camera")


class BinocularCamera:
    """
    Hardware abstraction and processing pipeline for a binocular stereo camera.
    Provides stereo splitting (Left/Right eyes), 3D anaglyph synthesis,
    depth disparity estimation, HUD annotation, snapshot saving, and display.
    """

    def __init__(
        self,
        device_index: int = CAMERA_DEVICE_INDEX,
        width: int = CAMERA_DEFAULT_WIDTH,
        height: int = CAMERA_DEFAULT_HEIGHT,
        fps: int = CAMERA_FPS,
        fourcc: str = CAMERA_FOURCC,
        warmup_frames: int = CAMERA_WARMUP_FRAMES,
    ):
        if not CV2_AVAILABLE:
            raise ImportError(
                "OpenCV and NumPy are required for BinocularCamera. "
                "Install them with: pip install opencv-python numpy"
            )

        self.device_index = device_index
        self.width = width
        self.height = height
        self.fps = fps
        self.fourcc = fourcc
        self.warmup_frames = warmup_frames

        self._cap: Optional[cv2.VideoCapture] = None
        self._actual_width: int = 0
        self._actual_height: int = 0
        self._actual_fps: float = 0.0
        self._stereo_matcher = None
        self._lock = threading.Lock()

    def open(self) -> bool:
        """
        Open the video capture device using V4L2 backend on Linux,
        configure MJPEG stream, resolution, and settle sensor auto-exposure.
        """
        if self.is_opened():
            return True

        logger.info(f"Opening stereo camera on /dev/video{self.device_index}...")

        # Use CAP_V4L2 on Linux for low latency and correct device mapping
        if sys.platform.startswith("linux"):
            self._cap = cv2.VideoCapture(self.device_index, cv2.CAP_V4L2)
        else:
            self._cap = cv2.VideoCapture(self.device_index)

        if not self._cap.isOpened():
            logger.error(f"Failed to open camera device /dev/video{self.device_index}")
            self._cap = None
            return False

        # Set pixel format: MJPG allows full 30 FPS at high stereo resolutions
        if self.fourcc:
            fourcc_code = cv2.VideoWriter_fourcc(*self.fourcc)
            self._cap.set(cv2.CAP_PROP_FOURCC, fourcc_code)

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._cap.set(cv2.CAP_PROP_FPS, self.fps)

        # Buffer size 1 to prevent frame lag during real-time processing
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self._actual_width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._actual_height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._actual_fps = self._cap.get(cv2.CAP_PROP_FPS)

        logger.info(
            f"Camera initialized: {self._actual_width}x{self._actual_height} "
            f"@ {self._actual_fps:.1f} FPS (FOURCC: {self.fourcc})"
        )

        # Settle sensor auto-exposure and white balance
        if self.warmup_frames > 0:
            logger.debug(f"Discarding {self.warmup_frames} warmup frames...")
            for _ in range(self.warmup_frames):
                self._cap.read()

        return True

    def is_opened(self) -> bool:
        """Check if camera device is currently active and open."""
        return self._cap is not None and self._cap.isOpened()

    def read_raw(self) -> Tuple[bool, Optional[np.ndarray]]:
        """
        Grab and decode the next side-by-side stereo frame in a thread-safe manner.
        Returns:
            (success: bool, frame: Optional[np.ndarray])
        """
        with self._lock:
            if not self.is_opened():
                if not self.open():
                    return False, None

            ret, frame = self._cap.read()
            return ret, frame

    def capture_raw(self, retries: int = 3) -> np.ndarray:
        """
        Capture raw side-by-side stereo frame with automatic retries.
        Raises RuntimeError if capture fails after retries.
        """
        for _ in range(retries):
            ret, frame = self.read_raw()
            if ret and frame is not None:
                return frame
            time.sleep(0.04)

        raise RuntimeError(
            f"Failed to capture frame from camera device /dev/video{self.device_index}"
        )

    def capture_stereo(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Capture raw stereo frame and split horizontally into Left Eye and Right Eye.
        Returns:
            (left_eye_frame, right_eye_frame)
        """
        frame = self.capture_raw()
        return self.split_stereo(frame)

    @staticmethod
    def split_stereo(stereo_frame: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Split a side-by-side stereo frame into separate left and right eye images.
        """
        h, w = stereo_frame.shape[:2]
        half_w = w // 2
        left_eye = stereo_frame[:, :half_w].copy()
        right_eye = stereo_frame[:, half_w:].copy()
        return left_eye, right_eye

    @staticmethod
    def create_anaglyph(left_eye: np.ndarray, right_eye: np.ndarray) -> np.ndarray:
        """
        Synthesize a classic Red-Cyan 3D Anaglyph image from Left and Right eye views.
        Red channel comes from Left eye; Green and Blue channels come from Right eye.
        """
        if left_eye.shape != right_eye.shape:
            right_eye = cv2.resize(right_eye, (left_eye.shape[1], left_eye.shape[0]))

        anaglyph = np.zeros_like(left_eye)
        # BGR ordering in OpenCV:
        anaglyph[:, :, 0] = right_eye[:, :, 0]  # Blue from right eye
        anaglyph[:, :, 1] = right_eye[:, :, 1]  # Green from right eye
        anaglyph[:, :, 2] = left_eye[:, :, 2]   # Red from left eye
        return anaglyph

    def compute_disparity(
        self,
        left_eye: np.ndarray,
        right_eye: np.ndarray,
        num_disparities: int = 32,
        block_size: int = 15,
        colormap: int = cv2.COLORMAP_JET,
    ) -> np.ndarray:
        """
        Compute stereo depth disparity map using Block Matching (StereoBM).
        Returns a false-color depth representation.
        """
        if self._stereo_matcher is None:
            # numDisparities must be divisible by 16; blockSize must be odd (5..255)
            self._stereo_matcher = cv2.StereoBM.create(
                numDisparities=max(16, (num_disparities // 16) * 16),
                blockSize=max(5, block_size if block_size % 2 == 1 else block_size + 1),
            )

        # Convert to grayscale
        gray_l = cv2.cvtColor(left_eye, cv2.COLOR_BGR2GRAY) if len(left_eye.shape) == 3 else left_eye
        gray_r = cv2.cvtColor(right_eye, cv2.COLOR_BGR2GRAY) if len(right_eye.shape) == 3 else right_eye

        # Compute raw 16-bit signed disparity
        disp = self._stereo_matcher.compute(gray_l, gray_r)

        # Normalize to 0-255 unsigned 8-bit
        disp_norm = cv2.normalize(
            disp, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8U
        )

        # Apply color map for intuitive depth visualization
        disp_color = cv2.applyColorMap(disp_norm, colormap)
        return disp_color

    def create_annotated(
        self,
        stereo_frame: Optional[np.ndarray] = None,
        title: str = "ROBOT STEREO VISION",
    ) -> np.ndarray:
        """
        Build an annotated HUD composite containing:
        - Crosshair reticles on both optical centers
        - 'LEFT EYE (3D-L)' and 'RIGHT EYE (3D-R)' badges
        - Center separation boundary
        - Timestamp, resolution, and hardware status banner
        """
        if stereo_frame is None:
            stereo_frame = self.capture_raw()

        annotated = stereo_frame.copy()
        h, w = annotated.shape[:2]
        half_w = w // 2

        # 1. Center separator line
        cv2.line(annotated, (half_w, 0), (half_w, h), (0, 255, 255), 2)

        # 2. Left eye crosshairs & label
        left_cx = half_w // 2
        left_cy = h // 2
        self._draw_reticle(annotated, left_cx, left_cy, (0, 255, 0), "3D-L (LEFT)")

        # 3. Right eye crosshairs & label
        right_cx = half_w + (half_w // 2)
        right_cy = h // 2
        self._draw_reticle(annotated, right_cx, right_cy, (0, 200, 255), "3D-R (RIGHT)")

        # 4. Top status banner
        banner_h = 32
        overlay = annotated.copy()
        cv2.rectangle(overlay, (0, 0), (w, banner_h), (20, 20, 20), -1)
        cv2.addWeighted(overlay, 0.75, annotated, 0.25, 0, annotated)

        # Header text
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status_text = f"{title} | {w}x{h} ({half_w}x{h}/eye) | {now_str}"
        cv2.putText(
            annotated,
            status_text,
            (12, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

        return annotated

    @staticmethod
    def _draw_reticle(img: np.ndarray, cx: int, cy: int, color: Tuple[int, int, int], label: str):
        """Draw HUD aiming reticle with targeting brackets and label."""
        r = 20
        # Target circle
        cv2.circle(img, (cx, cy), r, color, 1, cv2.LINE_AA)
        cv2.circle(img, (cx, cy), 3, color, -1)

        # Target crosshair ticks
        tick = 10
        cv2.line(img, (cx - r - tick, cy), (cx - r + 4, cy), color, 1)
        cv2.line(img, (cx + r - 4, cy), (cx + r + tick, cy), color, 1)
        cv2.line(img, (cx, cy - r - tick), (cx, cy - r + 4), color, 1)
        cv2.line(img, (cx, cy + r - 4), (cx, cy + r + tick), color, 1)

        # Label tag
        cv2.rectangle(img, (cx - 45, cy + r + 6), (cx + 45, cy + r + 24), (10, 10, 10), -1)
        cv2.putText(
            img,
            label,
            (cx - 40, cy + r + 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            color,
            1,
            cv2.LINE_AA,
        )

    def save_capture(
        self,
        output_dir: Union[str, Path] = CAMERA_SAVE_DIR,
        prefix: str = "stereo",
        include_annotated: bool = True,
        include_anaglyph: bool = True,
        include_depth: bool = True,
    ) -> Dict[str, str]:
        """
        Capture current scene and save high-resolution artifacts to disk:
        - Full side-by-side stereo frame
        - Isolated Left Eye
        - Isolated Right Eye
        - Annotated HUD frame (optional)
        - 3D Red-Cyan Anaglyph (optional)
        - Depth Disparity Map (optional)

        Returns dictionary of saved file paths.
        """
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stereo_raw = self.capture_raw()
        left_eye, right_eye = self.split_stereo(stereo_raw)

        saved_files = {}

        # 1. Full raw stereo
        raw_filename = out_path / f"{prefix}_raw_{timestamp}.jpg"
        cv2.imwrite(str(raw_filename), stereo_raw, [cv2.IMWRITE_JPEG_QUALITY, 95])
        saved_files["raw_stereo"] = str(raw_filename)

        # Also save static default 'stereo_latest.jpg' for quick access
        latest_stereo = out_path / "stereo_latest.jpg"
        cv2.imwrite(str(latest_stereo), stereo_raw, [cv2.IMWRITE_JPEG_QUALITY, 95])
        saved_files["stereo_latest"] = str(latest_stereo)

        # 2. Left eye
        left_filename = out_path / f"{prefix}_left_{timestamp}.jpg"
        cv2.imwrite(str(left_filename), left_eye, [cv2.IMWRITE_JPEG_QUALITY, 95])
        saved_files["left_eye"] = str(left_filename)
        latest_left = out_path / "left_eye_latest.jpg"
        cv2.imwrite(str(latest_left), left_eye, [cv2.IMWRITE_JPEG_QUALITY, 95])

        # 3. Right eye
        right_filename = out_path / f"{prefix}_right_{timestamp}.jpg"
        cv2.imwrite(str(right_filename), right_eye, [cv2.IMWRITE_JPEG_QUALITY, 95])
        saved_files["right_eye"] = str(right_filename)
        latest_right = out_path / "right_eye_latest.jpg"
        cv2.imwrite(str(latest_right), right_eye, [cv2.IMWRITE_JPEG_QUALITY, 95])

        # 4. Annotated HUD frame
        if include_annotated:
            annotated = self.create_annotated(stereo_raw)
            ann_filename = out_path / f"{prefix}_annotated_{timestamp}.jpg"
            cv2.imwrite(str(ann_filename), annotated, [cv2.IMWRITE_JPEG_QUALITY, 95])
            saved_files["annotated"] = str(ann_filename)
            latest_ann = out_path / "annotated_latest.jpg"
            cv2.imwrite(str(latest_ann), annotated, [cv2.IMWRITE_JPEG_QUALITY, 95])

        # 5. Red-cyan 3D anaglyph
        if include_anaglyph:
            anaglyph = self.create_anaglyph(left_eye, right_eye)
            ana_filename = out_path / f"{prefix}_anaglyph_{timestamp}.jpg"
            cv2.imwrite(str(ana_filename), anaglyph, [cv2.IMWRITE_JPEG_QUALITY, 95])
            saved_files["anaglyph"] = str(ana_filename)
            latest_ana = out_path / "anaglyph_latest.jpg"
            cv2.imwrite(str(latest_ana), anaglyph, [cv2.IMWRITE_JPEG_QUALITY, 95])

        # 6. Stereo depth disparity map
        if include_depth:
            disp = self.compute_disparity(left_eye, right_eye)
            disp_filename = out_path / f"{prefix}_disparity_{timestamp}.jpg"
            cv2.imwrite(str(disp_filename), disp, [cv2.IMWRITE_JPEG_QUALITY, 95])
            saved_files["disparity"] = str(disp_filename)
            latest_disp = out_path / "disparity_latest.jpg"
            cv2.imwrite(str(latest_disp), disp, [cv2.IMWRITE_JPEG_QUALITY, 95])

        return saved_files

    @staticmethod
    def has_display() -> bool:
        """Check if an active graphical X11 or Wayland display environment exists."""
        display = os.environ.get("DISPLAY")
        wayland = os.environ.get("WAYLAND_DISPLAY")
        return bool(display or wayland)

    def show(
        self,
        frame: Optional[np.ndarray] = None,
        window_name: str = "Robot Binocular Stereo Vision",
        wait_ms: int = 0,
    ) -> bool:
        """
        Display image in an OpenCV desktop window if graphical display is present.
        If running headless (e.g. over SSH console), gracefully outputs a notification
        and renders a terminal ANSI preview.
        """
        if frame is None:
            frame = self.capture_raw()

        if self.has_display():
            try:
                cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
                cv2.imshow(window_name, frame)
                cv2.waitKey(wait_ms)
                return True
            except cv2.error as e:
                logger.warning(f"cv2.imshow failed ({e}), falling back to headless mode.")

        # Headless fallback: print terminal preview
        print(self.render_terminal_ascii(frame, width=72))
        return False

    @staticmethod
    def render_terminal_ascii(image: np.ndarray, width: int = 70) -> str:
        """
        Render a vivid 24-bit ANSI truecolor image thumbnail directly in the terminal console!
        Uses half-block unicode characters (▀) to pack 2 vertical pixels per character row,
        doubling vertical resolution over headless SSH connections.
        """
        h, w = image.shape[:2]
        aspect = h / w
        char_rows = max(8, int(width * aspect * 0.5))
        pixel_h = char_rows * 2

        small = cv2.resize(image, (width, pixel_h), interpolation=cv2.INTER_AREA)

        lines = []
        lines.append("+" + "-" * width + "+")
        for y in range(0, pixel_h, 2):
            row_str = ["|"]
            for x in range(width):
                b_t, g_t, r_t = small[y, x]
                if y + 1 < pixel_h:
                    b_b, g_b, r_b = small[y + 1, x]
                else:
                    b_b, g_b, r_b = b_t, g_t, r_t
                row_str.append(f"\033[38;2;{r_t};{g_t};{b_t}m\033[48;2;{r_b};{g_b};{b_b}m\u2580\033[0m")
            row_str.append("|")
            lines.append("".join(row_str))
        lines.append("+" + "-" * width + "+")

        return "\n".join(lines)

    def release(self):
        """Release camera hardware resource and clean up."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("Camera device released.")

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    @property
    def resolution(self) -> Tuple[int, int]:
        """Current camera resolution (width, height)."""
        return self._actual_width or self.width, self._actual_height or self.height
