-- Service accounts (#87): machine identities that belong to no person. Their
-- tokens live in api_tokens beside personal ones, so hashing, expiry and
-- revocation are one mechanism. A token has exactly one owner: a person
-- (issuer, subject, owner_name) or a service account.
-- Timestamps are ISO 8601 UTC text like everywhere else.

CREATE TABLE service_accounts (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    disabled_at TEXT,
    disabled_by TEXT
);

ALTER TABLE api_tokens ALTER COLUMN issuer DROP NOT NULL;
ALTER TABLE api_tokens ALTER COLUMN subject DROP NOT NULL;
ALTER TABLE api_tokens ALTER COLUMN owner_name DROP NOT NULL;
ALTER TABLE api_tokens ADD COLUMN service_account_id TEXT REFERENCES service_accounts(id);
ALTER TABLE api_tokens ADD CONSTRAINT api_tokens_one_owner CHECK (
    (service_account_id IS NULL AND issuer IS NOT NULL
        AND subject IS NOT NULL AND owner_name IS NOT NULL)
    OR (service_account_id IS NOT NULL AND issuer IS NULL
        AND subject IS NULL AND owner_name IS NULL)
);
CREATE INDEX ix_api_tokens_account ON api_tokens(service_account_id);
