#!/usr/bin/env python3
"""
Comprehensive WHOIS optimization tests:
1. Connection reuse feasibility
2. Minimal response byte patterns
3. Actual bandwidth measurement
"""

import asyncio
import time
import base64
from pathlib import Path

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

WHOIS_SERVER = "whois.verisign-grs.com"
WHOIS_PORT = 43

PROXY_FILE = Path("/Users/collinsoik/Desktop/Code_Space/Proxy Status Checker/proxies.txt")


def load_proxy() -> dict:
    with open(PROXY_FILE) as f:
        line = f.readline().strip()
        auth, hostport = line.split("@")
        user, passwd = auth.split(":")
        host, port = hostport.split(":")
        return {"host": host, "port": int(port), "user": user, "pass": passwd}


# =============================================================================
# TEST 1: CONNECTION REUSE
# =============================================================================

async def test_connection_reuse():
    """
    Test if we can send multiple WHOIS queries through a single CONNECT tunnel.
    This could save ~330 bytes per query after the first.
    """
    print("=" * 70)
    print("TEST 1: CONNECTION REUSE")
    print("=" * 70)

    proxy = load_proxy()
    domains = ["google.com", "amazon.com", "microsoft.com", "xyztest123.com", "github.com"]

    print(f"\nAttempting to send {len(domains)} queries through ONE CONNECT tunnel...")
    print("-" * 70)

    try:
        # Connect to proxy
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(proxy["host"], proxy["port"]),
            timeout=10.0
        )

        # Send CONNECT request
        auth = base64.b64encode(f"{proxy['user']}:{proxy['pass']}".encode()).decode()
        connect_req = (
            f"CONNECT {WHOIS_SERVER}:{WHOIS_PORT} HTTP/1.1\r\n"
            f"Proxy-Authorization: Basic {auth}\r\n"
            f"\r\n"
        )

        writer.write(connect_req.encode())
        await writer.drain()

        # Read CONNECT response
        response = await asyncio.wait_for(reader.readline(), timeout=10.0)
        if b"200" not in response:
            print(f"CONNECT failed: {response.decode()}")
            return False

        # Drain headers
        while (await reader.readline()) not in (b"\r\n", b"\n", b""):
            pass

        print("CONNECT tunnel established!")
        print()

        # Try sending multiple queries through the same tunnel
        results = []
        for i, domain in enumerate(domains):
            print(f"Query {i+1}: {domain}...", end=" ")

            try:
                # Send WHOIS query
                writer.write(f"{domain}\r\n".encode())
                await writer.drain()

                # Read response
                response = await asyncio.wait_for(reader.read(256), timeout=10.0)
                response_text = response.decode('utf-8', errors='ignore')

                # Determine status
                if "No match" in response_text:
                    status = "available"
                elif "Domain Name" in response_text:
                    status = "taken"
                else:
                    status = "unknown"

                results.append({"domain": domain, "status": status, "success": True})
                print(f"{status} ({len(response)} bytes)")

            except asyncio.TimeoutError:
                print("TIMEOUT - connection may have closed")
                results.append({"domain": domain, "status": "timeout", "success": False})
                break
            except Exception as e:
                print(f"ERROR: {e}")
                results.append({"domain": domain, "status": "error", "success": False})
                break

        writer.close()

        # Analysis
        print()
        print("-" * 70)
        successful = [r for r in results if r["success"]]

        if len(successful) == len(domains):
            print("✅ CONNECTION REUSE WORKS!")
            print(f"   Successfully sent {len(domains)} queries through ONE tunnel")
            print("   This saves ~330 bytes per query after the first!")
            return True
        elif len(successful) > 1:
            print(f"⚠️ PARTIAL REUSE: {len(successful)}/{len(domains)} queries succeeded")
            print("   May need to reconnect periodically")
            return True
        else:
            print("❌ CONNECTION REUSE FAILED")
            print("   WHOIS server closes connection after each query")
            return False

    except Exception as e:
        print(f"Error: {e}")
        return False


# =============================================================================
# TEST 2: MINIMAL RESPONSE PATTERNS
# =============================================================================

async def test_response_patterns():
    """
    Test how few bytes we need to read to determine taken/available.
    """
    print("\n" + "=" * 70)
    print("TEST 2: MINIMAL RESPONSE BYTE PATTERNS")
    print("=" * 70)

    proxy = load_proxy()

    # Mix of taken and available domains
    test_domains = [
        ("google.com", "taken"),
        ("amazon.com", "taken"),
        ("microsoft.com", "taken"),
        ("xyztest123456.com", "available"),
        ("randomllc99999.com", "available"),
        ("foobar777888.com", "available"),
    ]

    print("\nAnalyzing first N bytes of WHOIS responses...")
    print("-" * 70)

    byte_requirements = []

    for domain, expected in test_domains:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(proxy["host"], proxy["port"]),
                timeout=10.0
            )

            auth = base64.b64encode(f"{proxy['user']}:{proxy['pass']}".encode()).decode()
            connect_req = f"CONNECT {WHOIS_SERVER}:{WHOIS_PORT} HTTP/1.1\r\nProxy-Authorization: Basic {auth}\r\n\r\n"

            writer.write(connect_req.encode())
            await writer.drain()

            response = await reader.readline()
            while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                pass

            # Send query
            writer.write(f"{domain}\r\n".encode())
            await writer.drain()

            # Read full response for analysis
            full_response = await asyncio.wait_for(reader.read(2048), timeout=10.0)
            writer.close()

            # Find minimum bytes needed
            response_text = full_response.decode('utf-8', errors='ignore')

            # Check different byte sizes
            for check_size in [16, 20, 32, 48, 64]:
                prefix = response_text[:check_size]
                detected = None
                if "No match" in prefix:
                    detected = "available"
                elif "Domain Name" in prefix:
                    detected = "taken"

                if detected == expected:
                    byte_requirements.append({
                        "domain": domain,
                        "expected": expected,
                        "min_bytes": check_size,
                        "first_bytes": response_text[:50].replace('\n', '\\n')
                    })
                    print(f"{domain:30} [{expected:9}] → Detected in first {check_size} bytes")
                    print(f"   First 50 chars: {response_text[:50].replace(chr(10), '↵')}")
                    break
            else:
                # Couldn't detect in first 64 bytes
                byte_requirements.append({
                    "domain": domain,
                    "expected": expected,
                    "min_bytes": ">64",
                    "first_bytes": response_text[:100]
                })
                print(f"{domain:30} [{expected:9}] → Needs >64 bytes!")
                print(f"   First 100 chars: {response_text[:100]}")

        except Exception as e:
            print(f"{domain:30} ERROR: {e}")

    # Analysis
    print()
    print("-" * 70)
    min_bytes = [r["min_bytes"] for r in byte_requirements if isinstance(r["min_bytes"], int)]
    if min_bytes:
        max_needed = max(min_bytes)
        print(f"✅ Maximum bytes needed to detect status: {max_needed}")
        print(f"   Recommendation: Read first {max_needed + 16} bytes for safety margin")
    else:
        print("⚠️ Could not determine minimum bytes reliably")

    return max_needed if min_bytes else 64


# =============================================================================
# TEST 3: BANDWIDTH MEASUREMENT
# =============================================================================

async def test_bandwidth():
    """
    Measure actual data sent and received per query.
    """
    print("\n" + "=" * 70)
    print("TEST 3: BANDWIDTH MEASUREMENT")
    print("=" * 70)

    proxy = load_proxy()
    domains = [f"testdomain{i:05d}.com" for i in range(10)]

    print(f"\nMeasuring bandwidth for {len(domains)} queries...")
    print("-" * 70)

    # Calculate sizes
    auth = base64.b64encode(f"{proxy['user']}:{proxy['pass']}".encode()).decode()

    # Fixed per-connection overhead
    connect_request = f"CONNECT {WHOIS_SERVER}:{WHOIS_PORT} HTTP/1.1\r\nProxy-Authorization: Basic {auth}\r\n\r\n"
    connect_response = "HTTP/1.1 200 Connection Established\r\n\r\n"

    connect_sent = len(connect_request.encode())
    connect_recv = len(connect_response.encode())

    print(f"\nPer-Connection Overhead:")
    print(f"  CONNECT request:  {connect_sent} bytes sent")
    print(f"  CONNECT response: {connect_recv} bytes received")

    # Per-query overhead
    total_query_sent = 0
    total_query_recv = 0
    query_count = 0

    for domain in domains:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(proxy["host"], proxy["port"]),
                timeout=10.0
            )

            writer.write(connect_request.encode())
            await writer.drain()

            _ = await reader.readline()
            while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                pass

            # Query
            query = f"{domain}\r\n"
            writer.write(query.encode())
            await writer.drain()

            # Read minimal response (32 bytes as test)
            response = await asyncio.wait_for(reader.read(32), timeout=10.0)
            writer.close()

            total_query_sent += len(query.encode())
            total_query_recv += len(response)
            query_count += 1

        except Exception as e:
            print(f"Error with {domain}: {e}")

    avg_query_sent = total_query_sent / query_count if query_count else 0
    avg_query_recv = total_query_recv / query_count if query_count else 0

    print(f"\nPer-Query (with 32-byte response limit):")
    print(f"  Query sent:     {avg_query_sent:.1f} bytes avg")
    print(f"  Response recv:  {avg_query_recv:.1f} bytes avg")

    # Calculate scenarios
    print("\n" + "=" * 70)
    print("BANDWIDTH PROJECTIONS FOR 580M QUERIES")
    print("=" * 70)

    tcp_overhead = 150  # Rough estimate for TCP handshake

    scenarios = [
        ("No reuse, 32B response", connect_sent + connect_recv + tcp_overhead + avg_query_sent + 32),
        ("No reuse, 64B response", connect_sent + connect_recv + tcp_overhead + avg_query_sent + 64),
        ("No reuse, 256B response", connect_sent + connect_recv + tcp_overhead + avg_query_sent + 256),
        ("With reuse (10/conn), 32B", (connect_sent + connect_recv + tcp_overhead) / 10 + avg_query_sent + 32),
        ("With reuse (10/conn), 64B", (connect_sent + connect_recv + tcp_overhead) / 10 + avg_query_sent + 64),
    ]

    total_queries = 580_000_000

    print(f"\n{'Scenario':<35} {'Per Query':>12} {'580M Total':>15} {'Status':>10}")
    print("-" * 75)

    for name, bytes_per_query in scenarios:
        total_gb = (bytes_per_query * total_queries) / (1024**3)
        status = "✅ OK" if total_gb < 250 else "❌ OVER"
        print(f"{name:<35} {bytes_per_query:>10.0f} B {total_gb:>12.1f} GB {status:>10}")

    return scenarios


# =============================================================================
# MAIN
# =============================================================================

async def main():
    print("=" * 70)
    print("WHOIS OPTIMIZATION COMPREHENSIVE TESTS")
    print("=" * 70)

    # Test 1: Connection reuse
    reuse_works = await test_connection_reuse()

    # Test 2: Response patterns
    min_bytes = await test_response_patterns()

    # Test 3: Bandwidth
    scenarios = await test_bandwidth()

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print(f"""
Connection Reuse:     {'YES - saves ~330 bytes/query!' if reuse_works else 'NO - new connection each query'}
Min Response Bytes:   {min_bytes} bytes needed to detect status
Recommended Read:     {min_bytes + 16} bytes (with safety margin)

BANDWIDTH RECOMMENDATION:
""")

    if reuse_works:
        print("  Use connection reuse with 10 queries per tunnel")
        print("  Read only first 48 bytes of response")
        print("  Expected bandwidth: ~70-80 GB (well under 250GB limit)")
    else:
        print("  No connection reuse - need new tunnel per query")
        print(f"  Read only first {min_bytes + 16} bytes of response")
        print("  Expected bandwidth: ~250-300 GB (close to/over limit)")
        print("  Consider buying additional bandwidth or reducing query count")


if __name__ == "__main__":
    asyncio.run(main())
