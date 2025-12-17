# High-Performance Domain Availability Checker

A massively scalable domain availability checker using the WHOIS protocol with proxy rotation. Designed to check **700M+ domains** efficiently.

## Performance

| Metric | Value |
|--------|-------|
| **Throughput** | 3,700 domains/sec |
| **Success Rate** | 100% |
| **704M domains** | ~53 hours (~2.2 days) |
| **Bandwidth** | ~157 GB total |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   DOMAIN VARIATIONS DB                       │
│                (DuckDB - 704M+ domains)                      │
└───────────────────────────┬─────────────────────────────────┘
                            │
                     ┌──────▼──────┐
                     │   DOMAIN    │
                     │   CHECKER   │
                     │             │
                     │ Batch: 10K  │
                     │ Checkpoint  │
                     └──────┬──────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
        ┌─────▼─────┐ ┌─────▼─────┐ ┌─────▼─────┐
        │  PROXY 1  │ │  PROXY 2  │ │ PROXY N   │
        │  1 conn   │ │  1 conn   │ │  1 conn   │
        └─────┬─────┘ └─────┬─────┘ └─────┬─────┘
              │             │             │
              └─────────────┼─────────────┘
                            │
                     ┌──────▼──────┐
                     │   WHOIS     │
                     │   SERVER    │
                     │ (Verisign)  │
                     └──────┬──────┘
                            │
                     ┌──────▼──────┐
                     │   RESULTS   │
                     │  (DuckDB)   │
                     └─────────────┘
```

## Key Optimizations

### 1. WHOIS over RDAP
- **1.7x faster** than RDAP (~130ms vs ~200ms latency)
- **5x less bandwidth** (64 bytes vs 400+ bytes per query)

### 2. Minimal Response Reading
- Only reads first **64 bytes** of WHOIS response
- Status detection in first bytes:
  - `"No match"` → Available
  - `"Domain Name"` → Taken

### 3. Optimal Concurrency
- **1 connection per proxy** (bottleneck analysis confirmed this is optimal)
- 1000 proxies = 1000 concurrent connections
- Higher per-proxy concurrency causes failures

### 4. Checkpoint/Resume System
- Saves progress every 100K domains (~27 seconds)
- Automatic resume on restart
- No duplicate checks

## Data Usage

| Component | Bytes/Query |
|-----------|-------------|
| CONNECT request (sent) | ~107 |
| WHOIS query (sent) | ~21 |
| CONNECT response (recv) | ~39 |
| WHOIS response (recv) | 64 |
| **Total** | **~239** |

**704M domains = ~157 GB total bandwidth**

## Installation

### Local Development (Linux/macOS)

```bash
# Clone the repo
git clone <repo-url>
cd rdap_test

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Local Development (Windows)

```powershell
# Clone the repo
git clone <repo-url>
cd rdap_test

# Create virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### Docker - Linux (Recommended for Production)

```bash
# Build the Linux image
docker build -f Dockerfile.linux -t domain-checker .

# Or use docker-compose
docker-compose up -d
```

### Docker - Windows

```powershell
# Build the Windows image (requires Windows containers mode)
docker build -f Dockerfile.windows -t domain-checker .

# Or use docker-compose
$env:DOCKERFILE="Dockerfile.windows"
docker-compose up -d
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `VARIATIONS_DB` | Path to domain variations DuckDB | Required |
| `CHECKS_DB` | Path to results DuckDB | Required |
| `PROXY_FILE` | Path to proxy list file | Required |
| `BATCH_SIZE` | Domains per batch | 10000 |
| `CHECKPOINT_INTERVAL` | Save checkpoint every N domains | 100000 |
| `MAX_PROXIES` | Limit number of proxies | All |
| `LIMIT` | Max domains to check | All |
| `RESUME` | Resume from checkpoint | true |

### Proxy File Format

```
user1:pass1@ip1:port1
user2:pass2@ip2:port2
...
```

## Usage

### Production Run (Docker)

1. Create a `data/` directory with:
   - `domain_variations.duckdb` - Your domain source database
   - `proxies.txt` - Your proxy list

2. Run with docker-compose:
```bash
docker-compose up -d
```

3. Monitor logs:
```bash
docker-compose logs -f
```

### Production Run (Local - Linux/macOS)

```bash
export VARIATIONS_DB=/path/to/domain_variations.duckdb
export CHECKS_DB=/path/to/domain_checks.duckdb
export PROXY_FILE=/path/to/proxies.txt

cd src
python domain_checker.py --run
```

### Production Run (Local - Windows)

```powershell
$env:VARIATIONS_DB="C:\data\domain_variations.duckdb"
$env:CHECKS_DB="C:\data\domain_checks.duckdb"
$env:PROXY_FILE="C:\data\proxies.txt"

cd src
python domain_checker.py --run
```

### Test Runs

```bash
cd src

# Small test (1K domains)
python domain_checker.py --test

# Checkpoint/resume test (2K domains)
python domain_checker.py --test-resume

# Larger test (100K domains)
python domain_checker.py --test-100k
```

## Project Structure

```
rdap_test/
├── src/
│   ├── whois_checker.py    # Core WHOIS checker (64-byte optimization)
│   ├── proxy_pool.py       # Proxy management with health tracking
│   ├── database.py         # DuckDB integration for results
│   └── domain_checker.py   # Main orchestrator with checkpointing
├── data/                   # Data directory (mount point for Docker)
│   ├── domain_variations.duckdb  # Source database (704M domains)
│   ├── domain_checks.duckdb      # Results database (created on run)
│   └── proxies.txt               # Proxy list file
├── tests/                  # Protocol and integration tests
├── benchmarks/             # Performance analysis scripts
├── Dockerfile.linux        # Linux container configuration
├── Dockerfile.windows      # Windows container configuration
├── docker-compose.yml      # Easy deployment (auto-selects platform)
├── .env.example            # Environment variable template
├── requirements.txt        # Python dependencies
└── README.md
```

## Database Schema

### Source Database (domain_variations.duckdb)

Expected table: `domain_variations`
```sql
domain VARCHAR  -- The domain to check (e.g., 'example.com')
```

### Results Database (domain_checks.duckdb)

Created automatically:
```sql
CREATE TABLE domain_checks (
    domain VARCHAR PRIMARY KEY,
    status VARCHAR NOT NULL,  -- 'taken', 'available', 'error', 'unknown'
    error VARCHAR,
    checked_at TIMESTAMP
);

CREATE TABLE checkpoints (
    id INTEGER PRIMARY KEY,
    last_offset BIGINT,
    total_checked BIGINT,
    updated_at TIMESTAMP
);
```

## Scaling Estimates

| Proxies | Throughput | 704M Runtime |
|---------|------------|--------------|
| 100 | 370/sec | 22 days |
| 250 | 925/sec | 8.8 days |
| 500 | 1,850/sec | 4.4 days |
| 750 | 2,775/sec | 2.9 days |
| **1000** | **3,700/sec** | **2.2 days** |
| 1500+ | 5,000/sec* | 1.6 days |

*Capped by Verisign rate limits

## Bottleneck Analysis

The system was optimized through iterative testing:

1. **Per-proxy concurrency**: Testing showed 1 connection per proxy is optimal. Higher concurrency per proxy causes connection failures.

2. **Protocol choice**: WHOIS chosen over RDAP for 1.7x speed improvement and 5x bandwidth reduction.

3. **Response reading**: Only 64 bytes needed to determine availability status.

4. **Server limits**: Verisign caps total throughput at ~5,000/sec regardless of proxy count.

## Deployment Checklist

- [ ] Copy `domain_variations.duckdb` to target machine
- [ ] Create proxy list file with valid proxies
- [ ] Set environment variables or create `.env` file
- [ ] Build Docker image or install Python dependencies
- [ ] Run initial test with `--test` flag
- [ ] Start production run with `--run` flag
- [ ] Monitor logs for errors

## Troubleshooting

### High Error Rate
- Check proxy health (proxies may be rate-limited or banned)
- Reduce `MAX_PROXIES` to use fewer, healthier proxies
- Check network connectivity

### Slow Throughput
- Increase proxy count
- Verify proxies are geographically distributed
- Check for network bottlenecks

### Resume Not Working
- Ensure `CHECKS_DB` path is consistent between runs
- Check that checkpoint table exists in results database

## License

MIT
