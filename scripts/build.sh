#!/usr/bin/env bash
# Build 2 image local trên VM 103 để Coolify (hoặc docker compose) dùng.
set -euo pipefail
cd "$(dirname "$0")/.."
sudo docker build -t mock-backend:latest ./mock-backend
sudo docker build -t vision-api:latest   ./vision-api
echo ">> done: vision-api:latest, mock-backend:latest"
sudo docker images | grep -E "vision-api|mock-backend"
