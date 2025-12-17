FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Create data directory for mounted volumes
RUN mkdir -p /data

# Default environment variables
ENV VARIATIONS_DB=/data/domain_variations.duckdb
ENV CHECKS_DB=/data/domain_checks.duckdb
ENV PROXY_FILE=/data/proxies.txt
ENV RESUME=true
ENV BATCH_SIZE=10000
ENV CHECKPOINT_INTERVAL=100000

# Run the domain checker
WORKDIR /app/src
CMD ["python", "domain_checker.py", "--run"]
