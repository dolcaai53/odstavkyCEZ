#!/bin/bash
# Docker entrypoint script for cez-monitor

set -e

# Ensure data and logs directories exist
mkdir -p "${DATA_DIR:-.}" "${LOG_PATH%/*}"

# Check if config.yaml exists
if [ ! -f "${DATA_DIR:-./data}/config.yaml" ]; then
    echo "ERROR: Config file not found at ${DATA_DIR:-./data}/config.yaml"
    echo "Please create it from config.example.yaml"
    exit 1
fi

# Run the application
exec python3 cez_monitor.py
