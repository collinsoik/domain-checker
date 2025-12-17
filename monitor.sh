#!/bin/bash
# Domain Checker Monitor - Live updating dashboard
# Usage: ./monitor.sh

# Hide cursor and setup clean exit
tput civis
trap 'tput cnorm; exit' INT TERM

# Clear screen once at start
clear

while true; do
    # Move cursor to top-left instead of clearing (smoother)
    tput cup 0 0

    # Get latest log line
    LINE=$(sg docker -c "docker logs domain-checker --tail 1" 2>&1)

    echo "╔══════════════════════════════════════════╗"
    echo "║       Domain Checker Monitor             ║"
    echo "╠══════════════════════════════════════════╣"

    if [[ $LINE == *"Batch:"* ]]; then
        # Extract metrics
        BATCH_SPEED=$(echo "$LINE" | grep -oP '\d+/sec' | head -1 | grep -oP '\d+')
        OVERALL_SPEED=$(echo "$LINE" | grep -oP 'Overall: \d+/sec' | grep -oP '\d+')
        CHECKED=$(echo "$LINE" | grep -oP 'Total: [\d,]+' | grep -oP '[\d,]+' | tr -d ',')
        TOTAL=704023090
        PCT=$(echo "$LINE" | grep -oP '\([\d.]+%\)' | tr -d '()')
        ERRORS=$(echo "$LINE" | grep -oP 'E:\d+' | grep -oP '\d+')
        CONCURRENCY=$(echo "$LINE" | grep -oP 'C:\d+' | grep -oP '\d+')

        # Calculate ETA
        if [[ -n "$BATCH_SPEED" && "$BATCH_SPEED" -gt 0 ]]; then
            REMAINING=$((TOTAL - CHECKED))
            ETA_SECS=$((REMAINING / BATCH_SPEED))
            ETA_HOURS=$((ETA_SECS / 3600))
            ETA_DAYS=$(echo "scale=1; $ETA_HOURS / 24" | bc)
        else
            ETA_DAYS="--"
            ETA_HOURS="--"
        fi

        printf "║  Batch Speed:   %-6s/sec              ║\n" "$BATCH_SPEED"
        printf "║  Overall Speed: %-6s/sec              ║\n" "$OVERALL_SPEED"
        echo "╠══════════════════════════════════════════╣"
        printf "║  Progress: %'12d / %'d   ║\n" "$CHECKED" "$TOTAL"
        printf "║  Complete: %-6s                       ║\n" "$PCT"
        echo "╠══════════════════════════════════════════╣"
        printf "║  ETA: %-5s days (%-5s hours)          ║\n" "$ETA_DAYS" "$ETA_HOURS"
        printf "║  Errors: %-4s  Concurrency: %-4s        ║\n" "$ERRORS" "$CONCURRENCY"
    else
        printf "║  %-40s ║\n" "${LINE:0:40}"
    fi

    echo "╚══════════════════════════════════════════╝"
    echo ""
    echo "  (Ctrl+C to exit)"

    # Clear any leftover lines
    tput el

    sleep 2
done
