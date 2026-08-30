-- Neon (Postgres). Chạy 1 lần:  psql "$DATABASE_URL" -f schema.sql
CREATE EXTENSION IF NOT EXISTS vector;

-- tenant = "client". owner_email dùng để client login (Cloudflare Access) tự thấy tenant của mình.
CREATE TABLE IF NOT EXISTS tenants (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  owner_email TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_tenants_email ON tenants (lower(owner_email));

-- user đăng nhập dashboard bằng email + password (thay Cloudflare Access)
CREATE TABLE IF NOT EXISTS users (
  email         TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL,          -- pbkdf2$iter$saltB64$hashB64
  role          TEXT NOT NULL DEFAULT 'client',   -- 'admin' | 'client'
  tenant_id     TEXT REFERENCES tenants(id) ON DELETE SET NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login_at TIMESTAMPTZ
);

-- token API vision-api sẽ đọc qua GET /internal/api-tokens
CREATE TABLE IF NOT EXISTS api_tokens (
  token        TEXT PRIMARY KEY,          -- 'tok_' + 40 hex
  tenant_id    TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  role         TEXT NOT NULL DEFAULT 'client',   -- 'client' | 'admin'
  label        TEXT,
  revoked      BOOLEAN NOT NULL DEFAULT false,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_tokens_tenant ON api_tokens (tenant_id) WHERE NOT revoked;

CREATE TABLE IF NOT EXISTS products (
  id         TEXT PRIMARY KEY,
  tenant_id  TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  sku        TEXT,
  name       TEXT NOT NULL,
  meta       JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_products_tenant ON products (tenant_id);
CREATE INDEX IF NOT EXISTS ix_products_sku ON products (tenant_id, sku);

CREATE TABLE IF NOT EXISTS product_embeddings (
  id         TEXT PRIMARY KEY,
  product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  tenant_id  TEXT NOT NULL,
  vec        vector(384) NOT NULL,          -- DINOv2-S, đã L2-normalize
  note       TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_pemb_product ON product_embeddings (product_id);
CREATE INDEX IF NOT EXISTS ix_pemb_tenant ON product_embeddings (tenant_id);

-- vision-api POST vào đây mỗi lần search -> nguồn tính usage
CREATE TABLE IF NOT EXISTS events (
  id         BIGSERIAL PRIMARY KEY,
  tenant_id  TEXT,
  kind       TEXT,                          -- 'product_search' | 'face_search'
  payload    JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_events_tenant ON events (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_events_kind ON events (kind, created_at DESC);

-- seed demo
INSERT INTO tenants (id, name) VALUES ('t_demo', 'Demo') ON CONFLICT (id) DO NOTHING;
