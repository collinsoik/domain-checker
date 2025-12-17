#!/usr/bin/env python3
"""Debug WHOIS response to see exact format."""

import asyncio
import base64
from pathlib import Path

WHOIS_SERVER = "whois.verisign-grs.com"
WHOIS_PORT = 43
PROXY_FILE = Path("/Users/collinsoik/Desktop/Code_Space/Proxy Status Checker/proxies.txt")


def load_proxy():
    with open(PROXY_FILE) as f:
        line = f.readline().strip()
        auth, hostport = line.split("@")
        user, passwd = auth.split(":")
        host, port = hostport.split(":")
        return {"host": host, "port": int(port), "user": user, "pass": passwd}


async def main():
    proxy = load_proxy()

    # Test domains
    test_domains = [
        "google.com",        # taken
        "testllc00000001.com",  # likely available
    ]

    for domain in test_domains:
        print(f"\n{'='*60}")
        print(f"Domain: {domain}")
        print("="*60)

        reader, writer = await asyncio.open_connection(proxy["host"], proxy["port"])

        auth = base64.b64encode(f"{proxy['user']}:{proxy['pass']}".encode()).decode()
        connect_req = f"CONNECT {WHOIS_SERVER}:{WHOIS_PORT} HTTP/1.1\r\nProxy-Authorization: Basic {auth}\r\n\r\n"

        writer.write(connect_req.encode())
        await writer.drain()

        # Read CONNECT response
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break

        # Send WHOIS query
        writer.write(f"{domain}\r\n".encode())
        await writer.drain()

        # Read response in chunks to see what we get
        for byte_count in [16, 32, 48, 64, 128]:
            try:
                # We can only read once, so just read 128 bytes
                pass
            except:
                pass

        # Read full response
        response = await asyncio.wait_for(reader.read(256), timeout=10)
        writer.close()

        print(f"\nFirst 128 bytes (repr):")
        print(repr(response[:128]))

        print(f"\nFirst 128 bytes (decoded):")
        print(response[:128].decode('utf-8', errors='ignore'))

        print(f"\nByte positions:")
        print(f"  'No match' at: {response.find(b'No match')}")
        print(f"  'Domain Name' at: {response.find(b'Domain Name')}")
        print(f"  'DOMAIN NAME' at: {response.find(b'DOMAIN NAME')}")


asyncio.run(main())
