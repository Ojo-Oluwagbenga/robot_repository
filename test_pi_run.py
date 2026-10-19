import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('172.23.30.92', username='pi', password='00000000', timeout=5)

cmd = 'cd /home/pi/projects/Robot/robot && python3 -c "from vision.hand_recognizer import MultiAngleHandRecognizer; r = MultiAngleHandRecognizer(); print(\'SUCCESS: MultiAngleHandRecognizer initialized on Pi!\')"'
stdin, stdout, stderr = ssh.exec_command(cmd)
print("STDOUT:", stdout.read().decode())
print("STDERR:", stderr.read().decode())

ssh.close()
