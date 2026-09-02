# Getting Started

This is the shortest path from nothing to a VCF Doctor instance scanning a
vCenter. It assumes you can run a container and can reach a vCenter over
HTTPS from wherever the container runs. No configuration files are needed;
everything is set in the GUI.

## 1. Run the container

Use the published image:

```bash
docker run -d --name vcf-doctor \
  -p 8000:8000 \
  -v vcf-doctor-data:/data \
  ghcr.io/sentania-labs/vcf-doctor:latest
```

Or, from a clone of this repository:

```bash
docker compose up --build
```

Either way the console is at http://localhost:8000 and the database lives on
the `/data` volume. Keep that volume: it holds every snapshot, the change log,
your connections and your settings. Run exactly one instance per volume; two
instances would scan twice and fight over the database.

Tags: `latest` is the newest build of `main` that passed every CI gate,
`v0.1.N` are numbered releases, `sha-<7>` pins a specific commit. Pin a
release or a sha in anything that is not a laptop.

## 2. Set the operator password

The first visit shows a setup page. Choose a password (8 characters or more).
That single shared password protects the console and the API from then on.
To seed it non-interactively, set `VCF_DOCTOR_ADMIN_PASSWORD` on first boot;
it is only read when no password exists yet.

You can change the password later under Settings, Access.

## 3. Add a vCenter

Go to **Connections**, click **Add vCenter**, and fill in:

- **Name**: what you want to see in the connection selector, for example
  `Workload domain 01`.
- **vCenter host**: the FQDN or IP of the vCenter.
- **Username** and **Password**: a read-only vCenter account is enough. VCF
  Doctor never changes anything in vCenter.
- **Scan interval**: how often to snapshot this vCenter (15 minutes by
  default, 5 minutes minimum).
- **Verify TLS certificate**: on if your vCenter certificate is trusted by
  the container, off for self-signed lab certificates.

Use **Test** to check the credentials before saving. The password is
encrypted before it is stored.

## 4. Run the first scan

Click **Scan Now** in the top bar. A scan takes a few seconds to a minute or
two depending on inventory size. Scheduled scans then continue at the
interval you chose, whether or not anyone has the console open.

Health checks that compare against a previous snapshot (removed hosts,
removed networks) only produce findings from the second scan onward, so the
first Overview will be quieter than the second.

## 5. Where to look

- **Overview**: the health score, key numbers, the most important findings,
  and the latest changes. Start here.
- **Health**: every finding from the latest snapshot. Click one to see the
  evidence behind it, the related changes and the vCenter events in that
  window. The Assistant buttons in the drawer explain the finding or draft a
  PowerCLI script if an Anthropic key is configured.
- **Changes**: pick any two snapshots of one connection and see what is
  different, by object.
- **Environment**: the same changes rolled up across every connection for a
  time window (last scan cycle, 24 hours, 7 days, custom).
- **Events**: vCenter events and tasks collected with each scan.
- **Inventory** and **Snapshots**: browse what was captured and when.
- **Settings**: retention tiers, health score weights, change significance,
  the assistant and its API key, encryption status, and the password.

## Before you rely on it: the encryption key

vCenter passwords and the Anthropic API key are encrypted before they are
written to the database. The encryption key comes from one of two places:

- **`VCF_DOCTOR_SECRET_KEY`** set in the deployment. This is the production
  path. In Kubernetes keep it in a SealedSecret (or equivalent) in your
  deployment repository so it survives redeploys and rebuilds.
- **A generated key file.** Without the variable, the app creates
  `vcf-doctor.key` next to the database on the volume on first start, with
  owner-only permissions, and reuses it afterwards.

Losing the key is recoverable: the snapshots and history are untouched, but
each connection is flagged **Needs password** on the Connections page until
you re-enter it, and the Anthropic key must be re-entered in Settings.
Settings, Encryption at rest shows which source is active and never shows
the key itself. Rotate by setting a new key, restarting, and re-entering the
passwords. Generate one with:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

## Optional: the assistant

The Claude assistant is off until it has an Anthropic API key. Enter one in
Settings, Assistant (stored encrypted, never shown again) or set
`ANTHROPIC_API_KEY` on the container. A key entered in Settings takes
precedence. Everything the assistant says is grounded in the evidence on
screen, and any script it generates is shown, never run.

## Next

- [Deployment](DEPLOYMENT.md): every environment variable, Kubernetes
  expectations, image verification.
- [Security](SECURITY.md): what protects the console and what CI checks
  before an image is published.
