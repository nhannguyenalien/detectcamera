#!/usr/bin/env bash
# Chạy như root: chuẩn bị quyền volume + seed model, rồi hạ quyền xuống 'app'.
set -e

APP_UID=$(id -u app)
APP_GID=$(id -g app)

# 1) volume model do Docker tạo -> thuộc root. Cấp quyền cho app.
mkdir -p /data/models/insightface/models
chown -R "$APP_UID:$APP_GID" /data/models 2>/dev/null || true

# 2) seed model từ bản đã bake trong image (không cần tải từ GitHub lúc chạy).
if [ ! -d "/data/models/insightface/models/${INSIGHTFACE_MODEL:-buffalo_l}" ] \
   && [ -d "/opt/models/insightface/models/${INSIGHTFACE_MODEL:-buffalo_l}" ]; then
  echo "[entrypoint] seeding model ${INSIGHTFACE_MODEL:-buffalo_l} vào volume"
  cp -r /opt/models/insightface/models/* /data/models/insightface/models/
  chown -R "$APP_UID:$APP_GID" /data/models
fi

exec gosu app "$@"
