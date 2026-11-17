# Runbook: `jarvis-backup`

A nightly **encrypted** `tar` of the Hermes volume (`/opt/data`, mounted **read-only**) into
`${BACKUP_DIR}`, a copy of it in a second location, and a weekly drill that restores the newest
one into a scratch directory and reads it back. Same image as [`jarvis`](jarvis.md), different
entrypoint (`/opt/jarvis/bin/jarvis-backup loop`). It has no port and no healthcheck — it is a
sleep loop.

- **Image:** `${AURA_REGISTRY}/jarvis:${AURA_TAG}` (entrypoint `jarvis-backup`)
- **Reads:** `hermes-home` → `/opt/data:ro`
- **Writes:** `${BACKUP_DIR}` → `/backups` (`hermes-home-<utc>.tar.gz.age`), `${BACKUP_MIRROR_DIR}` → `/mirror`
- **Keeps:** the newest `BACKUP_KEEP` files (default 14) in each place

The HF and Piper caches and `*.log` are excluded (re-seeded from the image); the
sessions DB and the rest of `/opt/data` are included. The tar is piped into
[age](https://age-encryption.org) (v1.3.2, pinned in `scripts/install-age.sh`; the image
builds that version with a patched Go, T-0758), so no clear tarball is ever on the disk.

## What you set (in `/opt/jarvis-host/.env`)

| Variable | What | Without it |
| --- | --- | --- |
| `BACKUP_AGE_RECIPIENT` | The **public** key (`age1…`) | The nightly refuses to run: `FAILED: BACKUP_AGE_RECIPIENT is not set`. It never writes a clear backup. |
| `BACKUP_AGE_IDENTITY` | Host path of the **private** key file, mounted read-only | The Sunday drill and `jarvis-backup drill` fail naming this setting. |
| `BACKUP_MIRROR_DIR` | Host path of a second location (another disk, a NAS mount) | Backups are written but the log warns "this is the only copy", and the drill fails: "no second copy". |

One-time key pair (do this off the host, or copy the key off it):

```bash
age-keygen -o backup-age.key        # prints "Public key: age1…" — that is BACKUP_AGE_RECIPIENT
```

**Keep a copy of `backup-age.key` somewhere that is not this host.** Lose it and every backup is
unreadable. Mounted on the same host as the backups it also means anyone who owns the host can
read them, so the mirror is where the protection is: put the key file outside what the mirror
mounts, and the NAS sees only ciphertext.

Backups from before 2026-11-08 are clear `hermes-home-*.tar.gz` files. Nothing prunes them any
more; delete them (`rm`) once the first encrypted backup has been drilled.

## Start / stop

```bash
cd /opt/jarvis/deploy
docker compose up -d jarvis-backup
docker compose restart jarvis-backup
```

## Verify it is running / force a backup now

```bash
docker compose logs --tail=20 jarvis-backup      # "nightly at 03:15 (TZ), keep 14, drill on Sundays"
docker compose exec jarvis-backup jarvis-backup once   # one backup immediately (+ the mirror copy)
ls -1t "$BACKUP_DIR"/hermes-home-*.tar.gz.age | head
```

The loop fires at **03:15 in the container TZ**. A failed run prints
`[jarvis-backup] FAILED` and the loop continues.

## The drill

```bash
docker compose exec jarvis-backup jarvis-backup drill            # the newest backup
docker compose exec jarvis-backup jarvis-backup drill /backups/hermes-home-<timestamp>.tar.gz.age
```

It decrypts the file into a scratch directory in the container (never the live volume), then
checks that `config.yaml` parses, `state.db` (the sessions database) passes SQLite's
`integrity_check` and lists tables, `skills/` holds at least one `SKILL.md`, and the same file
exists byte for byte in the mirror. Exit 0 and `DRILL OK` only if all of that holds. Otherwise
`DRILL FAILED:` and a reason:

- `…is corrupt or truncated` — the file does not authenticate, or decrypts to something that is not a tar.
- `the wrong identity` — `BACKUP_AGE_IDENTITY` is not the key the file was encrypted to.
- `config.yaml` / `state.db` / `skills/` — the restore worked but that part did not read back.
- `no second copy` — `BACKUP_MIRROR_DIR` unset, or the newest file is missing or different there.
- `no backup to restore` — nothing in `/backups`.

The loop runs the drill on **Sundays, right after the 03:15 backup**, so it proves the file
just written. Its verdict is in `docker compose logs jarvis-backup`, and
the nightly and the drill each also leave one line (`kind epoch ok|failed`, no path or key) in the
`backup-status` volume, which the assistant's container mounts read-only (`/opt/backup-status`):
the daily health check reads it and tells you once about a failed drill, a failed nightly, or no
passing drill for more than eight days (only after a first drill has run; a fresh install is not a
breakage). It never sees the backups themselves. The decrypted home exists only in the service's `/tmp`, a tmpfs (`size=4g` in compose,
about 4x the ~860 MB compressed home): it is removed when the drill ends and vanishes with the container
if the drill is killed, so no clear text is left on a disk. A home that grows past it makes the drill
fail with "the scratch space is full"; raise `size`. At start the loop prints a `WARNING` line for each of
the three variables that is unset, saying what stops working.

## Restore (drill this before you need it)

```bash
docker compose exec jarvis-backup jarvis-backup restore hermes-home-<timestamp>.tar.gz.age
```

That prints the procedure; it does not restore in place. On the host (with `age` installed),
stack stopped:

```bash
cd /opt/jarvis/deploy && docker compose down
age -d -i /path/to/backup-age.key "$BACKUP_DIR"/hermes-home-<timestamp>.tar.gz.age | \
  docker run --rm -i -v jarvis-hermes-home:/opt/data alpine \
    sh -c 'rm -rf /opt/data/* && tar -C /opt/data -xzf - && chown -R 10000:10000 /opt/data'
docker compose up -d && curl -fsS "http://$TAILSCALE_IP:8642/health"
```

The `chown 10000:10000` matters — the Hermes process runs as that uid and a tar
restored as root leaves `/opt/data` unwritable.

## Common failures

- **`/backups` empty.** `BACKUP_DIR` on the host is not mounted or not writable, the loop
  has not reached 03:15 yet, or `BACKUP_AGE_RECIPIENT` is unset (the log says so). Run
  `jarvis-backup once` to prove the path.
- **Restore comes up unwritable.** The `chown` step was skipped; re-run it against the
  volume.
- **`.part` files beside no finished backup.** Before 2026-09-18 the script took tar's
  exit 1 ("file changed as we read it", which a live volume always has) for a failure
  and never renamed the archive; thirteen nights in a row on the host. Since then exit 1
  is a finished backup and a real failure (exit 2 and up) removes its `.part` and logs
  `FAILED`. A `.part` left today means the container was stopped mid-write.
- **The archives are somewhere else.** `BACKUP_DIR` in `/opt/jarvis-host/.env` is the
  bind mount; on 2026-09-18 it still pointed at a host simulation under a home directory
  from the first deploy rehearsal, so sixteen nights of backups went there.
- **`DRILL FAILED … state.db` on a backup that otherwise looks fine.** The database is copied
  while Hermes writes it; the drill is what tells you that copy is not consistent. Run
  `jarvis-backup once` and drill again; if it keeps failing, the backup method needs a
  `sqlite3 .backup` step first (GAPS G29).

## Rollback

Rolling back an image tag does not touch backups; the volume, `${BACKUP_DIR}` and
`${BACKUP_MIRROR_DIR}` persist across `make rollback`. An image from before 2026-11-08 writes
clear tarballs and has no `drill`.
