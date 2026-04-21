FROM python:3.12-slim

WORKDIR /app

# Install cron
RUN apt-get update && apt-get install -y cron && rm -rf /var/lib/apt/lists/*

# Copy application files
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cez_monitor.py resolve_towns.py docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# Create data and logs directories for persistent storage
RUN mkdir -p /app/data /app/logs

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Ensure directories are writable
RUN chmod 755 /app/data /app/logs

# Health check - verify script exists and can run
HEALTHCHECK --interval=3600s --timeout=10s --start-period=5s --retries=1 \
    CMD python3 -c "import cez_monitor; print('OK')" || exit 1

# Entrypoint: check config and run cez_monitor
ENTRYPOINT ["./docker-entrypoint.sh"]
