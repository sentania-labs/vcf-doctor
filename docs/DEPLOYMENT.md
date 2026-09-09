# Deployment

This repository is **not** responsible for deployment. It publishes a
container image; whoever deploys it (in the lab, the deployment repository
and Argo CD) owns everything else. Nothing about a specific vCenter belongs
in a manifest: connections, schedules, retention and assistant settings are
application state set through the GUI and stored on the volume.

## Contract

| Item | Value |
|---|---|
| Image | `ghcr.io/sentania-labs/vcf-doctor:<tag>` where tag is `vX.Y.Z` (release), `sha-<7>` or `latest` |
| Port | `8000` (HTTP) |
| Health | `GET /api/health` (the container also declares a `HEALTHCHECK` on it) |
| Build identity | `GET /api/version` returns the [build identity fields](../backend/app/_version.py); `GET /api/health` reports the same version |
| Persistent volume | `/data` (SQLite at `/data/vcf-doctor.db`, encryption key file next to it) |
| Replicas | **exactly 1**, `strategy: Recreate`. Two pods would double-scan and contend for SQLite. |
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

## Environment variables

All optional. Anything an operator would change day to day has a GUI
control in Settings; these only set deployment-time defaults or override
them.

| Variable | Default | Purpose |
|---|---|---|
| `VCF_DOCTOR_DB_PATH` | `/data/vcf-doctor.db` | SQLite location |
| `VCF_DOCTOR_SECRET_KEY` | unset | Key for encrypting vCenter passwords and the Anthropic key at rest. Unset: a key file is generated next to the database. See [Security](SECURITY.md). |
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

`docker-compose.yml` builds and runs the image with a named volume for
laptop use. It is not a deployment artifact.

## Recovery

- **Lost volume**: history is gone; connections and settings must be
  re-entered. Nothing in vCenter is affected.
- **Lost encryption key, volume intact**: history is intact; re-enter each
  vCenter password (flagged "Needs password" on Connections) and the
  Anthropic key. See [Security](SECURITY.md).
- **Rotated encryption key, previous key still available**: no re-entry is
  needed; follow [rotation and recovery](SECURITY.md#secrets-at-rest).
- **Bad release**: re-pin the previous digest or tag and file an issue. The
  database schema is migrated forward on startup; going back a release is
  not guaranteed to be safe once a newer release has written to the volume,
  so snapshot the volume before upgrading anything you care about.
