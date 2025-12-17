#!/usr/bin/env python3
"""Test if WHOIS actually supports multiple queries per connection."""

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

    print("Testing connection reuse with detailed output...")
    print("=" * 60)

    reader, writer = await asyncio.open_connection(proxy["host"], proxy["port"])

    auth = base64.b64encode(f"{proxy['user']}:{proxy['pass']}".encode()).decode()
    connect_req = f"CONNECT {WHOIS_SERVER}:{WHOIS_PORT} HTTP/1.1\r\nProxy-Authorization: Basic {auth}\r\n\r\n"

    writer.write(connect_req.encode())
    await writer.drain()

    # Read CONNECT response
    while True:
        line = await reader.readline()
        print(f"CONNECT response: {line}")
        if line in (b"\r\n", b"\n", b""):
            break

    print("\nTunnel established. Testing queries...")
    print("-" * 60)

    domains = ["google.com", "amazon.com", "testxyz123.com", "microsoft.com", "foobar999.com"]

    for i, domain in enumerate(domains):
        print(f"\nQuery {i+1}: {domain}")

        # Send query
        writer.write(f"{domain}\r\n".encode())
        await writer.drain()
        print(f"  Sent query")

        # Try to read response
        try:
            response = await asyncio.wait_for(reader.read(64), timeout=5.0)
            print(f"  Response ({len(response)} bytes): {response[:50]}")

            if b"No match" in response:
                print(f"  Status: AVAILABLE")
            elif b"Domain Name" in response:
                print(f"  Status: TAKEN")
            else:
                print(f"  Status: UNKNOWN")

            if len(response) == 0:
                print(f"  *** Connection closed by server! ***")
                break

        except asyncio.TimeoutError:
            print(f"  TIMEOUT - no response")
        except Exception as e:
            print(f"  ERROR: {e}")
            break

    writer.close()


asyncio.run(main())
