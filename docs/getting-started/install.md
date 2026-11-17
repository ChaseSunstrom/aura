# Install

One line puts AURA on a machine that has Docker and opens a setup page in the browser.
Nothing is edited by hand.

Linux and macOS:

```sh
curl -fsSL https://raw.githubusercontent.com/ChaseSunstrom/aura/main/install.sh | sh
```

Windows, in PowerShell (Docker Desktop and Git for Windows installed):

```powershell
irm https://raw.githubusercontent.com/ChaseSunstrom/aura/main/install.ps1 | iex
```

To read it before it runs, download it first:

```sh
curl -fsSLO https://raw.githubusercontent.com/ChaseSunstrom/aura/main/install.sh && less install.sh && sh install.sh
```

The line fetches the source into `~/aura/src` and runs `scripts/install.sh` from it with
`~/aura` as the install directory. It asks for nothing in the terminal: it prints the
address of the setup page and opens it.

## The setup page

Six questions, then a review, then the install with its progress on the same page:

| step | what it asks | what it sets |
|---|---|---|
| Server | the address the ports bind (your network, or this computer only), the console password, the time zone, optionally an address for phones away from home | `TAILSCALE_IP`, `AURA_CONSOLE_PASSWORD_HASH` (the password itself is never written), `TZ`, `AURA_PUBLIC_URL` |
| Models | a model on this machine (needs an NVIDIA card) or an endpoint you already have, with a button that asks the endpoint for its models | `LLM_URL`, `LLM_MODEL`, `LLM_API_KEY`; whether `jarvis-models` is started |
| Voice | a natural voice (on the card) or a light one (on the processor), and the size of the listening model | `TTS_PROVIDER`, `AURA_TTS_ENGINE`, `WHISPER_MODEL` |
| Wake word | whether the house listens for its name | whether the wake-word service is started |
| Devices | phone, computer, car | which pairing steps the end of the install lists |
| Theme | AURA or JARVIS: the look, the voice and the name | the primary persona |

The page wears the theme you pick while you pick it. What a machine cannot do is greyed
with the reason (no graphics card: no local model, no natural voice).

The page is served by the installer for the length of the install, on this computer only
(`127.0.0.1:8198`), behind a one-time token in the address it prints. On a server with no
screen, either forward the port from your own computer (the installer prints the `ssh -L`
line) or serve the page on the server's own address:

```sh
curl -fsSL https://raw.githubusercontent.com/ChaseSunstrom/aura/main/install.sh | sh -s -- --setup-bind 192.0.2.10
```

That is plain http on that network: the token and your answers cross it unencrypted. Use
it on a network you trust, or use the tunnel.

`--no-gui` asks the two questions it must in the terminal instead and takes the defaults
for the rest; `--yes` asks nothing at all.

## What the installer does

Eight phases, each safe to run again; the page and the terminal both say which one it is in:

1. **preflight**: Docker with compose v2, curl, free disk (30 GB in the install folder and
   where Docker keeps its images; an install with no local model stored 26), the ports free
   (8080, 8199, 8642, 8643, and 8198 for the page). An NVIDIA GPU and its container
   toolkit are optional; without them AURA runs on the processor with an endpoint for
   its model.
2. **setup**: the page above. Its answers land in `<dir>/setup/answers.env` (mode 0600)
   and are checked again, value by value, before anything uses them.
3. **secrets**: `deploy/` is copied into `<dir>` (the layout production runs). A new
   `.env` for each stack (`<dir>/.env`, and `aura-core/.env` in the source, where the
   house stack runs from) comes from the `.env.example` templates, mode 0600, every key
   that needs a secret filled with a fresh random value, and your answers written in.
   Secrets are never printed. **An existing `.env` is never changed**; the script names
   the keys it lacks and the setup page is not shown.
4. **n8n**: `deploy/n8n-env.sh <dir>/n8n.env` creates n8n's own secrets.
5. **images**: built from the source on this machine (`deploy/docker-compose.build.yml`),
   or pulled when `AURA_REGISTRY` names a registry that has them. Only the services your
   answers need are built. A first build takes a while: the page shows it.
6. **start**: the theme is put in place, then both stacks start.
7. **verify**: each service's health, waiting up to 3 minutes each.
8. **pair**: the console's address, and the next step for each device you named.

| flag | meaning |
|---|---|
| `--dir DIR` | install directory (`~/aura` through the one line, `~/jarvis` from a checkout); a relative one is taken from the folder you run the line in |
| `--bind IP` | the one address ports bind, instead of the page's first question (never `0.0.0.0`) |
| `--no-gui` | no setup page: ask in the terminal |
| `--setup-bind IP` | where the setup page listens (default `127.0.0.1`; never `0.0.0.0`) |
| `--setup-port N` | the setup page's port (default 8198) |
| `--yes` | unattended: take defaults, never ask, no setup page |
| `--dry-run` | print every action, write and start nothing |
| `--build` | build the images here (already the default when no registry is named) |
| `--upgrade` | an install already runs here: hand over to `scripts/deploy-here.sh` |
| `--repo URL` | clone the source from here when the script is run on its own |

After a pipe, flags go after `sh -s --`. The one line also reads `AURA_HOME` (where it
installs), `AURA_REF` (a branch or tag instead of `main`), `AURA_REPO_URL` (a fork or a
mirror) and `AURA_INSTALL_DOCKER=1` (on Linux, install Docker with Docker's own script
first if it is missing).

The script **refuses to run when somebody else's containers of the same names are
running** on the host (a dry run too); `--upgrade` is the way to update a running install.

Running the line again after a failure continues: the answers are kept (without the
password's hash and the model key, which by then are in `.env`), the keys are kept,
built images are reused, and containers this install already started are carried on
with. The source is updated in place and never removed: the house keeps its settings and
what it has learned inside it (`~/aura/src/aura-core`).

Not covered by the installer: the firewall (`scripts/setup-host-firewall.sh`, needs
sudo), HTTPS in front of the console, and the first download of the model weights
(`jarvis-models` fetches them on its first start, so the first answer is slow). macOS and
Windows run the same script through Docker Desktop; neither has been run on real
hardware by this project yet ([verification](../verification.md)). The house service uses
host networking, which Docker Desktop has only as a setting (Settings > Resources >
Network > Enable host networking): switch it on first.

## Server

Run the line on the machine that will host AURA. Afterwards:

- Open the console (the page's **Open** button, or `http://<bind address>:8199`) and
  sign in with the password you chose. From `--yes` or `--no-gui` the password is
  `AURA_CONSOLE_PASSWORD` in `<dir>/.env`.
- Put HTTPS in front of the console if it is reachable beyond your own LAN
  (a reverse proxy such as Caddy; then set `AURA_PUBLIC_URL` and `AURA_CORS_ORIGINS`
  in `.env`).
- Close the host to everything else: `sudo scripts/setup-host-firewall.sh`.

## Desktop (the app)

On Linux, Windows or macOS, install the Jarvis desktop app (Tauri, one file per platform:
an AppImage on Linux, an installer on Windows; [`aura-desktop-app`](../../aura-desktop-app/README.md)).
Open it and choose the console's address, or in the console choose **Open in the desktop
app** on the Pairing panel: the `jarvis://pair` link pairs the computer. To run the whole
of AURA on a desktop, run the install line there as well, then open the app on the same
machine.

## Android

Install the Android app (APK) from the release page (`AURA_RELEASE_URL`, printed at the
end of the install), allow the install from that source, and open it. In the console, open
**Settings > Pairing**: it shows a QR; scan it from the app's first screen. The QR carries
the address the phone reaches (`AURA_PUBLIC_URL`, `http://<bind address>:8643` by
default). Android refuses cleartext to a bare address on newer versions, so beyond your own
trusted network put HTTPS in front and set `AURA_PUBLIC_URL` to it.

## After it

A visual walkthrough of first-run setup is planned for the console; until then see
**[First run](first-run.md)**.

## By hand

What the script does, step by step, for a host that already carries its own setup.
The by-hand host bootstrap is in the
[host runbook](../runbooks/host.md#host-bootstrap-once-on-the-jarvis-host).

### Prerequisites

- A **LAN address** for the host, reached from outside over your WireGuard into the LAN.
  Every published port binds `TAILSCALE_IP` (the name is kept; it holds that address) only
  ([ADR-0014](../adr/0014-host-is-lxc-runner-registry.md)).
- The **JARVIS host** (the dev LXC, which is production): Docker only, no sudo at runtime.
- An **LLM endpoint**: `qwen3.8-27b` on vLLM behind llama-swap, reachable at `LLM_URL`.
- The **house service** ([`jarvis-core`](../runbooks/jarvis-core.md)) running on the
  host at `:8080`, and a jarvis-core token for `HASS_TOKEN`.

### The bootstrap, in short

1. **Deploy user + registry.** Create a `deploy` user in the docker group; point Docker
   at the GitLab registry, `registry.home.example` (HTTPS, no insecure-registries entry).
2. **The one hand-created file.** Copy `deploy/.env.example` to the host's `.env`
   (`/opt/jarvis-host` on a host that also carries the checkout) and fill the secrets
   (`openssl rand -hex 32` for the three keys), `TAILSCALE_IP`, `HASS_TOKEN`, and the
   plugin URLs/tokens. Secrets live only here, referenced by name
   ([ADR-0017](../adr/0017-one-settings-schema.md)).
3. **First deploy.** From the dev LXC: `make deploy AURA_TAG=<sha>` (or push to
   `main`). Compose creates the `jarvis-hermes-home` and `jarvis-web-storage` volumes;
   the first start renders `config.yaml`, SOUL, USER and MEMORY from the image and
   never overwrites them after ([ADR-0010](../adr/0010-volume-seed-cont-init.md)).
4. **Firewall + TLS.** `scripts/setup-host-firewall.sh` drops everything but the
   bind interface; a reverse proxy (Caddy) puts the console on HTTPS 443. With
   WireGuard into the LAN, set `TAILSCALE_IP` (the bind address; the name predates
   the 2026-10-27 move to the LAN) to the host's LAN address and run
   `sudo LAN_ALLOW=<your LAN>/24 scripts/setup-host-firewall.sh` to let that subnet reach
   the console (8199) and the adapter (8643).

### What comes up

Five services in one compose project (`deploy/docker-compose.yml`):
[`jarvis`](../runbooks/jarvis.md), [`jarvis-adapter`](../runbooks/jarvis-adapter.md),
[`jarvis-web`](../runbooks/jarvis-web.md), [`searxng`](../runbooks/searxng.md),
[`jarvis-backup`](../runbooks/jarvis-backup.md). See the
[runbooks index](../runbooks/README.md) for how each is operated.

Next: **[First run](first-run.md)**.
