#!/usr/bin/env python3
"""
spoof_one_per_ip.py
Send one UDP packet from each of many source IPs (L2 send) — LAB USE ONLY.
Usage:
  sudo python3 spoof_one_per_ip.py --iface enp60s0 --target 192.168.1.50 --dst-mac aa:bb:cc:dd:ee:ff
"""
import argparse, random, time
from scapy.all import Ether, IP, UDP, sendp

def gen_ips(prefix, count):
    a,b = map(int, prefix.split('.'))
    ips=[]
    i=0
    while len(ips)<count:
        c = (i // 254) % 254 + 1
        d = (i % 254) + 1
        ips.append(f"{a}.{b}.{c}.{d}")
        i+=1
    return ips

def load_ips_from_file(path, count):
    with open(path) as f:
        lines=[l.strip() for l in f if l.strip()]
    return lines[:count]

def make_pkt(src_ip, dst_ip, dst_port=12345):
    ip = IP(src=src_ip, dst=dst_ip)
    udp = UDP(sport=random.randint(1024, 65535), dport=dst_port)
    payload = b"X"
    return ip/udp/payload

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--iface","-i", required=True)
    p.add_argument("--target","-t", required=True)
    p.add_argument("--dst-mac", default="ff:ff:ff:ff:ff:ff")
    p.add_argument("--count","-n", type=int, default=20)
    p.add_argument("--src-prefix", default="10.0", help="prefix for src IPs, like '10.0' -> 10.0.x.y")
    p.add_argument("--attackers-file","-a", default=None, help="optional file with source IPs (one per line)")
    p.add_argument("--port","-p", type=int, default=55555)
    p.add_argument("--pause", type=float, default=0.02)
    args = p.parse_args()

    if args.attackers_file:
        src_ips = load_ips_from_file(args.attackers_file, args.count)
    else:
        src_ips = gen_ips(args.src_prefix, args.count)

    print(f"Interface: {args.iface}, target: {args.target}, dst_mac: {args.dst_mac}")
    print(f"Sending 1 packet each from {len(src_ips)} source IPs. STARTING...")
    for src in src_ips:
        pkt = Ether(dst=args.dst_mac) / make_pkt(src, args.target, args.port)
        sendp(pkt, iface=args.iface, verbose=False)
        time.sleep(args.pause)
    print("DONE")

if __name__ == '__main__':
    main()
