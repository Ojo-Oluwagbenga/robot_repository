"""Robot Vision and Camera Package."""

try:
    from ..hardware.camera import BinocularCamera
except (ImportError, ValueError):
    try:
        from hardware.camera import BinocularCamera
    except (ImportError, ValueError):
        from robot.hardware.camera import BinocularCamera

try:
    from .ball_tracker import MultiBallTracker
except (ImportError, ValueError):
    try:
        from ball_tracker import MultiBallTracker
    except (ImportError, ValueError):
        from robot.vision.ball_tracker import MultiBallTracker

try:
    from .arm_pose_estimator import ArmPoseEstimator, ArmPoseResult, FingerTip
except (ImportError, ValueError):
    try:
        from arm_pose_estimator import ArmPoseEstimator, ArmPoseResult, FingerTip
    except (ImportError, ValueError):
        from robot.vision.arm_pose_estimator import ArmPoseEstimator, ArmPoseResult, FingerTip

try:
    from .hand_marker_model import HandMarkerTracker, MarkerState
except (ImportError, ValueError):
    try:
        from hand_marker_model import HandMarkerTracker, MarkerState
    except (ImportError, ValueError):
        from robot.vision.hand_marker_model import HandMarkerTracker, MarkerState

try:
    from .hand_recognizer import MultiAngleHandRecognizer, HandRecognitionResult
except (ImportError, ValueError):
    try:
        from hand_recognizer import MultiAngleHandRecognizer, HandRecognitionResult
    except (ImportError, ValueError):
        from robot.vision.hand_recognizer import MultiAngleHandRecognizer, HandRecognitionResult

__all__ = [
    "BinocularCamera",
    "MultiBallTracker",
    "ArmPoseEstimator",
    "ArmPoseResult",
    "FingerTip",
    "HandMarkerTracker",
    "MarkerState",
    "MultiAngleHandRecognizer",
    "HandRecognitionResult",
]

