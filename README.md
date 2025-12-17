# High-Performance Domain Availability Checker

A massively scalable domain availability checker using the WHOIS protocol with proxy rotation and **adaptive rate control**. Designed to check **704M+ .com domains** efficiently while respecting Verisign rate limits.

## Performance

| Metric | Value |
|--------|-------|
| **Sustained Throughput** | 550-650 domains/sec |
| **Success Rate** | 99.9% |
| **704M domains** | ~12-14 days |
| **Bandwidth** | ~157 GB total |

### Why Not Faster?

Verisign (the .com WHOIS authority) enforces rate limiting:
- **Single IP**: Throttled to ~50/sec with 13% timeouts
- **1000 Proxies**: Sustained ~650/sec (each IP gets its own rate limit bucket)
- **Burst vs Sustained**: Initial burst can hit 4,000/sec but drops to 600-700/sec

The adaptive rate controller automatically detects and responds to rate limiting.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   DOMAIN VARIATIONS DB                       │
│             (domain_variations.duckdb - 704M+)               │
└───────────────────────────┬─────────────────────────────────┘
                            │
                     ┌──────▼──────┐
                     │   DOMAIN    │
                     │   CHECKER   │
                     │             │
                     │ ┌─────────┐ │
                     │ │ADAPTIVE │ │
                     │ │  RATE   │ │
                     │ │CONTROL  │ │
                     │ └─────────┘ │
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
                     │ domain_checks.duckdb │
                     └─────────────┘
```

## Adaptive Rate Control System

The checker includes an intelligent rate controller that maximizes throughput while avoiding rate limit penalties.

### How It Works

```
┌─────────────────────────────────────────────────────────────┐
│                    METRICS TRACKER                           │
│  - Rolling latency window (last 100 queries)                │
│  - Timeout rate (last 1000 queries)                         │
│  - Real-time throughput calculation                         │
└───────────────────────────┬─────────────────────────────────┘
                            │
                     ┌──────▼──────┐
                     │  ADAPTIVE   │
                     │ CONTROLLER  │
                     │             │
                     │ Evaluates:  │
                     │ - Latency   │
                     │ - Timeouts  │
                     │ - Throughput│
                     └──────┬──────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
         INCREASE      DECREASE       PAUSE
         (+10%)        (-20%)      (30s reset)
```

### Rate Control Thresholds

| Signal | Threshold | Action |
|--------|-----------|--------|
| Low latency | Avg < 120ms | Increase concurrency 10% |
| High latency | Avg > 200ms | Decrease concurrency 20% |
| Critical latency | P95 > 500ms | Severe decrease 50% |
| Warning timeout | > 1% | Decrease concurrency 20% |
| High timeout | > 2% | Severe decrease 50% |
| Critical timeout | > 5% | Pause 30s, reset to 50% |

### Controller States

- **RAMPING_UP**: Increasing concurrency (good conditions)
- **STABLE**: Maintaining current rate
- **BACKING_OFF**: Decreasing concurrency (rate limiting detected)
- **PAUSED**: Temporarily stopped due to severe throttling

## Key Optimizations

### 1. WHOIS over RDAP
- **5x faster** than RDAP (~100ms vs ~500ms latency)
- Tested both protocols - WHOIS clearly superior for bulk checking

### 2. Minimal Response Reading
- Only reads first **64 bytes** of WHOIS response
- Status detection in first bytes:
  - `"No match"` → Available
  - `"Domain Name"` → Taken

### 3. Optimal Concurrency
- **1 connection per proxy** (bottleneck analysis confirmed)
- Adaptive controller adjusts between 50-800 concurrent connections
- Default: 500 initial, scales based on conditions

### 4. Optimized Database Writes
- **Bulk insert with temp table + upsert** (2.6x faster than row-by-row)
- Transactional writes for data integrity
- DuckDB for high-performance columnar storage

### 5. Checkpoint/Resume System
- Saves progress every 100K domains
- Automatic resume on restart
- No duplicate checks

## Data Files

| File | Description | Size |
|------|-------------|------|
| `domain_variations.duckdb` | Source database with 704M+ domains | ~15 GB |
| `domain_checks.duckdb` | Results database (created on run) | ~20 GB (estimated final) |
| `proxies.txt` | Proxy list file | ~50 KB |

**Note**: Database files are not included in the repository due to size. The results database is created automatically on first run.

## Installation

### Docker (Recommended)

```bash
# Clone the repo
git clone <repo-url>
cd domain-checker

# Build and run
docker compose up -d

# Monitor progress
./monitor.sh 15
```

### Local Development

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# or: .\venv\Scripts\Activate.ps1  # Windows

# Install dependencies
pip install -r requirements.txt
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

### Adaptive Rate Control Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `RATE_INITIAL_CONCURRENCY` | Starting concurrency | 500 |
| `RATE_MAX_CONCURRENCY` | Maximum allowed concurrency | 800 |
| `RATE_MIN_CONCURRENCY` | Minimum allowed concurrency | 50 |
| `RATE_LATENCY_LOW_MS` | Latency below which to increase | 120 |
| `RATE_LATENCY_HIGH_MS` | Latency above which to decrease | 200 |
| `RATE_TIMEOUT_HIGH` | Timeout rate triggering decrease | 0.02 (2%) |

### Proxy File Format

```
user1:pass1@ip1:port1
user2:pass2@ip2:port2
...
```

## Usage

### Production Run (Docker)

```bash
# Start the checker
docker compose up -d

# Monitor progress
./monitor.sh 15

# View logs
docker logs -f domain-checker

# Stop gracefully
docker compose down
```

### Production Run (Local)

```bash
export VARIATIONS_DB=/path/to/domain_variations.duckdb
export CHECKS_DB=/path/to/domain_checks.duckdb
export PROXY_FILE=/path/to/proxies.txt

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

### Benchmarking

```bash
cd src

# Benchmark with timing breakdown
python benchmark_timing.py --domains 1000 --proxies 100 --concurrency 100

# Single proxy baseline test
python benchmark_timing.py --single
```

## Project Structure

```
domain-checker/
├── src/
│   ├── domain_checker.py   # Main orchestrator with adaptive control
│   ├── whois_checker.py    # Core WHOIS checker (64-byte optimization)
│   ├── proxy_pool.py       # Proxy management with health tracking
│   ├── database.py         # DuckDB integration (optimized bulk writes)
│   ├── metrics.py          # Rolling metrics tracker for rate control
│   ├── rate_controller.py  # Adaptive concurrency controller
│   └── benchmark_timing.py # Performance benchmarking tool
├── data/
│   ├── domain_variations.duckdb  # Source database (not in repo)
│   ├── domain_checks.duckdb      # Results database (created on run)
│   └── proxies.txt               # Proxy list (not in repo)
├── monitor.sh              # Progress monitoring script
├── Dockerfile.linux        # Linux container configuration
├── Dockerfile.windows      # Windows container configuration
├── docker-compose.yml      # Docker deployment configuration
├── requirements.txt        # Python dependencies
└── README.md
```

## Database Schema

### Source Database (domain_variations.duckdb)

Expected table: `domain_variations`
```sql
CREATE TABLE domain_variations (
    domain VARCHAR PRIMARY KEY  -- e.g., 'example.com'
);
```

### Results Database (domain_checks.duckdb)

Created automatically:
```sql
CREATE TABLE domain_checks (
    domain VARCHAR PRIMARY KEY,
    status VARCHAR NOT NULL,  -- 'taken', 'available', 'error', 'unknown'
    error VARCHAR,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE checkpoints (
    id INTEGER PRIMARY KEY,
    last_offset BIGINT,
    total_checked BIGINT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_status ON domain_checks(status);
```

## Performance Tuning

### Tested Configurations

| Concurrency | Throughput | Notes |
|-------------|------------|-------|
| 300 | 586/sec | Conservative, very stable |
| 500 | 651/sec | **Recommended** - best balance |
| 800 | 668/sec | Marginal gain, more resource usage |

### Direct vs Proxy Performance

| Setup | Burst | Sustained | Timeouts |
|-------|-------|-----------|----------|
| Direct (no proxy) | 2,677/sec | 54/sec | 13% |
| 1000 Proxies | 800/sec | 650/sec | 0% |

Proxies provide **12x better sustained throughput** by distributing queries across multiple IP addresses.

## Monitoring

### Progress Monitor Script

```bash
# Sample for 15 seconds and show stats
./monitor.sh 15

# Output:
# ============================================
#   DOMAIN CHECKER PROGRESS
# ============================================
#   Checked:    294,180 / 704,023,090 (.0417%)
#   Available:  280,619
#   Taken:      13,560
# --------------------------------------------
#   Rate:       610.0 domains/sec
#   ETA:        320.4 hours (13.35 days)
# ============================================
```

### Docker Commands

```bash
# Check container status
docker ps

# View real-time logs
docker logs -f domain-checker

# Resource usage
docker stats domain-checker

# Restart after config change
docker compose down && docker compose up -d
```

## Troubleshooting

### Rate Limiting Detected

If you see `[Concurrency adjusted: X -> Y]` messages:
- The adaptive controller is working correctly
- Concurrency will recover when conditions improve
- If stuck at minimum, check proxy health

### High Timeout Rate

- Check proxy connectivity
- Reduce `RATE_INITIAL_CONCURRENCY`
- Verify proxies aren't IP-banned

### Slow Throughput

- Increase proxy count (more IPs = more rate limit budget)
- Check network latency to proxies
- Ensure proxies are geographically distributed

### Resume Not Working

- Ensure `CHECKS_DB` path is consistent between runs
- Check that checkpoint table exists
- Verify database isn't corrupted

### Database Locked Error

- Only one process can write to DuckDB at a time
- Stop any other checkers before starting
- The monitor script copies the DB to avoid locks

## Runtime Projections

| Throughput | 704M Runtime | Use Case |
|------------|--------------|----------|
| 400/sec | 20.4 days | Conservative/unstable proxies |
| 550/sec | 14.8 days | Typical sustained |
| 650/sec | 12.5 days | Optimal conditions |
| 1000/sec | 8.1 days | Multi-pool setup (future) |

## Future Improvements

- **Multi-pool support**: Run multiple independent proxy pools for higher aggregate throughput
- **Distributed checking**: Spread across multiple machines
- **Result streaming**: Export available domains in real-time

## License

MIT
