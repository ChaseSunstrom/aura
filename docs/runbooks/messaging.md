# Runbook: messaging (Telegram, Discord, inbound webhooks)

JARVIS answers on Telegram and Discord through Hermes' own gateway platform
adapters, and turns external events into turns through Hermes' inbound webhook
platform. All three are **off** in a fresh deployment and none of them can be
switched on by an environment variable arriving on its own.

A chat platform is a door into the same agent the console and the phone reach, not
a separate assistant: memory, goals and skills are the one Hermes instance's, and
every tool call goes through the same approval gate. What is *not* shared is the
conversation. Hermes keys a session as `agent:main:{platform}:{chat_type}:{chat_id}`,
so a Telegram DM and the console each have their own thread of talk, and asking
Telegram to "carry on with that" means naming what "that" was.

## What is on, and who may talk

| Platform | Enable flag | Allowlist | Delivery target |
| --- | --- | --- | --- |
| Telegram | `TELEGRAM_ENABLED` | `TELEGRAM_ALLOWED_USERS` (+ `TELEGRAM_GROUP_ALLOWED_CHATS`) | `TELEGRAM_HOME_CHANNEL` |
| Discord | `DISCORD_ENABLED` | `DISCORD_ALLOWED_USERS` | `DISCORD_HOME_CHANNEL` |
| Inbound webhook | `WEBHOOK_ENABLED` | HMAC per route (`WEBHOOK_SECRET`) | n/a |

Every one of those is a typed setting in `aura-schema` and appears in
[the settings reference](../reference/settings.md) under **Messaging**. The bot
tokens are settings too, but only by *name*: `TELEGRAM_BOT_TOKEN` and
`DISCORD_BOT_TOKEN` carry a shape and a description and never a value, and neither
is rendered into `config.yaml` — Hermes' adapters read them from the environment.

### Why the enable flag exists

Hermes turns a chat platform on the moment it sees a bot token in the environment
(`gateway/config.py`, `_enable_from_env`). A token pasted into `.env` to "try
something" would therefore put JARVIS on a public network with nobody having
decided to. So the rendered config writes `enabled` explicitly for every platform,
from a setting of its own, and an explicit `enabled: false` wins over a token that
is present. `deploy/tests/test_gateway_platforms.py` pins that.

### How the allowlist is enforced

Not by JARVIS. Hermes' gateway decides, in
`gateway/authz_mixin.py::GatewayAuthorizationMixin._is_user_authorized`, and
`gateway/run.py::_handle_message` calls it **before a session is created and before
the model is asked anything**: an unauthorized sender is logged and dropped there.

The rules that matter, in the order Hermes applies them:

1. A per-platform "allow anyone" flag (`TELEGRAM_ALLOW_ALL_USERS`, …). **JARVIS
   never sets one.**
2. The env allowlist: `TELEGRAM_ALLOWED_USERS` / `DISCORD_ALLOWED_USERS`, a
   comma-separated list of ids.
3. The pairing store — a person the owner approved with
   `hermes pairing approve <platform> <code>`.
4. `GATEWAY_ALLOW_ALL_USERS`. **Also never set.**
5. Otherwise: deny.

An **empty allowlist means nobody**, which is the behaviour to rely on if a bot
token ever leaks before the ids are filled in.

Prove it on any host where Hermes is installed:

```bash
python3 scripts/verify/gateway-allowlist.py
```

It drives Hermes' real authorization function with a listed id and an unlisted one
on both platforms, checks that an empty allowlist authorizes nobody, and confirms
that the two fail-open flags really do disable the allowlist — so the tests that
assert JARVIS never sets them are testing something real. Exit 0 means every case
held; exit 2 means Hermes was not importable there, which is "not proven here", not
"proven false". `deploy/tests/test_gateway_platforms.py` runs the same script and
skips with that reason where Hermes is absent.

### DM pairing, and when it does not apply

`GATEWAY_UNAUTHORIZED_DM_BEHAVIOR` decides what an unlisted person's direct message
gets:

- `pair` (the default) — a pairing code, which does nothing until the owner runs
  `hermes pairing approve telegram <code>` (`hermes pairing list` shows what is
  pending, and `hermes pairing revoke` takes it back). Approval also writes the
  person into the allowlist, so there is one place to look for who may talk.
- `ignore` — silently dropped.

**It only governs the case where no allowlist is configured.** Once a platform has
one, Hermes resolves the behaviour to `ignore` regardless of this setting, because
answering unknown senders with pairing codes is both noisy and a way to leak that
the bot is live. So on a configured JARVIS a stranger gets silence, and pairing is
the route in only while the allowlist is empty.

The refusal also happens earlier than the setting's name suggests. The Telegram
adapter rejects at intake (`_is_user_authorized_from_message`, logged as
`[Telegram] Blocked unauthorized user <id> in chat <id>`) by calling the same
gateway authorization function, so the message never becomes an event at all. The
gateway's own check in `_handle_message` is the second line of defence, not the
first.

Neither path reaches the model.

## Turning a platform on

1. Create the bot and copy its token: Telegram via [@BotFather](https://t.me/BotFather),
   Discord in the developer portal (the bot needs the **message content** intent).
2. Find your own numeric id. Telegram: message the bot once and read the id out of
   the gateway log, or ask a userinfo bot. Discord: enable developer mode and copy
   your user id.
3. Put the token, the id and the enable flag in the host's `.env`
   (`/opt/jarvis-host/.env`), never in the repo:

   ```
   TELEGRAM_ENABLED=true
   TELEGRAM_BOT_TOKEN=<from BotFather>
   TELEGRAM_ALLOWED_USERS=<your numeric id>
   ```

4. Deploy through the pipeline (`make deploy AURA_TAG=<tag>`). Production is
   immutable; nothing here is edited on the host by hand.
5. Check it came up, then message the bot from an account that is **not** listed and
   confirm it gets no answer at all and the refusal appears in the log:

   ```bash
   docker logs jarvis 2>&1 | grep -i telegram | tail
   docker logs jarvis 2>&1 | grep -E "Blocked unauthorized user|Unauthorized user"
   ```

## Inbound webhooks (n8n, CI, sensors)

`WEBHOOK_ENABLED=true` starts an HTTP listener on `WEBHOOK_PORT` (8650 by default),
published on `TAILSCALE_IP` (the host's LAN address) only. The template ships one route, `n8n`, whose prompt
hands the whole payload to the agent.

```
POST http://<lan-ip>:8650/webhooks/n8n
X-Webhook-Signature-V2: <hmac of timestamp+body with WEBHOOK_SECRET>
```

Every route must carry an HMAC secret or **the gateway refuses to start** — that is
Hermes' own check, and it is the reason `WEBHOOK_SECRET` is not optional once
`WEBHOOK_ENABLED` is true. Hermes' `INSECURE_NO_AUTH` escape hatch is never shipped
here, and a test asserts it is absent from the template.

Going the other way — JARVIS firing an n8n workflow — is the `n8n-trigger` plugin,
which needs no webhook listener at all.

## Traps found while wiring this up

Not incidents on this stack — nothing has run here with a real token yet. These are
what reading Hermes' source and running the live proof turned up, and each one is
the reason a line above exists.

- **A bot token alone starts a platform.** `gateway/config.py`'s `_enable_from_env`
  enables Telegram or Discord the moment the variable is present, unless the YAML
  says `enabled` explicitly. That is why the rendered config writes it for every
  platform. If a platform ever starts unexpectedly, check `config.yaml` still has
  its block: a hand-edited live config is the only route back to the old behaviour.
- **The pairing code does not appear once an allowlist exists.** Expected, and
  better than the alternative: `_get_unauthorized_dm_behavior` resolves to `ignore`
  when an allowlist is configured, whatever the global setting says, because
  answering unknown senders with codes is noisy and confirms the bot is live. Seen
  in the live proof, where the stranger received nothing at all.
- **The refusal is logged by the adapter, not the gateway.** Grep for
  `Blocked unauthorized user` first; `Unauthorized user:` is the gateway's cold-path
  message and only appears for senders the adapter let through.
- **A session outlives the allowlist that authorized it.** On restart Hermes
  re-checks a resumed session's owner against the current allowlist and drops it
  with "session owner is no longer authorized" (`gateway/run.py`). Removing an id
  therefore takes effect on the next restart, not just for new conversations.

## See also

- [Settings reference](../reference/settings.md) — the Messaging group.
- [`jarvis` runbook](jarvis.md) — the gateway container these platforms run inside.
- `scripts/verify/gateway-allowlist.py` — the unit-level proof described above.
- `deploy/tests/test_telegram_allowlist_live.py` — the end-to-end proof: a real
  `hermes gateway run`, the real adapter and allowlist, and a fake Bot API instead of
  api.telegram.org, so an unlisted account can be shown getting nothing without a
  token existing. Run it with `AURA_LIVE_TELEGRAM_PROOF=1`.
