import socket
import json
import time

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
s.bind(("0.0.0.0", 5005))
s.settimeout(2.5)

ping = json.dumps({"cmd": "STATUS", "esp": 0, "instructionID": "scan_all"}).encode("utf-8")
s.sendto(ping, ("255.255.255.255", 5005))

start = time.time()
print("Listening for ESPs on UDP 5005...")
while time.time() - start < 3.0:
    try:
        data, (ip, port) = s.recvfrom(2048)
        print(f"[{ip}] {data.decode('utf-8', errors='ignore')}")
    except socket.timeout:
        break
