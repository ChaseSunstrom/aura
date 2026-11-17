"""What a paired device's own token may do, in one place (T-1378).

A token the pairing flow minted lives on a phone or a desktop that can be lost or
rooted. It is denied by default: it may send the WebSocket command types and reach
the REST routes below, and nothing else. Both auth doors read these two tables
(`WebSocketHandler._dispatch`, `rest.require_token`), so a route or command added
tomorrow is refused to a paired token until someone lists it here on purpose.

The tables were derived from what the two clients send with their own token
(`android-app/.../kotlin`, `aura-desktop/aura_desktop`). Token management, the
generic service call, states and events writes, automation / schedule / tool
authoring and `tools/call`, settings and registry writes are not here, so they are
refused.

Behind the door the allowed commands are narrowed where they would otherwise hand a paired
token the owner's reach (the rules live beside the code they bound): voice identity cannot
make or touch an owner (`api/speaker.py`), `subscribe_events` names one of `WS_EVENTS` (no
match-all, no consent or companion-message events; the table is pinned to the phone's own
subscriptions by `tests/test_paired_access.py`), a device id another live token registered,
or a link with no token id, cannot be taken over, a paired token's enrolment marks stop
extending after `speaker.PAIRED_RUN_MAX`, and a paired device's `device_event` is always untrusted.

Not covered: this is a door, not a sandbox. `jarvis/approve` is on the allowlist because the
phone is the owner's approval surface, and the request it answers is raised by the house's
own token or an automation, never by the phone's token: so a paired token may answer ANY
held request, and a STOLEN paired token can approve held requests until it is revoked.
Revoking the token is the remedy, and for a device that should never approve (a desktop, a
shared tablet) the owner's `approver` switch on the token (T-1380: `PATCH /api/auth/tokens/{id}`
or `config/token/update`, both owner-only and absent from the tables below) is the narrower
one: with it off the token is refused `jarvis/approve` (`api.common.async_approve`) and both
`APPROVAL_EVENTS`, read live so it bites on a socket already open. It is on for a new pairing
and for a token stored before the switch existed. A paired device answering a coding job's
held edit is treated like any other held request: Jarvis Code's gate shares the same
`jarvis/approve` command and events, so the same switch governs it and there is no special
case. The switch is not a defence against a stolen token that is still an approver, and it
does not stop the device's own assistant turns from asking the house's token to act.
`conversation/process` and `assist_pipeline/run` hand
text to the assistant, which acts with the house's own approvals (jarvis-core has no
conversation agent of its own: ADR-0012); a phone still hears `state_changed` and tool
events for its activity strip. A caller that holds the house token (the Hermes plugins, the
adapter's relays) is not a paired token and is not limited by this module. A token minted
before T-1262 carries no paired flag and keeps full reach until the device is re-paired.
"""

from __future__ import annotations

from typing import Final

REFUSED_CODE: Final = "forbidden"
REFUSED_MESSAGE: Final = "this token may not do this"


class PairedRefused(PermissionError):
    """A paired token's identity reached an owner-only service through the registry."""


#: WebSocket command type -> the client that sends it with its own token.
WS_COMMANDS: Final[dict[str, str]] = {
    # the handshake of every long-lived socket
    "ping": "phone DeviceLink/JarvisChannel keepalive; desktop channel.py heartbeat",
    "jarvis/device/register": "phone JarvisChannel and desktop channel.py: announce the device and its abilities",
    # the device channel's replies (device_result, device_event, jarvis_message_result) are push
    # frames handled off the read loop and never reach the door; they need no row.
    "subscribe_events": "phone AssistPipelineClient and TaskFrames: the turn's and the tasks' event feed",
    "conversation/process": "phone DeviceLink and JarvisConversation: a typed turn",
    "assist_pipeline/run": "phone AssistPipelineClient and CompanionVoiceClient: a voice turn",
    "assist_pipeline/pipeline/list": "phone AssistPipelineClient: which pipelines exist",
    "jarvis/approve": "phone ApprovalBridge: answer a consent from the phone's own screen",
    "jarvis/notes/list": "phone JarvisConversation: the notes shown beside a turn",
    "jarvis/memory/list": "phone JarvisConversation: the memory shown beside a turn",
    "jarvis/notifications/list": "phone MomentWatch: the notification feed",
    "jarvis/tasks/list": "phone TaskFrames: the task list",
    "jarvis/surface/list": "phone SurfaceWatch: what the house put on the surface",
    "jarvis/surface/remove": "phone SurfaceWatch: dismiss a surface card",
}

#: (HTTP method, route template under /api) -> the client that calls it with its own token.
REST_ROUTES: Final[dict[tuple[str, str], str]] = {
    ("GET", "/api/notes"): "desktop actions/notes.py: find_note",
    ("POST", "/api/notes"): "desktop actions/notes.py: save_note",
    ("GET", "/api/notes/{note_id}"): "desktop actions/notes.py: find_note by id",
    ("GET", "/api/voice/speaker"): "phone VoiceIdentityClient and desktop enrol_client.py: who is enrolled",
    ("POST", "/api/voice/speaker/enrol"): "phone VoiceIdentityClient and desktop enrol_client.py: enrol a voice",
    ("POST", "/api/voice/speaker/enrolling"): "phone VoiceIdentityClient: enrolment progress",
    ("POST", "/api/voice/speaker/verify"): "phone VoiceIdentityClient: check a voice sample",
    ("POST", "/api/voice/speaker/people"): "phone VoiceIdentityClient: the People screen's write",
    ("DELETE", "/api/voice/speaker"): "phone VoiceIdentityClient and desktop enrol_client.py: forget a voice",
    ("GET", "/api/models/{name}"): "phone ModelStore: download an on-device model",
    ("GET", "/api/maps/regions"): "phone car/holo MapPackSync: which map packs exist",
    ("GET", "/api/maps/packs/{region}"): "phone car/holo MapPackSync: download a map pack",
    ("POST", "/api/maps/route"): "phone car/holo HouseMaps: a route",
    ("POST", "/api/maps/match"): "phone car/holo HouseMaps: snap a fix to the road",
    ("GET", "/api/maps/buildings"): "phone car/holo HouseMaps: buildings near the car",
    ("GET", "/api/maps/live"): "phone car/holo HouseMaps: live overlays near the car",
}


#: Bus event type -> the client that subscribes to it with its own token. `subscribe_events` on a
#: paired socket names one of these and nothing else: no missing type, no "*", so the feed is not
#: the whole bus (device registrations, consents, companion messages, other devices' traffic).
WS_EVENTS: Final[dict[str, str]] = {
    **dict.fromkeys(
        (
            "jarvis_tool_started", "jarvis_tool_finished", "jarvis_task_added",
            "jarvis_task_updated", "state_changed", "jarvis_mqtt_event", "vision_look_started",
            "vision_look_finished", "vision_look_denied", "memory_changed", "jarvis_notification",
            "jarvis_approval_required", "jarvis_approval_resolved", "jarvis_speaker_verdict",
        ),
        "phone AssistPipelineClient: ActivityRows.EVENTS, the strip's whole vocabulary",
    ),
    "jarvis_task_removed": "phone JarvisChannel.watchTasks: TaskBoard.EVENTS",
    "jarvis_surface_changed": "phone JarvisChannel.watchSurface: SurfaceWatch.EVENT",
}


#: The two events a paired token with the owner's `approver` switch off may not hear (T-1380).
APPROVAL_EVENTS: Final = frozenset({"jarvis_approval_required", "jarvis_approval_resolved"})


def ws_allowed(msg_type: object) -> bool:
    return isinstance(msg_type, str) and msg_type in WS_COMMANDS


def event_allowed(event_type: object) -> bool:
    return isinstance(event_type, str) and event_type in WS_EVENTS


def rest_allowed(method: str, route_path: str | None) -> bool:
    """`route_path` is the matched route's template, never the raw URL (`/api/notes/{note_id}`)."""
    return route_path is not None and (method.upper(), route_path) in REST_ROUTES
