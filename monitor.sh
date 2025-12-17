#!/bin/bash
# Domain checker progress monitor

INTERVAL=${1:-10}  # Default 10 second sample

echo "Sampling for ${INTERVAL}s..."

# First sample
cp data/domain_checks.duckdb /tmp/checks_copy.duckdb 2>/dev/null
cp data/domain_checks.duckdb.wal /tmp/checks_copy.duckdb.wal 2>/dev/null
COUNT1=$(sg docker -c "docker run --rm -v /tmp:/tmp domain-checker-domain-checker python -c \"
import duckdb
conn = duckdb.connect('/tmp/checks_copy.duckdb', read_only=True)
print(conn.execute('SELECT COUNT(*) FROM domain_checks').fetchone()[0])
\"" 2>/dev/null)

sleep $INTERVAL

# Second sample
cp data/domain_checks.duckdb /tmp/checks_copy.duckdb 2>/dev/null
cp data/domain_checks.duckdb.wal /tmp/checks_copy.duckdb.wal 2>/dev/null
COUNT2=$(sg docker -c "docker run --rm -v /tmp:/tmp domain-checker-domain-checker python -c \"
import duckdb
conn = duckdb.connect('/tmp/checks_copy.duckdb', read_only=True)
print(conn.execute('SELECT COUNT(*) FROM domain_checks').fetchone()[0])
\"" 2>/dev/null)

TOTAL=704023090

# Calculate rate
DIFF=$((COUNT2 - COUNT1))
RATE=$(echo "scale=1; $DIFF / $INTERVAL" | bc)
REMAINING=$((TOTAL - COUNT2))
ETA_SECS=$(echo "scale=0; $REMAINING / ($DIFF / $INTERVAL)" | bc 2>/dev/null)
ETA_HOURS=$(echo "scale=1; $ETA_SECS / 3600" | bc 2>/dev/null)
ETA_DAYS=$(echo "scale=2; $ETA_HOURS / 24" | bc 2>/dev/null)
PCT=$(echo "scale=4; $COUNT2 * 100 / $TOTAL" | bc)

# Get status breakdown
STATS=$(sg docker -c "docker run --rm -v /tmp:/tmp domain-checker-domain-checker python -c \"
import duckdb
conn = duckdb.connect('/tmp/checks_copy.duckdb', read_only=True)
avail = conn.execute(\\\"SELECT COUNT(*) FROM domain_checks WHERE status='available'\\\").fetchone()[0]
taken = conn.execute(\\\"SELECT COUNT(*) FROM domain_checks WHERE status='taken'\\\").fetchone()[0]
print(f'{avail},{taken}')
\"" 2>/dev/null)

AVAIL=$(echo $STATS | cut -d',' -f1)
TAKEN=$(echo $STATS | cut -d',' -f2)

echo ""
echo "============================================"
echo "  DOMAIN CHECKER PROGRESS"
echo "============================================"
printf "  Checked:    %'d / %'d (${PCT}%%)\n" $COUNT2 $TOTAL
printf "  Available:  %'d\n" $AVAIL
printf "  Taken:      %'d\n" $TAKEN
echo "--------------------------------------------"
printf "  Rate:       %s domains/sec\n" $RATE
printf "  ETA:        %.1f hours (%.2f days)\n" $ETA_HOURS $ETA_DAYS
echo "============================================"
echo ""
echo "Target: 2,700-4,000/sec for 2-3 day completion"
