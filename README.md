# Domain Availability Checker

High-performance domain availability checker using WHOIS protocol with proxy rotation. Designed to check 580M+ domain variations efficiently.

## Performance

| Metric | Value |
|--------|-------|
| Throughput | **3,700 domains/sec** |
| Success Rate | 100% |
| 580M domains | ~44 hours (~1.8 days) |
| Bandwidth | ~130 GB |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     DOMAIN VARIATIONS DB                     │
│              (580M domains from LLC name generator)          │
└───────────────────────────┬─────────────────────────────────┘
                            │
                     ┌──────▼──────┐
                     │   DOMAIN    │
                     │  CHECKER    │
                     │             │
                     │ 1000 proxies│
                     │ 1 conn each │
                     └──────┬──────┘
                            │
                     ┌──────▼──────┐
                     │   WHOIS     │
                     │  CHECKER    │
                     │             │
                     │ 64B response│
                     │ Retry logic │
                     └──────┬──────┘
                            │
                     ┌──────▼──────┐
                     │   RESULTS   │
                     │   (SQLite)  │
                     └─────────────┘
```

## Key Optimizations

### 1. WHOIS over RDAP
- **1.7x faster** than RDAP (~130ms vs ~200ms latency)
- **5x less bandwidth** (64 bytes vs 400+ bytes per query)

### 2. Minimal Response Reading
- Only reads first **64 bytes** of WHOIS response
- Status detection in first 16 bytes:
  - `"No match"` → Available
  - `"Domain Name"` → Taken

### 3. Optimal Concurrency
- **1 connection per proxy** (bottleneck analysis revealed per-proxy limits)
- 1000 proxies = 1000 concurrent connections
- Higher per-proxy concurrency causes failures

### 4. Proxy Pool with Health Tracking
- Automatic proxy rotation
- Health-based proxy selection
- Retry with different proxy on failure

## Installation

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/domain-checker.git
cd domain-checker

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install httpx uvloop
```

## Usage

### Quick Test (100 domains, 1 proxy)
```bash
cd src
python whois_checker.py --test 100
```

### Multi-Proxy Test (1000 domains, 50 proxies)
```bash
python whois_checker.py --test 1000 --proxies 50
```

### Database Integration Test
```bash
python domain_checker.py --test
```

### Checkpoint/Resume Test
```bash
python domain_checker.py --test-resume
```

### Full Scale Test (100K domains)
```bash
python domain_checker.py --test-100k
```

## Configuration

Create a proxy file at the path specified in the code (or modify `PROXY_FILE` path):

```
user1:pass1@ip1:port1
user2:pass2@ip2:port2
...
```

## Project Structure

```
domain-checker/
├── src/
│   ├── whois_checker.py    # Core WHOIS checker with optimizations
│   ├── proxy_pool.py       # Proxy management with health tracking
│   ├── database.py         # SQLite integration for results
│   └── domain_checker.py   # Main orchestrator with checkpointing
├── tests/
│   ├── test_rdap.py        # Basic RDAP tests
│   ├── test_whois.py       # Basic WHOIS tests
│   └── ...                 # Protocol comparison tests
├── benchmarks/
│   ├── bottleneck_analysis.py   # Find optimal concurrency
│   ├── compare_protocols.py     # RDAP vs WHOIS comparison
│   └── ...                      # Performance benchmarks
└── README.md
```

## Bottleneck Analysis

Found that **per-proxy concurrency** is the limiting factor:

| Proxies | Per-Proxy | Throughput | Success |
|---------|-----------|------------|---------|
| 50 | 10 | 175/sec | 81.7% |
| 500 | 1 | **2,947/sec** | **100%** |

Optimal: Use all proxies with 1 connection each.

## Protocol Comparison

| Protocol | Latency | Bandwidth/query | Connection Reuse |
|----------|---------|-----------------|------------------|
| WHOIS | ~130ms | 64 bytes | No (server closes) |
| RDAP | ~200ms | 400+ bytes | No |

WHOIS was chosen for production due to lower latency and bandwidth.

## License

MIT
