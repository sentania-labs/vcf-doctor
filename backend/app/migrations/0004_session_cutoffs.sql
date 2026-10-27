-- Single sign-on sessions end for a person who is refused at a later sign-in
-- (#87). A browser session is a signed cookie, so it cannot be recalled; instead
-- every session issued to this person at or before not_before is refused.
-- not_before is Unix seconds, the unit the session cookie's issue time uses.

CREATE TABLE session_cutoffs (
    issuer     TEXT NOT NULL,
    subject    TEXT NOT NULL,
    not_before BIGINT NOT NULL,
    PRIMARY KEY (issuer, subject)
);
