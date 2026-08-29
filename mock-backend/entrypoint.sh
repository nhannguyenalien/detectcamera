#!/usr/bin/env bash
set -e
mkdir -p /data/backend
chown -R app:app /data/backend 2>/dev/null || true
exec gosu app "$@"
