# Connecting accounts (Google, GitHub, Microsoft 365, Slack, Discord, GitLab, Spotify, Notion, Todoist)

JARVIS links an account with a real OAuth consent: you approve on the provider's own page, the house keeps
the token encrypted, and the model never sees it. Code: `aura-core/aura/integrations/oauth/`; tool:
`hermes/plugins/aura-connect/`.

| Provider | Gives JARVIS | Scopes asked | Token revoke at the provider |
|---|---|---|---|
| Google | Calendar read, Gmail read and drafts, Contacts read | see Google below | yes |
| GitHub | repos and notifications, read | `repo`, `notifications` | yes |
| Microsoft 365 | profile, mail and calendar, read | `offline_access`, `User.Read`, `Mail.Read`, `Calendars.Read` | no: remove the app in your Microsoft account |
| Slack | your public channels, read | user scope `channels:read` | yes |
| Discord | your profile and server list, read | `identify`, `guilds` | yes |
| GitLab | the API, read | `read_api` | yes |
| Spotify | your profile, playlists, player devices, read | `playlist-read-private`, `user-read-playback-state` | no: remove the app at spotify.com/account/apps |
| Notion | your user and the pages you share with the app, read | none (picked on Notion's page) | yes |
| Todoist | your projects and tasks, read; adds one task when you ask | `data:read`, `task:add` (guess, see below) | no: remove the app in Todoist's integrations settings |

**None of the seven added in T-1338 (Microsoft 365 to Todoist) has been tried against a real provider** (GAPS
G18): they are tested against a mocked provider that checks the protocol shape (PKCE, Basic or body client
credentials, form or JSON, the `state`). Each provider's own quirks (Slack answers 200 with `ok: false` and wraps
a person's token; Notion wants Basic auth, a JSON body and no PKCE; GitLab's address is a setting) are in the
`PROVIDERS` table's comment. Expect to adjust one on first contact. Where a provider has no revoke endpoint,
disconnecting still deletes the token here; remove the app on the provider's side to end the grant.

JARVIS never sends mail. Google has no scope that makes drafts without also allowing send (`gmail.compose`
does both), so the guarantee is in JARVIS, not the scope: there is no send tool. Likewise GitHub's `repo`
scope is read-write (it is the only one that reaches private repos); JARVIS only reads.

## Settings (jarvis-core `.env`, names only)

| Name | What |
|---|---|
| `OAUTH_REDIRECT_BASE` | The console's origin as your browser reaches it, e.g. `https://jarvis.example.net`. No trailing path. |
| `OAUTH_GOOGLE_CLIENT_ID`, `OAUTH_GOOGLE_CLIENT_SECRET` | From the Google Cloud client below. |
| `OAUTH_GITHUB_CLIENT_ID`, `OAUTH_GITHUB_CLIENT_SECRET` | From the GitHub OAuth app below. |
| `OAUTH_<P>_CLIENT_ID`, `OAUTH_<P>_CLIENT_SECRET` | For `<P>` in `MICROSOFT`, `SLACK`, `DISCORD`, `GITLAB`, `SPOTIFY`, `NOTION`, `TODOIST`: the client from that provider's developer page. |
| `OAUTH_GITLAB_BASE_URL` | A self-hosted GitLab's address (`https://gitlab.example.test`, no trailing path). It must be `https`; plain `http` is accepted only to a private address literal such as `http://10.0.0.7:8929`, because the client secret and tokens travel to it. Anything else is refused at start. Empty means gitlab.com. |

Redirect URI to give both providers: `<OAUTH_REDIRECT_BASE>/api/oauth/callback`. It lands on the console,
which relays to jarvis-core. Recreate the `jarvis-core` container after editing `.env`.

## Google

1. console.cloud.google.com: create or pick a project. APIs & Services, Library: enable Google Calendar API,
   Gmail API and People API.
2. OAuth consent screen: User type External, add yourself as a test user. Add the scopes
   `calendar.readonly`, `gmail.readonly`, `gmail.compose`, `contacts.readonly`.
3. Credentials, Create credentials, OAuth client ID, type Web application. Add the redirect URI above.
4. Put the client ID and secret in the two `OAUTH_GOOGLE_*` settings.
5. **Testing to In production.** An app left in Testing makes Google expire the refresh token after 7 days,
   so JARVIS will ask you to approve again weekly. On the consent screen, press Publish app. For personal
   use you need not complete verification: you get an "unverified app" warning to click through once, and
   the token no longer lapses weekly.

## GitHub

1. github.com, Settings, Developer settings, OAuth Apps, New OAuth App.
2. Homepage URL: the console origin. Authorization callback URL: the redirect URI above.
3. Generate a client secret. Put ID and secret in the two `OAUTH_GITHUB_*` settings.

## The other seven

Each: create an app on the provider's developer page, give it the redirect URI above and the scopes in the
table (read-only ones only; Slack's go under "User Token Scopes", Notion's capabilities are read content only),
then put the ID and secret in the two `OAUTH_<P>_*` settings. Microsoft: Entra admin center, App registrations,
Web platform, and a client secret; the `common` tenant takes a work, school or personal account. GitLab:
Preferences, Applications, with the `read_api` scope, on your own instance if self-hosted. A provider whose client ID
is empty stays off.

## Connecting

Tell JARVIS "connect my Google account". You approve the ask; JARVIS sends the consent link to the device
you are using. Open it in a browser that is signed in to the console (the callback sits behind the console
sign-in). The link works once and expires in ten minutes. Ask "is Google connected?" to run the read-only
test call.

If a refresh fails (revoked, expired), the account shows `needs_consent` and JARVIS says so once; connect
again to fix it. It does not retry in a loop. Disconnecting (`connect_revoke`) deletes the token and asks
the provider to void it.

## Named reads (T-1341)

`connect_read(provider, what, query, limit)` looks something up in a connected account. It is free for the
owner and closed to a guest, and its result counts as outside text (the next approval card says so). jarvis-core
makes the call, so the token never reaches Hermes. Each read is a fixed GET on the provider's own host (GitLab:
your configured base address); the model names a read from the list, never an address, and only the read's own
query parameter is passed (80 characters at most). Redirects are not followed. The answer is cut to a keep-list
of fields, 120 characters a string, 25 rows (10 by default) and 8000 bytes.

| Provider | Reads |
| --- | --- |
| Google | `calendars`, `events` (next 14 days; `query` searches), `contacts` |
| GitHub | `repos`, `notifications` |
| Microsoft | `inbox` (subject, sender, time, read flag; never the body), `calendar` (next 14 days) |
| Slack | `channels` |
| Discord | `servers` |
| GitLab | `projects`, `issues` (`query` searches both) |
| Spotify | `playlists`, `devices`, `playing` |
| Notion | `me` |
| Todoist | `projects`, `tasks`, `due` (tasks due today or overdue; the morning brief reads it, T-1415) |

Left out, each for the scope the row already holds: Gmail subjects (the read scope lists ids only, one more call
each), Notion pages and databases (search is a POST), Slack messages (the history scopes are not requested).
No new scope was added. The list is pinned in `tests/contracts/connect-reads.json`, read by both suites. None of
these ran against a real provider (GAPS G18).

## The one write: add a Todoist task (T-1370)

`connect_write(provider="todoist", action="add_task", content, due, description)` is the first thing JARVIS
does *to* a connected account rather than reading it. It is **asked about every time** (an ASK row in the
approval matrix, remembered per task, so an "always" on one task covers no other) and closed to a guest. The card
shows the whole title, due text and description.

jarvis-core makes the call, so the token never reaches Hermes. It is one fixed entry in the provider row's `writes`
table, like a named read: a POST to a fixed path on Todoist's API host, three fixed fields (`content`, required, 500
characters; `due`, 80; `description`, 1000), text only, validated in the house before any request. The model
supplies no address, method, header or path; an unknown field, a wrong type or an over-long value is refused whole,
with no request. It is tried once and never retried: if Todoist cannot be reached the answer says the task may not
have been added, so look at the list before asking again.

**Scope.** Todoist's token answer carries no scope, so the house records the scope it asked for at consent and
keeps it across refreshes. New Todoist consents ask for the narrowest pair, `data:read` plus `task:add` (never the
broad `data:read_write`), joined with a comma. The write is allowed only when the recorded scopes contain `task:add`
exactly (a substring such as `xtask:add` does not count); an account that already holds `data:read_write` passes too.
An account connected before this change holds read only: asked to add a task it makes **no request** and answers
"Todoist was connected for reading only: reconnect Todoist to allow adding tasks". Say "connect my Todoist" again
to approve the new scope.

**A generic house call cannot reach it.** `ha_call_service` with the `oauth` domain (any service, in any spelling,
list or entity-id form) is denied outright in the approval matrix, even with a remembered "always" on house service
calls: the connect tools are the only way to a connected account. On the house's own service route (REST and the
socket's `call_service`) `oauth.start`, `revoke`, `read`, `test` and `write` also refuse a token that pairing minted
(403 "this token may not do this"); the same service check refuses every service in `GATED_DOMAINS` (`lock`, `notify`)
and `GATED_SERVICES` (`web.browse`, `files.write`, `code.run`, `code.push_branch`, `calendar.create_event`,
`calendar.rsvp`), and `ServiceRegistry.async_call` refuses an owner-only service whose caller identity is a paired token.

**A paired token may do only what `aura/paired_access.py` lists** (the generated page
[paired-access](../reference/paired-access.md) shows every command, route and event with the client that needs it). A token the pairing flow minted lives on a phone
or desktop that can be lost, so it is denied by default at both auth doors: the REST gate (`require_token`) and the
WebSocket dispatch answer anything off the list with the same fixed `forbidden` ("this token may not do this", 403 on
REST). The list is the device channel (`jarvis/device/register`, the keepalive), the assistant turn (`conversation/process`,
`assist_pipeline/*`, `subscribe_events`, `jarvis/approve`), the notes, memory, notification, task and surface lists, and
the REST routes the phone and the desktop agent really call (notes, voice enrolment, models, map packs and routes), one
comment per entry naming the client. A paired token therefore cannot mint, list or delete tokens, call a service, write
a state, fire an event, create or run an automation, script, scene, schedule or authored tool, call `tools/call`, or
change a setting; a token minted by a console or house token is not paired and is not limited. What this does not cover:
anything relayed with the house token (Hermes, the adapter, the console's relay) and what the assistant itself does for a
`conversation/process` turn, which is bounded by its own approvals; automations and in-house calls carry no token
identity and are not stopped; `status` and the other non-owner-only services reached through the assistant are not
gated by the registry check.

**Behind that door a paired token is narrowed too (T-1378).** On the voice routes it cannot create an owner, enrol into
an owner label that exists, or forget everyone or the owner (the first owner of a house with none is allowed). **The
owner re-enrols their own voice from the console after signing in** (**SETTINGS › Voice → ENROL**): the console relays the
call under the server-held house token, which is never paired, so the phone's refusal does not reach it
(`test_the_owner_token_enrols_into_the_owner_label_where_a_paired_token_is_refused`; the relay's side is pinned in
`aura-web/src/lib/server/speakerRelay.test.ts`). Its
`subscribe_events` must name one event on `WS_EVENTS` (no missing type, no `*`; consent and companion-message events are
not on it). A device id another live token registered cannot be re-registered by a paired token (a revoked holder is let
go; a console or house token may take it over). A paired device's `device_event` is always untrusted, whatever `trust`
it sends. Still open on purpose: the phone is the owner's approval surface, and the request it answers is raised by the
house's own token or an automation, never by the phone's token, so a paired token may answer ANY held request
(`jarvis/approve`) and hears every approval event. **A stolen paired token can approve held requests until it is
revoked: revoke it as soon as a phone is lost.** Its activity strip also hears `state_changed`, MQTT
and tool events. **A token minted before T-1262 carries no paired flag**, reads as not paired, and keeps full reach until the
device is paired again: re-pair every phone and desktop that was paired before then.

**A paired device that should not approve (T-1380).** Every paired token carries an `approver` flag, on for a new
pairing and for every token stored before it existed, shown in the token list (`GET /api/auth/tokens`,
`config/token/list`). Turn it off for a desktop or a shared tablet with a console or house token:
`PATCH /api/auth/tokens/{id}` with `{"approver": false}` (or `config/token/update`); a paired token cannot change it.
With it off the device is refused `jarvis/approve` and a subscription to `jarvis_approval_required` /
`jarvis_approval_resolved`, and a subscription it already holds stops delivering them on the next event. A coding
job's held edit is answered through the same command, so the same switch governs it. It is a narrowing, not a fix for a
stolen token that is still an approver: revoke that. It does not touch a token that is not paired.

**A lost answer.** If the call from Hermes to the house times out or the house is unreachable, `connect_write`
answers "the task may or may not have been added: check the list before trying again", never a plain "unreachable".

**Not checked against Todoist** (GAPS G18): the scope names `data:read` and `task:add` (and `data:read_write`), the
comma separator in the consent link, the add-task path `POST /api/v1/tasks` and the field names `due_string` and
`description`, and (T-1415) the `due` read's path `GET /api/v1/tasks/filter` with its `query` parameter (`today | overdue`)
and `limit`, were written from memory, not from Todoist's current documentation or a live app, and each is marked as a guess in the
`PROVIDERS` row. Tested against a fake provider only. The action list is pinned in `tests/contracts/connect-reads.json`
(`writes`), read by both suites.

## Where tokens live

Fernet-encrypted in the jarvis-core storage directory; the key is a separate `0600` file beside them. Back
up neither into a place you would not put a password. Deleting the key file makes every stored token
unreadable and every account needs consent again.
