HOUSE_AREAS = {
    "name": "house_areas",
    "description": "Rooms/areas of the house with the entities assigned to each. Read-only. Use with ha_call_service to act on a whole room.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

HOUSE_AUTOMATIONS = {
    "name": "house_automations",
    "description": "List the house automations (id, alias, enabled, triggers summary, last triggered). Read-only.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

HOUSE_AUTOMATION_CONTROL = {
    "name": "house_automation_control",
    "description": "Enable, disable or trigger an automation by id. Needs approval (it changes what the house does on its own).",
    "parameters": {
        "type": "object",
        "properties": {"automation_id": {"type": "string"}, "action": {"type": "string", "enum": ["enable", "disable", "trigger"]}},
        "required": ["automation_id", "action"],
    },
}

HOUSE_AUTOMATION_CREATE = {
    "name": "house_automation_create",
    "description": ("Create an automation from a Home-Assistant-style definition: alias, trigger (list), condition (list, optional), "
                    "action (list). Needs approval."),
    "parameters": {
        "type": "object",
        "properties": {
            "alias": {"type": "string"},
            "trigger": {"type": "array", "items": {"type": "object"}},
            "condition": {"type": "array", "items": {"type": "object"}},
            "action": {"type": "array", "items": {"type": "object"}},
            "mode": {"type": "string", "description": "single|restart|queued (default single)"},
        },
        "required": ["alias", "trigger", "action"],
    },
}

HOUSE_DEVICES = {
    "name": "house_devices",
    "description": "The user's paired phone(s) and desktop(s) with presence (online, screen, driving) and the actions each allows. Read-only.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

HOUSE_DEVICE_ACTION = {
    "name": "house_device_action",
    "description": ("Run an action on the user's phone or desktop through the device channel (e.g. torch on, send SMS, open app, "
                    "find my phone). Pass the parameters house_devices lists under params for that action (toggle_torch takes on: false to switch it off). The device applies its own tier policy and may refuse or ask the user; report what came back. Needs approval."),
    "parameters": {
        "type": "object",
        "properties": {"device_id": {"type": "string"}, "action": {"type": "string"}, "params": {"type": "object"}},
        "required": ["device_id", "action"],
    },
}

HOUSE_TELL_USER = {
    "name": "house_tell_user",
    "description": "Tell the user something when no conversation is open: it lands on the console's Notifications panel (always, as a record) and is pushed to a paired phone or desktop when one is present. Use for 'tell me when…' results and scheduled briefs. Answers pushed (devices reached, 0 or 1) and recorded (on the console). Proactive use only: it is throttled (quiet hours 23-7, while driving, 6 an hour, 30 a day) and what is held comes later as one digest, answering held instead of pushed. Pass urgent true only for something that cannot wait (a service down, an empty-house alert); never to deliver an answer the user just asked for, say that in the conversation.",
    "parameters": {"type": "object", "properties": {"message": {"type": "string"}, "title": {"type": "string"}, "urgent": {"type": "boolean"}}, "required": ["message"]},
}

HOUSE_HISTORY = {
    "name": "house_history",
    "description": "What was the temperature earlier, when did the door last open, how has a sensor changed: recent state history of one entity (last N hours) from the house recorder. Read-only.",
    "parameters": {"type": "object", "properties": {"entity_id": {"type": "string"}, "hours": {"type": "integer"}}, "required": ["entity_id"]},
}

HOUSE_SINCE = {
    "name": "house_since",
    "description": "What changed in the house while the owner was out, or since a time: every entity's changes from the house logbook (people, doors and locks, lights and devices, automations, other), grouped, repeats collapsed with a count. since='left' (default) reads when the house last saw the owner go. For an arrive-home catch-up. Read-only.",
    "parameters": {"type": "object", "properties": {
        "since": {"type": "string", "description": "'left' (default), or an ISO 8601 time"},
        "limit": {"type": "integer", "description": "most lines to return (default 60)"}}},
}

HOUSE_TIMER = {
    "name": "house_timer",
    "description": "A kitchen-style timer held by the house: 'ten minutes for the pasta', 'how long left on the tea', 'cancel the timer', 'snooze it'. The house counts it down, rings the phone or desktop this turn came from when it finishes (else the one the owner is on) and leaves a card on the console; it survives restarts. Say remaining_spoken back rather than working it out. Not for 'at seven' or 'tomorrow', and not for a reminder that carries words ('remind me in five minutes that the oven is on'): those are cronjob. The phone's own alarm app (phone_action set_timer) only when the owner asks for it on the phone.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["start", "list", "cancel", "pause", "resume", "snooze"]},
        "duration": {"type": "string", "description": "for start: seconds, '10m', '1h30m', '10:00'"},
        "label": {"type": "string", "description": "the timer's name: 'pasta', 'tea'; optional for cancel/pause/resume/snooze when only one is running"},
        "minutes": {"type": "integer", "description": "for snooze: how long, default 5"}},
        "required": ["action"]},
}

HOUSE_UNDO = {
    "name": "house_undo",
    "description": "'Undo that', 'put it back', 'turn it back': reverses your own most recent house change in the last few minutes, not one someone else or an automation made (lights, switches, covers, climate, media), putting each thing back as it was. Locks, alarms, messages and scripts are never reversed, and the answer says so. Say its message back as it is; ok false means nothing was put back.",
    "parameters": {"type": "object", "properties": {}},
}

VOICE_PERSON_FORGET = {
    "name": "voice_person_forget",
    "description": ("Forget one person from the house's voice identity: deletes their voiceprint, so the speaker gate treats their voice "
                    "as a stranger's until they are enrolled again (which only a person can do, on the phone or the console). Not "
                    "person_forget, which is a memory relation. Always one named person, never everyone. Needs approval, every time."),
    "parameters": {"type": "object", "properties": {"person": {"type": "string", "description": "the enrolled person's name, as the console's People screen shows it"}},
                   "required": ["person"]},
}

DEVICE_NAME = {
    "name": "device_name",
    "description": ("Give a device found on the home network a name (the console's Devices screen does the same): the house remembers it "
                    "and makes it a device_tracker. mac is the device's address from the network census. Not for paired phones or "
                    "desktops. Needs approval."),
    "parameters": {"type": "object", "properties": {"mac": {"type": "string", "description": "aa:bb:cc:00:11:22"}, "name": {"type": "string"}},
                   "required": ["mac", "name"]},
}

DESTRUCTIVE ={"house_automation_control", "house_automation_create", "house_device_action"}
