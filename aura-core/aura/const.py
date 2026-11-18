"""Shared constants for Jarvis Core."""

from __future__ import annotations

VERSION = "0.1.0"

# --- event types -----------------------------------------------------------
EVENT_STATE_CHANGED = "state_changed"
EVENT_SERVICE_REGISTERED = "service_registered"
EVENT_CALL_SERVICE = "call_service"
EVENT_JARVIS_START = "jarvis_start"
EVENT_JARVIS_STOP = "jarvis_stop"
EVENT_ENTITY_REGISTRY_UPDATED = "entity_registry_updated"
EVENT_DEVICE_REGISTRY_UPDATED = "device_registry_updated"
EVENT_AREA_REGISTRY_UPDATED = "area_registry_updated"
EVENT_AUTOMATION_TRIGGERED = "automation_triggered"

#: An automation whose action list raised. Fired beside the log line, because a
#: log line on a headless box is not a notification: the 3am automation is the
#: one everybody writes and nobody watches, and the first sign of trouble used
#: to be noticing weeks later that something had stopped happening. An
#: automation can trigger on this to tell you.
EVENT_AUTOMATION_FAILED = "automation_failed"

#: A task's lifecycle, as three moments an automation can act on, rather than
#: the `jarvis_task_updated` firehose that also carries every progress tick.
#: Deriving "it just finished" from an update means knowing the status BEFORE
#: the update, which a listener does not have — so "tell me when the research
#: is done" became a notification per tick. `tasks.py` fires these on the
#: transition only; `automation/triggers.py` maps them to `platform: task`.
EVENT_TASK_STARTED = "jarvis_task_started"
EVENT_TASK_COMPLETED = "jarvis_task_completed"
EVENT_TASK_FAILED = "jarvis_task_failed"
#: Cancelled is its own event and NOT a failure: somebody asked it to stop and
#: it did. Folding it into `failed` would page a human about a thing a human
#: just did.
EVENT_TASK_CANCELLED = "jarvis_task_cancelled"

#: Every voice-pipeline event, mirrored onto the bus as
#: `{"run_id", "type", "data", "pipeline", "device_id"}`. Defined here rather
#: than in `voice/pipeline.py` so the automation layer can listen for it on a
#: build with no voice stack installed — importing the runner to learn a string
#: is how an optional dependency becomes a required one.
EVENT_VOICE_PIPELINE = "voice_pipeline_event"
#: The one pipeline event `platform: wake_word` cares about.
VOICE_WAKE_END = "wake_word-end"

#: An editable setting changed — from the console, the REST API or the model's
#: `change_setting` tool, which are one write path (`api/common.py`
#: `async_set_setting`). Fired from that one place so a surface that shows
#: settings can refresh without polling, and so an automation can notice a
#: change it did not make. Carries `key`, `previous`, `value`, `applied`,
#: `restart_required` and `origin` (who wrote it: `api`, `llm`), never a
#: secret — the allowlist in `settings.py` holds none.
EVENT_SETTING_CHANGED = "jarvis_setting_changed"

MATCH_ALL = "*"

# --- common states ---------------------------------------------------------
STATE_ON = "on"
STATE_OFF = "off"
STATE_HOME = "home"
STATE_NOT_HOME = "not_home"
STATE_UNKNOWN = "unknown"
STATE_UNAVAILABLE = "unavailable"
STATE_OPEN = "open"
STATE_CLOSED = "closed"
STATE_LOCKED = "locked"
STATE_UNLOCKED = "unlocked"
STATE_PLAYING = "playing"
STATE_PAUSED = "paused"
STATE_IDLE = "idle"

# --- domains ---------------------------------------------------------------
DOMAIN_LIGHT = "light"
DOMAIN_SWITCH = "switch"
DOMAIN_CLIMATE = "climate"
DOMAIN_COVER = "cover"
DOMAIN_LOCK = "lock"
DOMAIN_FAN = "fan"
DOMAIN_MEDIA_PLAYER = "media_player"
DOMAIN_BUTTON = "button"
DOMAIN_NUMBER = "number"
DOMAIN_SELECT = "select"
DOMAIN_TEXT = "text"
DOMAIN_NOTIFY = "notify"

# Domains that ALWAYS require explicit human approval (Tier 3).
GATED_DOMAINS = frozenset({DOMAIN_LOCK, DOMAIN_NOTIFY})

#: Individual `domain.service` calls that ALWAYS require explicit human
#: approval, whichever route reaches them.
#:
#: ## Why this is separate from GATED_DOMAINS
#:
#: `GATED_DOMAINS` gates a whole ENTITY domain: every `lock.*` call is
#: dangerous because every entity in it is a door. That is the wrong shape for
#: an integration domain where one service is dangerous and its neighbours are
#: not — `code.run` edits a repository, while
#: `code.result` reads a job's outcome. Gating the domain would
#: hold a status poll for a human; gating nothing held a shell command for
#: nobody.
#:
#: ## The hole this closes
#:
#: The tier system decides a TOOL's tier in code, and `start_coding_job`,
#: `write_file` and a writing `web_browse` batch are all Tier 3. But each
#: of those verbs is ALSO registered as a service, so an automation can call it
#: directly — and `reach.gated_reach` only ever compared a call's *domain*
#: against `GATED_DOMAINS`. `code` is not an entity domain, so it
#: matched nothing:
#:
#:     automations:
#:       - alias: Tidy up
#:         triggers: [{platform: time, at: "03:00:00"}]
#:         actions: [{service: code.run, data: {task: "..."}}]
#:
#: `create_automation` wrote that at Tier 1 and `automation_control` ran it at
#: Tier 1, so the model could author a shell command and then run it with no
#: human in the loop — while calling `execute_command` directly was correctly
#: held. `async_execute` forwards the approval secret *because* "the human has
#: already said yes", a guarantee that only ever held for the tool.
#:
#: This is the same lesson as the plural-key bug in `automation/reach.py`: the
#: analysis is only as good as what it is handed, and two ways to say one thing
#: means one of them gets forgotten. `tests/test_gated_services.py` pins every
#: Tier-3 tool against its service twin so a new one cannot be added without
#: confronting this.
GATED_SERVICES = frozenset(
    {
        # Drives a real browser. The TOOL escalates only for a batch that
        # clicks or types (`is_write_batch`); the service takes the same steps
        # with no such check. Read-only batches are the minority and a
        # confirmation is the cheap side of this module's rule, so the whole
        # service is held rather than re-deriving "is this a write" here, where
        # a wrong answer is silent.
        "web.browse",
        # Adds a device Home Assistant discovered, and its integration, to the house. Tool form:
        # the Hermes tool `house_ha_confirm`, which asks; an automation reaching this service
        # would add whatever a device on the network announced, with no one asked (T-1549).
        "home_assistant.confirm_discovered",
        # Writes a file into one of the user's places. Tool form: `write_file`
        # (Tier 3). The tool asks every time; without this the SERVICE would be
        # the way round it, and "overwrite the note" is a loss with no undo.
        "files.write",
        # Starts a Jarvis Code job, which edits files in a real repository on
        # a branch of its own and runs that repository's checks. Tool form:
        # `start_coding_job` (Tier 3). `code.repositories` and `code.result` are its
        # ungated neighbours and read nothing but what the operator configured
        # and what a finished job produced — which is exactly the shape this
        # set exists for, and why it names calls rather than the domain.
        "code.run",
        # Pushes a branch to GitHub or GitLab. Tool form: `push_branch`
        # (Tier 3). Outward-facing in the way this set exists for: it puts
        # code on a server other people can see, and deleting a local file
        # does not undo it. `code.clone_repository` is its ungated neighbour
        # and only reads something the operator's allow-list already permits.
        "code.push_branch",
        # Puts an event on a calendar every device in the house syncs, and answers an
        # invitation, which the organiser (a person outside the house) sees. Tool forms:
        # `calendar_create` and `calendar_rsvp` (Tier 3); Hermes asks before its own
        # `calendar_create_event` / `calendar_rsvp` run and then calls these over the house
        # REST with a bearer token, which is not held (see the note on the console's own
        # button in `integrations/code`). What this closes is the other door: a routine
        # naming either service would otherwise run at no tier. `calendar.events` and
        # `calendar.calendars` read, and are not held.
        "calendar.create_event",
        "calendar.rsvp",
    }
)

# --- common attributes -----------------------------------------------------
ATTR_FRIENDLY_NAME = "friendly_name"
ATTR_DEVICE_CLASS = "device_class"
ATTR_UNIT_OF_MEASUREMENT = "unit_of_measurement"
ATTR_SUPPORTED_FEATURES = "supported_features"
ATTR_ICON = "icon"
ATTR_BRIGHTNESS = "brightness"
ATTR_COLOR_TEMP_KELVIN = "color_temp_kelvin"
ATTR_RGB_COLOR = "rgb_color"
ATTR_TEMPERATURE = "temperature"
ATTR_CURRENT_TEMPERATURE = "current_temperature"
ATTR_HVAC_MODE = "hvac_mode"
ATTR_POSITION = "position"
ATTR_VOLUME_LEVEL = "volume_level"
ATTR_MEDIA_TITLE = "media_title"

# --- services --------------------------------------------------------------
SERVICE_TURN_ON = "turn_on"
SERVICE_TURN_OFF = "turn_off"
SERVICE_TOGGLE = "toggle"
SERVICE_RELOAD = "reload"
