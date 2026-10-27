-- Personal API tokens (#87). A token acts as the person who made it. Only the
-- SHA-256 of the secret is stored; the secret itself is shown once, at creation.
-- Timestamps are ISO 8601 UTC text like everywhere else.

CREATE TABLE api_tokens (
    id             TEXT PRIMARY KEY,
    token_hash     TEXT NOT NULL UNIQUE,
    hint           TEXT NOT NULL,
    name           TEXT NOT NULL,
    issuer         TEXT NOT NULL,
    subject        TEXT NOT NULL,
    owner_name     TEXT NOT NULL,
    role           TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL,
    last_used_at   TEXT,
    revoked_at     TEXT,
    revoked_reason TEXT
);
CREATE INDEX ix_api_tokens_owner ON api_tokens(issuer, subject);
