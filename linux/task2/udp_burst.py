#!/usr/bin/env python3
# wsl_udp_burst.py — контрольный UDP burst для теста IDS
from scapy.all import IP, UDP, send
import time

target_ip = "192.168.0.106"   # замените на IP вашей VM
target_port = 60000
burst_size = 100             # пакетов в одном броске
bursts = 3                  # количество бросков
pause = 0.2                  # пауза между бросками (сек)
payload = b"A" * 700

for b in range(bursts):
    for i in range(burst_size):
        pkt = IP(dst=target_ip)/UDP(dport=target_port)/payload
        send(pkt, verbose=False)
    print(f"burst {b+1}/{bursts} sent")
    time.sleep(pause)

print("done")
