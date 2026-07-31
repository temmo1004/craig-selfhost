#!/bin/bash
# 肆方自架版入口：env → install.config → install.sh（起 pg/redis/pm2）→ 搬運器 → 常駐
set -e
cat > /app/install.config <<CFG
DISCORD_BOT_TOKEN=${CRAIG_BOT_TOKEN}
DISCORD_APP_ID=${CRAIG_APP_ID}
CLIENT_ID=${CRAIG_APP_ID}
CLIENT_SECRET=${CRAIG_CLIENT_SECRET}
DEVELOPMENT_GUILD_ID=${CRAIG_GUILD_ID}
PATREON_CLIENT_ID=test
PATREON_CLIENT_SECRET=test
PATRON_TIER_MAP={\"00001\":2,\"0002\":3}
PATREON_WEBHOOK_SECRET=test
GOOGLE_CLIENT_ID=test
GOOGLE_CLIENT_SECRET=test
MICROSOFT_CLIENT_ID=test
MICROSOFT_CLIENT_SECRET=test
DROPBOX_CLIENT_ID=test
DROPBOX_CLIENT_SECRET=test
APP_URI=http://localhost:3000
JWT_SECRET=${CRAIG_JWT_SECRET:-sifang-jwt}
API_PORT=5029
API_HOST=0.0.0.0
API_HOMEPAGE=${CRAIG_PUBLIC_URL:-http://localhost:5029}
ENNUIZEL_BASE=https://ez.craig.horse/
TRUST_PROXY=true
SENTRY_SAMPLE_RATE=1.0
SENTRY_SAMPLE_RATE_API=1.0
SERVER_NAME=sifang
REDIS_HOST=
REDIS_PORT=
NODE_VERSION="18.18.2"
DATABASE_NAME="craig"
POSTGRESQL_USER="craig"
POSTGRESQL_PASSWORD="craig"
POSTGRESQL_START_TIMEOUT_S=30
REDIS_START_TIMEOUT_S=30
DATABASE_URL=\"postgresql://craig:craig@localhost:5432/craig?schema=public\"
CFG
# /var/lib/postgresql 掛了持久卷：第一次是空的，要在卷上重建 cluster
mkdir -p /var/lib/postgresql
chown -R postgres:postgres /var/lib/postgresql
PGVER=$(ls /etc/postgresql 2>/dev/null | head -1)
if [ -n "$PGVER" ] && [ ! -f "/var/lib/postgresql/$PGVER/main/PG_VERSION" ]; then
  pg_dropcluster --stop "$PGVER" main 2>/dev/null || true
  pg_createcluster "$PGVER" main
fi
/etc/init.d/postgresql start || true
service redis-server start || redis-server --daemonize yes || true
for i in $(seq 1 30); do pg_isready -q && break; sleep 1; done
su postgres -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='craig'\"" | grep -q 1 || \
  su postgres -c "psql -c \"CREATE USER craig WITH PASSWORD 'craig' CREATEDB;\""
su postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='craig'\"" | grep -q 1 || \
  su postgres -c "createdb -O craig craig"
/app/install.sh
python3 /app/relay.py &
sleep infinity
