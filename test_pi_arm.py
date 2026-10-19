import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('172.23.30.92', username='pi', password='00000000', timeout=5)

cmd = '''python3 -c "
import cv2
from vision.hand_recognizer import MultiAngleHandRecognizer

cap = cv2.VideoCapture('/dev/video0', cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))

ret, frame = cap.read()
cap.release()

if ret and frame is not None:
    rec = MultiAngleHandRecognizer()
    rec.train_from_dataset('/home/pi/projects/Robot/robot/vision/dataset/hand_angles')
    res, ann = rec.process(frame[:, :640], frame[:, 640:])
    print(f'LIVE FRAME PROCESSED ON PI: Certainty={int(res.certainty*100)}%, Wrist={res.wrist_px}, Forearm={res.forearm_angle_deg:.1f}deg, IndexTip={res.index_tip_px}, ThumbTip={res.thumb_tip_px}')
    cv2.imwrite('/home/pi/projects/Robot/robot/live_pi_result.jpg', ann)
else:
    print('Failed to read camera frame')
"'''
stdin, stdout, stderr = ssh.exec_command(f"cd /home/pi/projects/Robot/robot && {cmd}")
print("STDOUT:\n", stdout.read().decode())
print("STDERR:\n", stderr.read().decode())

ssh.close()
