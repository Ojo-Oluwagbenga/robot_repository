import cv2
import numpy as np

img = cv2.imread('c:/Users/YOHANNA/Desktop/Projects/Robot Code/robot/vision/dataset/user_marked_reference.png')
h, w, c = img.shape
print(f"Reference image size: {w}x{h}")

hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

# In the user image, there is the left subframe and right subframe (it is a stereo side-by-side or stereo capture!)
# Let's check:
# Left half is w // 2
half_w = w // 2
left_img = img[:, :half_w]
left_hsv = hsv[:, :half_w]

# 1. Blue marker (Index finger)
# High saturation, blue hue
blue_mask = cv2.inRange(left_hsv, np.array([100, 150, 150]), np.array([130, 255, 255]))
blue_pts = np.argwhere(blue_mask > 0) # (y, x)
print(f"Blue marker points found: {len(blue_pts)}")

# 2. Green marker (Thumb)
green_mask = cv2.inRange(left_hsv, np.array([40, 150, 150]), np.array([85, 255, 255]))
green_pts = np.argwhere(green_mask > 0)
print(f"Green marker points found: {len(green_pts)}")

# 3. Red marker (Wrist circle & dot)
red1 = cv2.inRange(left_hsv, np.array([0, 160, 160]), np.array([10, 255, 255]))
red2 = cv2.inRange(left_hsv, np.array([170, 160, 160]), np.array([180, 255, 255]))
red_mask = cv2.bitwise_or(red1, red2)
red_pts = np.argwhere(red_mask > 0)
print(f"Red marker points found: {len(red_pts)}")

# 4. Yellow marker (Forearm line)
yellow_mask = cv2.inRange(left_hsv, np.array([20, 160, 160]), np.array([35, 255, 255]))
yellow_pts = np.argwhere(yellow_mask > 0)
print(f"Yellow marker points found: {len(yellow_pts)}")

# Save visualization of masks
cv2.imwrite('c:/Users/YOHANNA/Desktop/Projects/Robot Code/blue_mask.jpg', blue_mask)
cv2.imwrite('c:/Users/YOHANNA/Desktop/Projects/Robot Code/green_mask.jpg', green_mask)
cv2.imwrite('c:/Users/YOHANNA/Desktop/Projects/Robot Code/red_mask.jpg', red_mask)
cv2.imwrite('c:/Users/YOHANNA/Desktop/Projects/Robot Code/yellow_mask.jpg', yellow_mask)

# Extract skeletons/centerlines of the user's blue and green strokes
if len(blue_pts) > 0:
    # Sort blue points along principal axis
    b_xs = blue_pts[:, 1]
    b_ys = blue_pts[:, 0]
    print(f"Blue (Index) X range: [{b_xs.min()} .. {b_xs.max()}], Y range: [{b_ys.min()} .. {b_ys.max()}]")

if len(green_pts) > 0:
    g_xs = green_pts[:, 1]
    g_ys = green_pts[:, 0]
    print(f"Green (Thumb) X range: [{g_xs.min()} .. {g_xs.max()}], Y range: [{g_ys.min()} .. {g_ys.max()}]")

if len(red_pts) > 0:
    r_xs = red_pts[:, 1]
    r_ys = red_pts[:, 0]
    print(f"Red (Wrist) center: ({int(r_xs.mean())}, {int(r_ys.mean())}), radius approx: {int((r_xs.max()-r_xs.min()+r_ys.max()-r_ys.min())/4)}")

if len(yellow_pts) > 0:
    y_xs = yellow_pts[:, 1]
    y_ys = yellow_pts[:, 0]
    # Line fit
    [vx, vy, x0, y0] = cv2.fitLine(np.column_stack([y_xs, y_ys]), cv2.DIST_L2, 0, 0.01, 0.01)
    ang = np.degrees(np.arctan2(vy.item(), vx.item()))
    print(f"Yellow (Forearm) orientation angle: {ang:.1f} degrees")
