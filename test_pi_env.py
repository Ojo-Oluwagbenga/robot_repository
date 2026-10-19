import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('172.23.30.92', username='pi', password='00000000', timeout=5)

cmd = "python3 -c \"import sys; print('Python:', sys.version); import cv2; print('CV2:', cv2.__version__)\""
stdin, stdout, stderr = ssh.exec_command(cmd)
print(stdout.read().decode())
print(stderr.read().decode())

cmd_pkg = "python3 -c \"for mod in ['torch', 'mediapipe', 'ultralytics', 'scipy']: \n try:\n  m=__import__(mod); print(mod, getattr(m, '__version__', 'ok'))\n except Exception as e:\n  print(mod, 'NOT installed')\""
stdin, stdout, stderr = ssh.exec_command(cmd_pkg)
print(stdout.read().decode())
print(stderr.read().decode())

ssh.close()
