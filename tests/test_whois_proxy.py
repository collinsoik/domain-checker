#!/usr/bin/env python3
"""
Test WHOIS through SOCKS5 proxy.
Also test if we can use HTTP CONNECT tunnel for port 43.
"""

import asyncio
import time
import socket
from pathlib import Path

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

PROXY_FILE = Path("/Users/collinsoik/Desktop/Code_Space/Proxy Status Checker/proxies.txt")

WHOIS_SERVER = "whois.verisign-grs.com"
WHOIS_PORT = 43


def load_proxy() -> tuple[str, int, str, str]:
    """Load first proxy and parse it."""
    with open(PROXY_FILE) as f:
        line = f.readline().strip()
        # Format: user:pass@ip:port
        auth, hostport = line.split("@")
        user, passwd = auth.split(":")
        host, port = hostport.split(":")
        return host, int(port), user, passwd


async def test_http_connect_tunnel():
    """
    Test if HTTP proxy supports CONNECT method to port 43.
    This would allow WHOIS through HTTP proxy.
    """
    print("=" * 70)
    print("TEST 1: HTTP CONNECT Tunnel to Port 43")
    print("=" * 70)

    host, port, user, passwd = load_proxy()
    print(f"Proxy: {host}:{port}")

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=10.0
        )

        # Send HTTP CONNECT request
        import base64
        auth = base64.b64encode(f"{user}:{passwd}".encode()).decode()

        connect_request = (
            f"CONNECT {WHOIS_SERVER}:{WHOIS_PORT} HTTP/1.1\r\n"
            f"Host: {WHOIS_SERVER}:{WHOIS_PORT}\r\n"
            f"Proxy-Authorization: Basic {auth}\r\n"
            f"Proxy-Connection: Keep-Alive\r\n"
            f"\r\n"
        )

        writer.write(connect_request.encode())
        await writer.drain()

        # Read response
        response = await asyncio.wait_for(reader.readline(), timeout=10.0)
        response_text = response.decode()

        print(f"Response: {response_text.strip()}")

        if "200" in response_text:
            print("SUCCESS: HTTP CONNECT tunnel established!")

            # Read remaining headers
            while True:
                line = await reader.readline()
                if line == b"\r\n" or line == b"\n" or line == b"":
                    break

            # Now send WHOIS query through the tunnel
            domain = "google.com"
            writer.write(f"{domain}\r\n".encode())
            await writer.drain()

            # Read WHOIS response
            whois_response = await asyncio.wait_for(reader.read(2048), timeout=10.0)
            print(f"\nWHOIS Response ({len(whois_response)} bytes):")
            print(whois_response[:500].decode('utf-8', errors='ignore'))

            writer.close()
            return True
        else:
            print("FAILED: Proxy rejected CONNECT to port 43")
            print("(Most HTTP proxies only allow CONNECT to ports 443, 80)")
            writer.close()
            return False

    except Exception as e:
        print(f"ERROR: {e}")
        return False


async def test_direct_comparison():
    """
    Compare direct WHOIS vs RDAP through proxy.
    Calculate if WHOIS direct + RDAP proxy is viable.
    """
    print("\n" + "=" * 70)
    print("TEST 2: Direct WHOIS Throughput Analysis")
    print("=" * 70)

    # Test direct WHOIS throughput
    domains = [f"testdomain{i:06d}.com" for i in range(50)]

    async def whois_query(domain: str):
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(WHOIS_SERVER, 43),
                timeout=10.0
            )
            writer.write(f"{domain}\r\n".encode())
            await writer.drain()
            response = await asyncio.wait_for(reader.read(1024), timeout=10.0)
            writer.close()
            return True
        except:
            return False

    # Test with high concurrency
    for concurrency in [10, 25, 50]:
        sem = asyncio.Semaphore(concurrency)

        async def limited_query(domain):
            async with sem:
                return await whois_query(domain)

        start = time.perf_counter()
        results = await asyncio.gather(*[limited_query(d) for d in domains])
        elapsed = time.perf_counter() - start

        success = sum(results)
        throughput = len(domains) / elapsed

        print(f"\nConcurrency {concurrency}: {throughput:.1f}/sec ({success}/{len(domains)} success)")


async def analyze_options():
    """Analyze the viable options."""
    print("\n" + "=" * 70)
    print("ANALYSIS: WHOIS Options")
    print("=" * 70)

    analysis = """
Option 1: WHOIS Direct (No Proxy)
---------------------------------
Pros:
  - 3x faster than RDAP (~50ms vs ~175ms)
  - Simpler protocol
Cons:
  - Exposes your real IP
  - Rate limited by Verisign (they'll block you)
  - NOT VIABLE at scale without proxies

Option 2: WHOIS via HTTP CONNECT Tunnel
---------------------------------------
Pros:
  - Can use existing HTTP proxies
Cons:
  - Most HTTP proxies block CONNECT to non-standard ports
  - {connect_status}

Option 3: WHOIS via SOCKS5 Proxies
----------------------------------
Pros:
  - SOCKS5 supports any TCP connection
  - Would allow proxied WHOIS at scale
Cons:
  - Requires SOCKS5 proxies (Floxy may or may not support)
  - Need to check Floxy documentation

Option 4: Stick with RDAP Only
------------------------------
Pros:
  - Already working with HTTP proxies
  - ~180/sec with 1000 proxies is achievable
  - 580M checks in 4-7 days is acceptable
Cons:
  - Not the fastest option

RECOMMENDATION
--------------
1. Check if Floxy provides SOCKS5 proxy access
2. If yes: Test WHOIS through SOCKS5
3. If no: Stick with RDAP-only approach
"""

    # Check if CONNECT worked
    connect_worked = await test_http_connect_tunnel()
    connect_status = "WORKS! Could use this." if connect_worked else "Does NOT work with current proxies"

    print(analysis.format(connect_status=connect_status))


async def main():
    await analyze_options()


if __name__ == "__main__":
    asyncio.run(main())
