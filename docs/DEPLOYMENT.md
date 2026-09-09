# Deployment

This repository is **not** responsible for deployment. It publishes a
container image; whoever deploys it (in the lab, the deployment repository
and Argo CD) owns everything else. Nothing about a specific vCenter belongs
in a manifest: connections, schedules, retention and assistant settings are
application state set through the GUI and stored in PostgreSQL.

The console needs a PostgreSQL database. It is the only supported one; there
is no SQLite mode and no file-backed fallback. The database connection is a
deployment binding, so it is set here and never in Settings, which reports
only whether the database is reachable.

## Contract

| Item | Value |
|---|---|
| Image | `ghcr.io/sentania-labs/vcf-doctor:<tag>` where tag is `vX.Y.Z` (release), `sha-<7>` or `latest` |
| Port | `8000` (HTTP) |
| Liveness | `GET /api/health/live` (and `GET /api/health`, the same answer under the older name), 200 whenever the process is answering. The container's `HEALTHCHECK` uses this. |
| Readiness | `GET /api/health/ready`, 200 when the database is reachable and migrated, 503 otherwise. |
| Build identity | `GET /api/version` returns the [build identity fields](../backend/app/_version.py); `GET /api/health` reports the same version |
| Database | PostgreSQL 14 or newer, reached over `VCF_DOCTOR_DATABASE_URL`. Schema migrations are applied at startup and by `python3 -m app.migrate upgrade`. |
| Database password | A file, never an environment variable. `VCF_DOCTOR_DB_PASSWORD_FILE`, default `/run/secrets/vcf-doctor-db-password`. |
| Persistent volume | `/data`, holding only the generated encryption key file. A deployment that sets `VCF_DOCTOR_SECRET_KEY` needs no volume at all. |
| Replicas | More than one is supported. PostgreSQL owns concurrency, and one worker takes an advisory lock that makes it the only one running scheduled scans. |
| User | runs as uid `10001`; set `fsGroup: 10001` so the volume is writable |

Every published digest first passes the checks, repository scan, image scan and
container smoke test. An ordinary push to `main` publishes only the
`sha-<7>` image tag and reports `main-<7>` as its running version. It does not
move `latest`, mint a version tag or create a GitHub release.

A pushed `vX.Y.Z` tag is the release trigger. CI refuses a lightweight or
malformed tag, a tag that belongs to another commit, or a tagged commit that
is not reachable from `main`. The
same validation path builds the image with the tag as its running version. Once
the tested digest is proven unchanged, CI publishes `vX.Y.Z`, verifies its digest,
signs it and creates the GitHub release. Only main builds own `sha-<7>` tags.
A separate serialized promotion selects the highest version among completed GitHub releases,
copies the verified and signed digest recorded in its release notes to `latest`,
and verifies that alias while holding the promotion lock. Registry tags without
a completed release are ineligible, as are draft and prerelease records. Older release retries cannot roll it backwards;
queued promotions can be replaced because each reconciles all completed releases.
Full reruns verify and reuse an existing completed release digest before any
version-tag write, even if the rebuilt image has a different build date. A
version tag that disagrees with its release record fails publication and promotion.
Only alias promotion shares a concurrency group; builds and releases stay per-ref.

`make image` uses `dev`, the current checkout SHA, and the current UTC time. A backend run directly from a checkout reports `dev`, its
checkout SHA, and an unknown build time because there was no image build.

## Releasing

Follow [Cut a release](../CONTRIBUTING.md#cut-a-release) for the annotated-tag
procedure and version pinning guidance.

## Liveness and readiness

They are different questions and they lead to opposite actions, so they are
answered separately.

**Liveness** is whether the process is alive. `GET /api/health/live` touches
nothing external and stays 200 while PostgreSQL is unreachable. Restarting a
console whose database is down fixes nothing and a restart loop makes the
outage worse, so nothing should restart on the database.

**Readiness** is whether this instance can serve. `GET /api/health/ready` is
503 while the database is unreachable or a migration is pending. Sign-in and
every page behind it need the database, so an instance that cannot reach it is
one to take out of rotation, not one to send visitors to.

`GET /api/health` is the older name and still answers the older question,
liveness, unchanged. A manifest that has not been repointed yet keeps behaving
as it does today rather than restart-looping through a database outage. Point
the probes at the two specific paths; the old name is compatibility, not a
third answer.

```yaml
        livenessProbe:
          httpGet: { path: /api/health/live, port: 8000 }
          timeoutSeconds: 5
        readinessProbe:
          httpGet: { path: /api/health/ready, port: 8000 }
          timeoutSeconds: 5
```

`timeoutSeconds: 5` because readiness still answers while the database is
unreachable, in about three seconds, rather than stalling on the ten-second
connection pool timeout. Liveness reads nothing itself, and the
forwarded-headers middleware ahead of it holds the trusted-proxies setting for
a few seconds rather than looking it up per request, so an outage costs one
lookup every few seconds and not one per probe.

Both are public: they need no session, and they are the only endpoints that
stay useful during a database outage.

## The database

### Schema migrations

The schema lives in numbered `.sql` files under
[`backend/app/migrations`](../backend/app/migrations), applied in order and
recorded in a `schema_migrations` table. Two things apply them, and both take
the same PostgreSQL advisory lock, so several workers or pods starting together
migrate once rather than racing:

- the console itself, at startup;
- `python3 -m app.migrate upgrade`, which is the one-shot `migrate` service in
  `docker-compose.yml` and is the same thing a Kubernetes `Job` or an
  `initContainer` should run.

`python3 -m app.migrate status` prints what is applied and what is pending. A
reachable database with a pending migration reports as unhealthy on
`GET /api/health/ready` and on the Settings database panel, because a server
missing its tables is not a working database.

Adding the next migration is dropping in `0002_<what_it_does>.sql`. Nothing
else is registered and no shipped file is ever edited.

### The password

No supported path carries the database password in an environment variable.
`VCF_DOCTOR_DATABASE_URL` must not contain one; a URL that does is refused at
startup with a message naming the file to use instead. The password is read
from `VCF_DOCTOR_DB_PASSWORD_FILE`, a path that is a mounted file in one shape
and a mounted Kubernetes Secret in the other, so the application does the same
thing in both. No file means no password is sent, which is what a
trust-authenticated local server wants.

Give the file to the console's uid and nobody else. `defaultMode: 0440` with
`fsGroup: 10001` leaves it owned by root with group `10001`, readable by the
console and by no other process in the pod. The PostgreSQL side gets its own
copy, owned by its own uid; one shared file would have to be world readable,
because the two run as different users.

```yaml
# Kubernetes: the Secret arrives at the same path compose mounts.
      securityContext:
        fsGroup: 10001
      containers:
        - name: vcf-doctor
          env:
            - name: VCF_DOCTOR_DATABASE_URL
              value: postgresql://vcf_doctor@vcf-doctor-db:5432/vcf_doctor
            - name: VCF_DOCTOR_DB_PASSWORD_FILE
              value: /run/secrets/vcf-doctor-db-password
          volumeMounts:
            - name: db-password
              mountPath: /run/secrets
              readOnly: true
      volumes:
        - name: db-password
          secret:
            secretName: vcf-doctor-db
            defaultMode: 0440
            items: [{ key: password, path: vcf-doctor-db-password }]
```

### Standalone and docker

`docker-compose.yml` in this repository is self-contained: `docker compose up`
brings up `postgres:16` on a named volume, generates a database password into a
second volume, applies the migrations in the one-shot `migrate` service, and
starts the console with two uvicorn workers. There is no
external dependency to install first. The password is written twice, once for
each reader, each copy mode `0400` and owned by the uid that reads it.

### Kubernetes, single pod

One PostgreSQL pod with one PVC. In the sentania lab that is Longhorn with
best-effort locality and two replicas: in-cluster, node-survivable (the volume
reattaches when the pod is rescheduled), roughly a minute of downtime on node
loss, and no read replicas. The console is unchanged; it only takes a
`VCF_DOCTOR_DATABASE_URL`.

This is where a lab starts. It is enough for a single-estate console and it is
one object to reason about.

### Kubernetes, HA with CloudNativePG

A CloudNativePG `Cluster`: a primary and standbys spread across nodes, streaming
replication, automated failover, and in-cluster WAL archiving for
point-in-time recovery. Each instance can sit on fast local or single-replica
storage, because the redundancy is in PostgreSQL rather than in the block layer.
Synchronous block replication under a database pays a cross-node fsync on every
commit; streaming replication does not.

Nothing in the application changes between the two shapes. Point
`VCF_DOCTOR_DATABASE_URL` at the CloudNativePG read-write service, mount its
generated Secret at the password path above, and restart. Pooled connections
that break during a failover are checked on the way out of the pool, so a
failover costs a retry rather than an error.

### Upgrading a lab that is still on SQLite

The old volume's `vcf-doctor.db` is imported once, into a database that has no
history yet:

```bash
python3 -m app.migrate upgrade
python3 -m app.import_sqlite --path /data/vcf-doctor.db
```

Connections, schedules, scan runs, snapshots, findings, changes, events and
capture state all come across. Integer flags become real booleans, and a
snapshot still held as JSON text by a pre-gzip build is compressed on the way.
The old deployment's settings win, so its operator password, retention policy
and health score weights survive; the encryption key must come across too, or
the vCenter passwords need re-entering exactly as they would after any key loss.

It refuses a target that already holds history, so a second accidental run
cannot double one. `--force` overrides that; it adds rows to what is there.
Nothing writes back to the SQLite file, so the old volume stays a rollback
option until you delete it.

## Environment variables

All optional. Anything an operator would change day to day has a GUI
control in Settings; these only set deployment-time defaults or override
them.

| Variable | Default | Purpose |
|---|---|---|
| `VCF_DOCTOR_DATABASE_URL` | `postgresql://vcf_doctor@postgres:5432/vcf_doctor` | PostgreSQL connection, without a password. `DATABASE_URL` is read when this is unset. A URL carrying a password is refused. |
| `VCF_DOCTOR_DB_PASSWORD_FILE` | `/run/secrets/vcf-doctor-db-password` | File holding the database password. Missing file means none is sent. |
| `VCF_DOCTOR_DB_POOL_MAX_SIZE` | `10` | Pooled connections per worker process. A scan holds one for its whole run, so keep this above the number of vCenters that can scan at once, and multiply by the worker count when sizing the server's `max_connections`. |
| `VCF_DOCTOR_DB_POOL_MIN_SIZE` | `1` | Connections kept open per worker process |
| `VCF_DOCTOR_DB_POOL_TIMEOUT` | `10` | Seconds a request waits for a free pooled connection |
| `VCF_DOCTOR_DATA_DIR` | `/data` | Writable directory for the generated encryption key file. Nothing else is written there. |
| `VCF_DOCTOR_SECRET_KEY` | unset | Key for encrypting vCenter passwords and the Anthropic key at rest. Unset: a key file is generated in `VCF_DOCTOR_DATA_DIR`. See [Security](SECURITY.md). |
| `VCF_DOCTOR_SECRET_KEY_PREVIOUS` | unset | Previous encryption key for startup rotation. See [rotating the encryption key](#rotating-the-encryption-key) for the procedure in each deployment shape. |
| `ANTHROPIC_API_KEY` | unset | Enables the Claude assistant. A key entered in Settings takes precedence. |
| `VCF_DOCTOR_AUTH` | `on` | `off` disables the login page (use only behind ingress authentication) |
| `VCF_DOCTOR_ADMIN_PASSWORD` | unset | Seeds the operator password on first boot; otherwise the UI asks on first visit |
| `VCF_DOCTOR_TRUSTED_PROXIES` | unset (trust nobody) | Comma-separated IPs or CIDRs (the ingress) whose `X-Forwarded-For` and `X-Forwarded-Proto` are believed. Overrides the Settings page value. Set it to the ingress pod network so each visitor gets their own login lockout instead of sharing the ingress's. |
| `VCF_DOCTOR_LLM_MODEL` | `claude-opus-5` | Default assistant model; changeable in Settings |
| `VCF_DOCTOR_RETENTION_RECENT_DAYS` | `14` | Default retention tier: every scheduled snapshot younger than this is kept; changeable in Settings |
| `VCF_DOCTOR_RETENTION_HOURLY_DAYS` | `30` | Between recent and this age, one scheduled snapshot per hour is kept |
| `VCF_DOCTOR_RETENTION_DAILY_DAYS` | `365` | Between hourly and this age, one per day is kept; older scheduled snapshots and change-log rows are pruned. Manual snapshots are never pruned. (`VCF_DOCTOR_DEFAULT_RETENTION`, the old snapshot count, is ignored.) |
| `VCF_DOCTOR_RETENTION_TIMEZONE` | `TZ`, then `UTC` | Seeds the daily tier and Snapshots grouping timezone when no saved policy exists; see the [retention contract](RETENTION_EVENTS.md#retention-policy-settings-kv-retention_policy-gui-on-settings). |
| `VCF_DOCTOR_EVENT_RETENTION_HOURS` | [Configuration default](../backend/app/config.py) | Seeds the independent event history window; saved Settings values take precedence. See [event retention](RETENTION_EVENTS.md#events-and-tasks). |
| `VCF_DOCTOR_EVENT_ROW_CAP` | [Configuration default](../backend/app/config.py) | Seeds the maximum event rows per connection; saved Settings values take precedence. See [event retention](RETENTION_EVENTS.md#events-and-tasks). |
| `VCF_DOCTOR_HEALTH_WEIGHTS` | `critical=40,warning=15,info=0` | Deployment default for the health score weights; the values saved in Settings take precedence |
| `VCF_DOCTOR_MIN_INTERVAL_MINUTES` | `5` | Floor for scan intervals |
| `VCF_DOCTOR_SCHEDULER` | `on` | `off` disables scheduled scans (Scan Now still works) |
| `VCF_DOCTOR_STATIC_DIR` | `/app/static` in the image | Built frontend location |

`VCF_DOCTOR_TEST_FIXTURES` (and `VCF_DOCTOR_FIXTURES_DIR`, which points it
at a different sample set) exist for the test suite and the CI smoke test
only: they allow a connection backed by bundled sample data instead of a
vCenter. Never set them on a real deployment. When `VCF_DOCTOR_TEST_FIXTURES`
is off, startup pauses enabled schedules on leftover fixture connections
and logs a warning if any were paused. The connections remain visible on
Connections for the operator to remove; live vCenter schedules are unaffected.

## Rotating the encryption key

`VCF_DOCTOR_SECRET_KEY` and `VCF_DOCTOR_SECRET_KEY_PREVIOUS` are ordinary
environment variables, so rotation belongs to no particular deployment tool
and the procedure is the same in every shape below: supply the previous key
beside the new one in the environment, restart, and the app re-encrypts every
stored secret under the new key on startup in a single transaction and
reports the outcome on the Settings encryption card. Check that card after
the restart before going any further, then remove the previous key on the
next pass. Nothing is re-entered by hand, and no key is ever typed into the
interface.

**docker run**

```bash
docker run -d -v vcf-doctor:/data -p 8000:8000 \
  -e VCF_DOCTOR_SECRET_KEY="$NEW_KEY" \
  -e VCF_DOCTOR_SECRET_KEY_PREVIOUS="$OLD_KEY" \
  ghcr.io/sentania-labs/vcf-doctor:<tag>
```

**docker compose**

```yaml
services:
  vcf-doctor:
    image: ghcr.io/sentania-labs/vcf-doctor:<tag>
    environment:
      VCF_DOCTOR_SECRET_KEY: ${NEW_KEY}
      VCF_DOCTOR_SECRET_KEY_PREVIOUS: ${OLD_KEY}
```

**Kubernetes manifest**

```yaml
        env:
          - name: VCF_DOCTOR_SECRET_KEY
            valueFrom:
              secretKeyRef: { name: vcf-doctor-secrets, key: secret-key }
          - name: VCF_DOCTOR_SECRET_KEY_PREVIOUS
            valueFrom:
              secretKeyRef: { name: vcf-doctor-secrets, key: secret-key-previous }
```

**Argo CD with a sealed secret**

The deployment's `env` block is the Kubernetes one above, unchanged. What
Argo renders is the Secret behind it, so the previous key arrives as a second
sealed value feeding that same variable.

```yaml
spec:
  encryptedData:
    secret-key: AgB...<new key, sealed>
    secret-key-previous: AgB...<old key, sealed>
```

Unlike the other three shapes, this one does not restart anything on its own.
Where the `env` entries already exist, replacing the sealed values leaves the
Deployment spec byte-identical, so Argo syncs green with no rollout, and
`secretKeyRef` values are read once at container start: the running pod keeps
the old key and never sees the previous one. Make the restart explicit
(`kubectl rollout restart deploy/vcf-doctor`) or have the sync mutate the pod
template itself, with a checksum annotation over the Secret or a
name-suffixed generated Secret.

### Confirm it ran

Whatever the shape, open Settings > Encryption at rest after the restart and
read the last rotation line. Both of these must hold, and nothing else clears
the rotation:

1. The time on the line is the restart you just performed. A record from an
   earlier cycle stays on the card indefinitely, so "a rotation is reported"
   proves nothing on its own.
2. The line reports success. It begins "Last rotation" and says either how
   many secrets it re-encrypted or that there was nothing to do because every
   stored secret already opens with the current key. A startup handed the
   previous key always records an outcome, so either wording proves it ran
   with that key in hand.

A line beginning "Rotation attempted" is the failure wording, and a fresh
timestamp does not redeem it. It means at least one stored secret was not
opened by the value in `VCF_DOCTOR_SECRET_KEY_PREVIOUS`, and the line reports
both how many moved and how many were left, so a partial result is visible.
Two different things produce it. The supplied value may be wrong. Or it may be
correct while an older key is still in play, which happens when a credential
was never re-entered after an earlier rotation and so never came forward onto
the key you are rotating away from. The connections and Assistant key still
flagged on the page name which secrets are affected. Correct what is wrong, or
re-enter those credentials, and restart until a rotation is reported with
nothing left untouched.

No line at all, or a time from an earlier cycle, means the app never saw the
previous key on this restart. In the Argo shape that is usually the missing
rollout described above, because replacing the sealed Secret does not by
itself restart the pod.

In every one of those cases `VCF_DOCTOR_SECRET_KEY_PREVIOUS` stays exactly
where it is. Even after a partial rotation, it is still the only supplied key
that opened anything, and removing it before a clean run leaves whatever it
covers unreadable for good.

Whether credentials are flagged on the page (a connection showing "Needs
password", or an Assistant key asking to be re-entered) is worth reading, but
it never clears the rotation on its own. A pod that never restarted still
holds the old key, opens every secret with it, and flags nothing.

Only once both conditions hold, drop `VCF_DOCTOR_SECRET_KEY_PREVIOUS` (and the
sealed `secret-key-previous` entry, where one is used) on the next pass and
restart again. Leaving it set is not dangerous, it only keeps the old key
present longer than it needs to be.

## Verifying a pulled image

The published image index carries a max-mode SLSA provenance attestation and
an SPDX SBOM, and the digest is signed keyless with cosign (identity: this
repository's `ci.yml`, logged in Rekor).

```bash
cosign verify ghcr.io/sentania-labs/vcf-doctor@<digest> \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github.com/sentania-labs/vcf-doctor/'
docker buildx imagetools inspect ghcr.io/sentania-labs/vcf-doctor:<tag> --format '{{ json .SBOM }}'
```

## Local convenience

`docker-compose.yml` builds and runs the whole stack for laptop use. It is not
a deployment artifact.

## Recovery

- **Lost database**: history is gone; connections and settings must be
  re-entered. Nothing in vCenter is affected. Back up PostgreSQL the way you
  back up any other database; the container volume no longer holds history.
- **Database unreachable**: liveness stays green so nothing restarts the
  container, and readiness goes red so nothing routes traffic to it. Both
  answer in about three seconds rather than stalling. Everything that needs the
  database does fail while it is down, sign-in included; the Settings database
  panel reports the outage once a page is reachable, which covers the common
  partial case of a database that is up but not migrated.
- **Lost encryption key, database intact**: history is intact; re-enter each
  vCenter password (flagged "Needs password" on Connections) and the
  Anthropic key. See [Security](SECURITY.md).
- **Rotated encryption key, previous key still available**: no re-entry is
  needed; follow [rotation and recovery](SECURITY.md#secrets-at-rest).
- **Bad release**: re-pin the previous digest or tag and file an issue. The
  database schema is migrated forward on startup; going back a release is
  not guaranteed to be safe once a newer release has written to the volume,
  so snapshot the volume before upgrading anything you care about.
