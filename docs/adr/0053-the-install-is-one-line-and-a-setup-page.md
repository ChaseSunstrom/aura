# ADR-0053: the install is one line, and its questions are a page the installer serves

- **Status:** Accepted (2026-11-16)
- **Comes from:** the owner, 2026-11-09, for the public release: *"before the push make sure it
  has a one line install/setup GUI"*; recorded as T-0864: one command on Linux and macOS, one in
  PowerShell on Windows, a graphical first-run setup (server, models, voice, wake word, devices,
  theme) "with nothing hand-edited".
- **Builds on:** [ADR-0017](0017-one-settings-schema.md) (one settings schema),
  [ADR-0010](0010-volume-seed-cont-init.md) (the volume is seeded once).

## Context

`scripts/install.sh` already put both stacks on a machine in seven phases, but it assumed two
things a stranger does not have. It pulled images from a registry (production's, on the owner's
network), so on any other machine the first run failed at `images` unless `--build` was known
about, and `--build` then failed too, because the base compose file names images and builds
nothing. And everything a person chooses was a hand-edit of two `.env` files after the fact: the
model endpoint, the voice, the time zone, a console password printed nowhere but that file.

The console has its own first-run walkthrough (naming yourself, enrolling a voice), but it exists
only after the stack is up. The choices that decide *what is started* (a local model or an
endpoint, a GPU voice or a CPU one, the address every port binds) must be made before any
container exists, on a machine that may have no Python packages, no Node and no desktop.

## Decision

**Two thin entry points fetch the source; one installer does the work; its questions are a page
it serves itself for the length of the install.**

- **`install.sh` (POSIX sh) and `install.ps1`** at the root are what the one line pipes into a
  shell. They check for Docker and git, fetch the source into `~/aura/src` (a clone, or a tarball
  where git is missing) and hand over to `scripts/install.sh`. On Windows the PowerShell entry
  point runs that same script through Git for Windows' bash: there is one installer, not two.
  Each is one function called on its last line, so a download cut short runs nothing.
- **The setup page is `deploy/setup/`**: a standard-library Python server (`server.py`) and three
  static files. It runs on the machine's own `python3`, or in a `python:3.12-alpine` container
  where there is none. It listens on loopback unless told otherwise, never on every address, and
  answers its API only to a one-time token the installer prints in the page's address; the page
  moves the token out of the address bar at once.
- **The page writes one file and nothing else**: `<dir>/setup/answers.env`, mode 0600, every
  value matched against a strict pattern on the server and again, line by line, by the installer,
  which reads the file as data and never sources it. The console password is hashed in the server
  (scrypt, the console's own parameters and Unicode normalisation, bound to it by
  `tests/contracts/console-password.json`) and only the hash is written. The hash and a model key
  are blanked in that file once `.env` holds them.
- **Every question maps to a setting that exists.** A choice the stack cannot honour is not
  offered: there is no "no voice", and the wake word is on or off, with the word following the
  theme, because that is what the house does. What a machine cannot run (no NVIDIA card) is
  shown disabled with the reason, and refused by the server if sent anyway.
- **The installer reports to the page.** It writes `<dir>/setup/progress.json` at each phase;
  the page polls it and shows the eight phases, the failure if there is one, and the console's
  address at the end.
- **Images are built from the source when no registry is named.** `deploy/docker-compose.build.yml`
  adds a `build:` to the six services production pulls, under the names the base file asks for,
  and only the services the answers need are built and started.
- **The theme is seeded, not configured.** The chosen theme is written as the primary persona
  into the Hermes volume before the first start, the same file the persona plugin keeps; no new
  environment variable carries it.
- **Run again, it carries on, and it never removes what a house has stored.** The installer
  continues with containers compose started from this install's own folders and refuses
  anybody else's. The entry point updates a clone with a pull, unpacks an archive copy over
  itself and takes only an empty folder: the house keeps its settings and what it has learned
  inside the source (`aura-core/.env`, `aura-core/config`).
- **Room is checked in both places that fill**: the install directory, and wherever Docker
  keeps its images, which is most of it and often another disk.
- **The page wears the design system.** It loads the generated `tokens.css`, `themes.css` and
  fonts from the source, types no colour or size of its own (`make token-lint` covers
  `deploy/setup`), and switches theme live when one is picked.

## Consequences

- A person with Docker gets from one line to a running console without opening an editor, and
  the same line run again continues where a failed run stopped.
- The first install on a new machine builds six images: eight to nine minutes on four of the
  proof computer's processors and up to 26 GB stored with no local model, longer and more elsewhere. The installer
  asks for 30 GB free before it starts; the page shows the phase but not a percentage. A public
  image registry would remove the wait and is not part of this decision.
- No service mounts a file of the host's own system. The house mounted `/etc/localtime`; a
  machine with no zone data has none, and the first complete run stopped at `start` there. The
  zone is `TZ` and the image's own zone data.
- The page is plain http. On loopback that is this computer talking to itself. With
  `--setup-bind` on a LAN address the token and the answers cross that network unencrypted; the
  installer says so, and prints an `ssh -L` line as the alternative.
- `--yes` and `--no-gui` keep the terminal path, so automation and the existing tests do not
  need a browser.
- An install that already has a `.env` is never shown the page: its settings are changed where
  they live (the console's settings, or the file), not by answering the questions again.
- The proof is `scripts/verify/install-clean.sh`: a container that is its own machine (Ubuntu,
  Docker Engine, curl, git, its own Docker daemon, no python3, no source), a one-commit
  repository of the tree's files on a git daemon, the line, a browser answering the page, a
  sign-in to the console, and a question typed there that comes back answered by the
  endpoint named on the page. Its record is `docs/verification/install/clean-container.log`.
- **Not covered:** macOS and Windows run the same script through Docker Desktop and have not
  been run on real hardware by this project (`install.ps1` has only been read and linted, never
  executed); the firewall and HTTPS; the model weights' first download; a password that begins
  or ends with a character only one of Python's `strip` and JavaScript's `trim` treats as space.
