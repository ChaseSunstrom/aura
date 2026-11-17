# Pairing

Connecting a phone, a desktop and the console to JARVIS. Two flows exist, and
which one a device takes depends on what it needs to reach.

## The console's link: the house (jarvis-core)

The console's **Pairing** panel mints a short-lived code and shows it as a QR
and as a link, `jarvis://pair?v=1&u=<house>&c=<code>`. A device claims the
code at the house (`POST /api/pair/claim`) and receives its own jarvis-core
token — a device token, per device, revocable on its own
(`GET/DELETE /api/auth/tokens`). Codes are 192-bit, single-use, five minutes,
and a claim carrying an `Origin` header is refused, so a web page cannot claim
one; the console re-applies that rule at its hop (`api/pair/claim`).

* **The desktop app** (`aura-desktop-app`, ADR-0051): on the console's
  Pairing panel choose **Open in the desktop app**. The `jarvis://pair` link
  opens the installed app, which pairs the agent inside it, keeps the console's
  address and opens it: one click, no terminal. Without a click, paste the link
  or just the console's address into the app's Pair page, which the first open
  shows when nothing is chosen, and which the tray's **Pair with a house…**
  opens again. A second launch hands its link to the running app rather than
  starting another one.
* **The desktop agent on its own** (`jarvis-desktop`, a server or a headless
  box): `jarvis-desktop pair '<link>'`, or the address and the code
  (`pair http://host:8080 --code …`). Written to its `config.json` at 0600;
  `run` walks the same steps interactively when nothing is paired.
* **The phone**, when it is paired to the house or to the console directly:
  scan the QR; the app claims the code the same way.

Minting needs the console password (the door the console's own session
opens) **and** `AURA_PAIRING_SECRET`, which the console holds in memory
after the operator enters it once — the relay never lends the admin token to
a claim. `aura-core/aura/api/pairing.py` is the authority; the console's
`data/pairing.ts` builds the link, and the same string is asserted byte for
byte by the phone's mirror (`android-app/tools/pairing_payload_test.py`).

## The adapter's QR: Hermes (the HUD socket, audio, `/v1`)

The phone's HUD, audio relay and `/v1/*` go to the [adapter](../runbooks/jarvis-adapter.md),
which takes a bearer key: the owner's master key, `AURA_API_KEY`
([ADR-0004](../adr/0004-thin-adapter.md)), or, since T-0569, a device's own key.
`GET /api/pair` with the master key answers `{url, key, kind: "hermes-adapter"}`;
with a device key it answers the same without `key`, plus the `device` it is.
The Android app reads that QR (`config/HermesPairing.kt`) and keeps the key
for every request and socket. The console's relay refuses to forward
`/api/pair` and `/api/devices/keys` to a browser.

**The console's code brings it too, with a key of the device's own.** A code
redeemed at the console (`POST /api/pair/claim`) answers with the house's device
token **and** an `adapter` payload whose key was minted for this device alone
(`aura-web/src/lib/server/pairClaim.ts` asks the adapter's `POST /api/pair`,
master key, server side, with the claim's name and `kind` and the house's token id).
A claim says what it is (`kind`: `phone`, `desktop`, `other`;
`tests/contracts/pairing-claim.json`); a desktop gets no adapter key, since its
agent never talks to the adapter.
The phone reads it with the adapter QR's rules and keeps it beside the house
(`JarvisConfig.adapterUrl`/`adapterKey`): the house carries the device channel
and voice identity, the adapter the assistant — voice, approvals, personas and
CosyVoice. Before 2026-09-25 the console's code gave the house alone, and every
question went to the house's pipeline, which has no agent ("Sorry, no
conversation agent is configured yet"). A key is minted only for a caller
that just redeemed a code, which took the console password and the pairing
secret to mint, and the master key is never in the answer. A house-only phone (paired straight at jarvis-core) still gets
answers: the house forwards its pipeline to Hermes (`voice: agent:`), without
approvals.

Plain HTTP to a LAN address (`192.168.x.x`, the house's own) is accepted, and the phone's
in-app WireGuard tunnel carries it when you are away. A saved tunnel that will not start (its
Endpoint's name does not resolve yet, as on a car's Wi-Fi with no internet) is tried again,
at most 30 s apart, and Settings says why it is down (T-0913). The phone also still accepts the
`100.64.0.0/10` range, for a pairing made before the house moved to the LAN (2026-10-27).

## The console's own credential

The console reaches the adapter with `AURA_ADAPTER_KEY`, which is the **same value**
as `AURA_API_KEY`. Set the console password from `AURA_CONSOLE_PASSWORD[_HASH|_FILE]`
in the host `.env`, or choose it on first open — the setup sheet names the owner and
chooses it ([first run](first-run.md)) — kept in the `web-storage` volume; see the
[jarvis-web runbook](../runbooks/jarvis-web.md).

## Revoking one device

The console's **Devices → Own keys** lists every device key (name, kind, when
paired, when last seen) with a Revoke that asks first. One Revoke ends that
device's adapter key (the adapter hangs up its open sockets), its house token
(`DELETE /api/auth/tokens/<id>`, which hangs up the house's), and every console
session its key opened (the console's socket relays re-check within 30 s);
every other device keeps working. The key file is on the adapter's own volume
(`jarvis-adapter-state`), which is not in the backup: if it is lost, those
devices pair again and the master key still works.

The console has a second key list, **Integrations → House keys**: every token
the house itself holds, including ones no device was paired with (a console, a
display, a token made by hand). Revoking there ends the house token only; a
paired phone's adapter key stays live, so revoke a phone under Own keys.

## Rotating

A phone paired before T-0569 (2026-11-08) still holds the master key and is not
under Own keys: pair it again from Devices to give it its own. Once every phone
has, changing `AURA_API_KEY` (and `AURA_ADAPTER_KEY`, the same value, for
the console) locks out nothing but a phone that was not re-paired (GAPS G30). The full key-rotation table (adapter key,
Hermes key, serve token, HASS token, deploy SSH key, registry token) is in the
[host runbook](../runbooks/host.md#key-rotation).
