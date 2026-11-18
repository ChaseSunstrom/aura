"""What JARVIS says on a chat platform echoes the HUD (GLASS_HUD §10, §11).

A line that reaches Telegram or Discord carries the reactor's own mark for its
level — the steady ring for a plain or a settled notice, the ring with a gap
for one that needs the person, the ring with a bar for one that failed — and
then a sentence in the copy rules' register: sentence case, the thing named by
what the person understands, an error saying what happened and what to do
next, no ``·`` or dash joining two half-sentences. The glyphs come from
:mod:`_glyphs`, which ``design/build.py`` generates from the one table the
console theme and the web's ``STATE_GLYPH`` are generated from; the level
table mirrors ``tests/contracts/notify.json`` and
``hermes/plugins/tests/test_messages.py`` reads the contract to check it.

Three shapes, and which plugin speaks each:

* :func:`alert`, :func:`recovered`, :func:`blind` — the GPU watchdog
  (``gpu-telemetry/watch.py``), whose stdout the scheduler delivers verbatim.
* :func:`goal` — the ``/jgoal`` replies (``aura-goals``). The lifecycle
  glyphs (set, continuing, parked, done, failed) are Hermes' own, because
  Hermes' goal loop prints its status line beside these replies in the same
  chat and two vocabularies for one goal is a person wondering whether ⏳ and
  ◡ are the same thing. ``set`` is the reactor's goal glyph, and the test
  pins the rest against ``hermes_cli.goals``.
* :func:`approval` — the sentence behind Hermes' approval card
  (``_approval_hook.matrix_gate``). No glyph: the card's header is Hermes'
  (``⚠️ Command Approval Required``) and ours would land after its
  ``Reason:`` label, a mark in the middle of a sentence. What the template
  owns there is the sentence: the rule, then the arguments, as two sentences
  rather than an em dash.

Markdown: a line is plain text. Hermes converts standard markdown to each
platform's dialect and escapes the rest (Telegram's MarkdownV2 turns ``84.2``
into ``84\\.2`` on the wire, which is why the delivery proof's fake unescapes
before it looks), so a template must never pre-escape — that doubles every
backslash — and must not emit a character Hermes reads as markup: no bare
``*`` or ``_`` around a word, no leading ``#`` or ``>``, no ``~~`` or ``||``.
The test renders every template and round-trips it through the escaping.

What this does *not* cover: the model-written deliveries (the morning brief,
the evening summary, the hourly health line). Their structure is the job's
prompt in ``hermes/cron/jobs.tsv``; a glyph typed by a model is exactly the
drift the generated table exists to prevent, so those lines carry none.
"""
from __future__ import annotations

import json
import re
import unicodedata
from urllib.parse import urlsplit

from . import _glyphs

#: Level -> the reactor state whose glyph it carries (``levels`` in the contract).
LEVEL_STATE: dict[str, str] = {
    "info": "idle",
    "ok": "idle",
    "attention": "attention",
    "danger": "alert",
}

#: Template -> level (the contract's ``templates``), for the ones this module renders.
TEMPLATE_LEVEL: dict[str, str] = {
    "recovered": "ok",
    "alert": "danger",
    "approval": "attention",
    "goal": "info",
}

#: The goal lifecycle as Hermes' loop prints it (``hermes_cli/goals.py``); ``set``
#: is the reactor's goal overlay. Pinned by the test against Hermes when it is
#: installed, so these cannot drift from the status line printed beside them.
GOAL_GLYPHS: dict[str, str] = {
    "set": _glyphs.STATES["goal"]["glyph"],
    "continuing": "↻",
    "parked": "⏳",
    "done": "✓",
    "failed": "✗",
}


def glyph(level: str) -> str:
    """The reactor's static glyph for a notice level; ``info`` for an unknown one."""
    return _glyphs.STATES[LEVEL_STATE.get(level, "idle")]["glyph"]


def line(level: str, text: str) -> str:
    """``"<glyph> <text>"`` — one line, the mark first, the sentence after."""
    text = " ".join(str(text or "").split())
    return f"{glyph(level)} {text}".rstrip()


def alert(text: str) -> str:
    """Something crossed a limit or failed: the ring with a bar."""
    return line(TEMPLATE_LEVEL["alert"], text)


def recovered(text: str) -> str:
    """Something is back under its limit: the steady ring, settled."""
    return line(TEMPLATE_LEVEL["recovered"], text)


def blind(what_failed: str, at: str, next_step: str) -> str:
    """A watcher that cannot see: it needs the person, and says what to do.

    ``what_failed`` is the clause after "no readings", ``at`` the clock time,
    ``next_step`` the sentence that says what to check — an error that stops
    at "it failed" leaves the reader to guess where.
    """
    return line(
        TEMPLATE_LEVEL["approval"],
        f"GPU watchdog: no readings, {what_failed} ({at}). "
        f"Nothing is being watched until it comes back. {next_step}",
    )


def goal(event: str, text: str) -> str:
    """A goal's lifecycle line: ``goal("set", "Goal set (20-turn budget): …")``."""
    if event not in GOAL_GLYPHS:
        raise ValueError(f"not a goal event: {event!r} (one of {sorted(GOAL_GLYPHS)})")
    text = " ".join(str(text or "").split())
    return f"{GOAL_GLYPHS[event]} {text}".rstrip()


def approval(tool: str, reason: str, detail: str = "") -> str:
    """The reason behind an approval card: the rule, then the arguments.

    ``"ha_call_service acts on the house. domain: lock, service: unlock,
    entity_id: lock.front_door."`` — the rule is still named, or the owner
    cannot tell a standing rule from a one-off, and the arguments still ride
    in the sentence because Hermes' approval has no other slot for them.
    """
    rule = f"{tool} {' '.join(str(reason or '').split())}".strip()
    if not rule.endswith("."):
        rule += "."
    detail = " ".join(str(detail or "").split())
    if not detail:
        return rule
    # A clipped summary already ends in an ellipsis; a full stop after one
    # reads as a typo.
    if detail[-1] not in ".…":
        detail += "."
    return f"{rule} {detail}"


#: tool -> (the plain ask, what changes, can it be put back). ``{field}`` in the ask is filled from
#: the call's arguments (a list joined, a long value clipped, a missing one left out). "no" means
#: hold-to-confirm on the apps. A tool with no entry here gets the safe default in :func:`card`.
_DESKTOP = ("Act on your desktop", "Clicks, types or opens things on your desktop as you.", "no")
_AUTOMATION = ("Change what the house does by itself", "A routine or automation will run, or stop running, on its own.", "yes")
_NOTES = ("Change your notes", "Writes, edits or moves a note.", "no")
_BROWSER_RAW = ("Drive the browser directly", "Runs raw commands in the open browser page, outside the per-action checks.", "no")
_VAULT = ("Use a saved login", "Uses, stores or unlocks a credential in the browser's vault.", "no")
_N8N_WRITE = ("Change an n8n workflow", "Replaces the workflow definition; the old one is not kept.", "no")
_FILE_WRITE = ("Write {path}", "Writes a file outside Jarvis's files area.", "no")
_CARD: dict[str, tuple[str, str, str]] = {
    "artifact_delete": ("Delete an artifact", "Its bytes are removed; it may be on screen right now.", "no"),
    "browser_cdp": _BROWSER_RAW, "browser_console": _BROWSER_RAW, "browser_exec": _BROWSER_RAW,
    "browser_vault_enter_code": _VAULT, "browser_vault_fill": _VAULT,
    "browser_vault_save_login": _VAULT, "browser_vault_unlock": _VAULT, "browser_vault": _VAULT,
    "calendar_create_event": ("Add {title} to your calendar", "Every device in the house syncs the change.", "yes"),
    "calendar_move_event": ("Move a calendar event", "Every device in the house syncs the change.", "yes"),
    "calendar_rsvp": ("Answer an invitation as you", "The organiser sees your answer.", "no"),
    "comfyui_submit": ("Start an image render", "Takes the GPUs until it finishes.", "yes"),
    "computer_use": _DESKTOP, "desktop_click": _DESKTOP, "desktop_focus": _DESKTOP,
    "desktop_key": _DESKTOP, "desktop_open": _DESKTOP, "desktop_scroll": _DESKTOP,
    "desktop_type": _DESKTOP,
    "cronjob": ("Change what runs on a schedule", "A scheduled job is added, changed, fired or removed.", "yes"),
    "desktop_clipboard": ("Read or replace your desktop clipboard", "The clipboard may hold passwords or private text.", "no"),
    "desktop_files": ("Read {path} on your desktop", "Jarvis sees the file's contents.", "yes"),
    "desktop_lock": ("Lock or sleep your desktop", "The screen locks; you sign back in.", "yes"),
    "desktop_sleep": ("Lock or sleep your desktop", "The screen locks; you sign back in.", "yes"),
    "desktop_notes": ("Save a note on your desktop", "A new note is written.", "yes"),
    "desktop_run": ("Run on your desktop: {command}", "A shell command runs on your desktop as you.", "no"),
    "desktop_window": ("Close or minimise a window on your desktop", "Unsaved work in it may be lost.", "no"),
    "execute_code": ("Run a Python program", "It can call every other tool, so it can do anything they can.", "no"),
    "fact_store": ("Change what Jarvis remembers", "A remembered fact is rewritten or removed.", "no"),
    "memory": ("Change what Jarvis remembers", "A memory is rewritten or removed.", "no"),
    "fileshare_delete": ("Delete {path} on the file share", "The household file share changes for everyone.", "no"),
    "fileshare_write": ("Write {path} on the file share", "The household file share changes for everyone.", "no"),
    "goal_clear": ("Drop the standing goal", "Jarvis stops working toward it and its gates lift.", "yes"),
    "goal_pause": ("Pause the standing goal", "Jarvis stops working toward it until you resume it; its gates are kept.", "yes"),
    "goal_resume": ("Resume the standing goal", "Jarvis starts working toward it again on its own, with a fresh turn budget.", "yes"),
    "session_delete": ("Delete the chat {_chat}", "Its transcript is removed for good; it cannot be brought back.", "no"),
    "chat_model_set": ("Switch this chat to the model {model}", "A different model answers from the next turn, with its own speed and cost; switch back to undo.", "yes"),
    "theme_apply": ("Wear the theme {id}", "Every screen in the house changes to that look, as previewed.", "yes"),
    "ha_call_service": ("Call {domain}.{service} on {entity_id}", "Changes a device in the house; a lock, door or alarm is a physical-security change.", "yes"),
    "house_automation_control": _AUTOMATION, "house_automation_create": _AUTOMATION,
    "routine_add": ("Save a routine", "The house runs it by itself from now on.", "yes"),
    "house_device_action": ("Act on your phone or desktop: {action}", "The device does it, under its own rules.", "no"),
    "house_undo": ("Put Jarvis's last house change back", "The device returns to how it was.", "yes"),
    "house_ha_confirm": ("Add the {integration} device Home Assistant found",
                         "Home Assistant adds its integration and the device joins the house.", "no"),
    "connector_call": ("Call {tool} on {connector}", "An outside service does something, and its answer is untrusted text.", "no"),
    "connector_enable": ("Turn on {name}", "Jarvis gains the tools of an outside service; each call still asks.", "yes"),
    "mcp_add": ("Connect {name} with a token or inside the house", "A token is sent to that server, or a request reaches inside the house.", "yes"),
    "api_add": ("Connect {name} with a token or inside the house", "A token is sent to that server, or a request reaches inside the house.", "yes"),
    "integration_enable": ("Change which plugins load", "What Jarvis can do changes after a restart.", "yes"),
    "integration_install": ("Install {id}", "Code from a catalogue lands on this host.", "no"),
    "integration_update": ("Update {id}", "Code from a catalogue replaces what is installed.", "no"),
    "integration_remove": ("Remove {id}", "The installed plugin, skill or server is deleted.", "no"),
    "service_setup": ("Set {service} up for AURA", "AURA creates its owner or a new API key there and uses it.", "yes"),
    "connect_start": ("Connect your {provider} account", "A consent link is sent to your device; nothing is linked until you approve there.", "yes"),
    "connect_revoke": ("Disconnect your {provider} account", "The stored token is deleted and the provider is told to void it; you would connect again to undo.", "yes"),
    "connect_write": ("Add the task {_task} to your Todoist", "A task appears on your Todoist list; you would delete it there to undo.", "no"),
    "jellyfin_play": ("Play {query} on a screen", "Starts playback on someone's screen.", "yes"),
    "llama_swap_load": ("Swap the loaded model", "Whatever is using the current model is interrupted.", "yes"),
    "llama_swap_unload": ("Swap the loaded model", "Whatever is using the current model is interrupted.", "yes"),
    "model_download": ("Download the model {repo}", "Pulls gigabytes onto the AI machine's disk; delete the model to get the space back.", "yes"),
    "model_add": ("Add the model {id} to the model server", "Changes the model server's configuration; delete the model to undo.", "yes"),
    "model_settings": ("Change the settings of the model {model}", "Rewrites the model's launch command; it takes effect the next time it loads, and you can set the old values back.", "yes"),
    "model_delete": ("Delete the model {model}", "Removes it from the model server, and its downloaded files if asked; freed disk does not come back.", "no"),
    "voice_person_forget": ("Forget the voice of {person}", "Deletes their voiceprint for good; the house hears them as a stranger until they enrol again.", "no"),
    "forget_recent": ("Forget the last {minutes} minutes", "Erases the habit notes, list items, routines, people and places saved in that time, and the house's recorded history and memory notes from it, and the files the adapter and the desktops kept from it (saved pictures, screenshots). This conversation, long-term memory, the audit log and backups stay.", "no"),
    "device_name": ("Name the device {mac} {name}", "Adds a tracker for it in the house; name it again to change it.", "yes"),
    "texts_reply": ("Text {conversation}: {text}", "Sent from your phone as you; it cannot be unsent.", "no"),
    "emergency_contacts_set": ("Set your emergency contacts to {contacts}", "These people are texted your location and called when you ask for help.", "no"),
    "calls_place": ("Call {to}", "The call starts on your phone and they hear it ring; it cannot be undone.", "no"),
    "calls_back": ("Call back {call_id}", "The call starts on your phone and they hear it ring; it cannot be undone.", "no"),
    "calls_answer": ("Answer the ringing call", "The caller is connected to you now.", "no"),
    "calls_decline": ("Decline the ringing call", "The caller hears it end; a reply texts them a fixed line.", "no"),
    "calls_rule": ("Change how calls from strangers are handled", "Later calls from people you have not saved ring, are silenced or are rejected.", "yes"),
    "mail_send": ("Send mail to {to}", "Mail goes out from your address; it cannot be unsent.", "no"),
    "memory_import": ("Remember these from the export: {_facts}", "Jarvis stores each as a fact about you and reads them in later conversations.", "yes"),
    "memory_proposals": ("Remember what was proposed: {_proposed}", "Jarvis stores each as written and reads it back as your own in every later conversation.", "yes"),
    "map_region_remove": ("Remove the {id} map from the house", "The car's offline map and routing there go; downloading it again takes a while.", "yes"),
    "phone_island_set": ("Change the island settings on your phone", "Where the assistant's island sits, what it shows and whether it covers the status bar change; the same switches are in Settings, Island.", "yes"),
    "manage_connections": ("Connect an outside account", "An account is linked to Jarvis, or code is installed for it.", "no"),
    "mcp__notes__create_directory": _NOTES, "mcp__notes__edit_file": _NOTES,
    "mcp__notes__move_file": _NOTES, "mcp__notes__write_file": _NOTES,
    "media_fetch": ("Download a video from {url}", "A file from the internet is saved on this host.", "yes"),
    "n8n_activate_workflow": ("Start or stop an n8n workflow", "It fires, or stops firing, on its own schedule.", "yes"),
    "n8n_create_workflow": _N8N_WRITE, "n8n_update_workflow": _N8N_WRITE,
    "n8n_credential_create": ("Store a credential in n8n", "Workflows can act with it from then on.", "yes"),
    "n8n_credential_delete": ("Delete an n8n credential", "Workflows that use it stop working.", "no"),
    "n8n_delete_workflow": ("Delete an n8n workflow", "The workflow and its run history are gone.", "no"),
    "n8n_trigger": ("Run an n8n workflow", "It does whatever it does outside Jarvis.", "no"),
    "patch": _FILE_WRITE, "write_file": _FILE_WRITE,
    "plan_approve": ("Approve a plan", "Each step in it goes ahead without asking again.", "no"),
    "process": ("Run a command", "A command runs on this host.", "no"),
    "terminal": ("Run: {command}", "A shell command runs on this host; a delete has no undo.", "no"),
    "proxmox_guest_action": ("{action} a Proxmox machine", "A virtual machine starts, stops or reboots, and what runs on it is interrupted.", "yes"),
    "selfext_open_mr": ("Open a merge request", "A branch is pushed to origin and a request opened.", "no"),
    "selfext_verify": ("Run a plugin's tests", "Code Jarvis wrote runs on this host.", "yes"),
    "capability_set": ("Set {capability} to {level}", "What Jarvis may do in this area without asking changes.", "yes"),
    "settings_set": ("Change {key}", "How Jarvis behaves changes; settings_undo puts it back.", "yes"),
    "settings_undo": ("Undo a settings change", "A setting returns to its earlier value.", "yes"),
    "skill_manage": ("Change a skill", "A procedure Jarvis follows is created, edited or deleted.", "yes"),
    "watch_add": ("Watch {url}", "Jarvis polls an address on this host or the house network.", "yes"),
}
#: Home-keyed grants end in this: Hermes remembers "always" by rule_key, so a yes given at home
#: is stored apart and a request made away never finds it (T-0606).
AT_HOME = ":at-home"
_FIELD_CLIP = 60
#: A reply is shown whole (the tool refuses more than this), so the owner approves the exact words.
_FIELD_CLIPS = {"text": 480}


class _Args(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _fill(template: str, args: dict) -> str:
    vals = _Args()
    for key, value in (args if isinstance(args, dict) else {}).items():
        text = ", ".join(map(str, value)) if isinstance(value, (list, tuple)) else str(value)
        text = " ".join(text.split())
        clip = _FIELD_CLIPS.get(key, _FIELD_CLIP)
        vals[key] = text if len(text) <= clip else text[:clip - 1] + "…"
    try:
        out = template.format_map(vals)
    except (ValueError, IndexError, AttributeError):  # a stray brace in the table, never the call's
        out = template
    return " ".join(out.split())


#: What aura-emergency drops from a number before it stores it.
_NUMBER_NOISE = re.compile(r"[\s().-]")


#: How much typed text one card shows; longer text is said to be longer (T-1494).
TYPED_SHOWN = 900


#: Letters that draw as nothing (Hangul fillers, the braille blank): shown by number.
_BLANK_LETTERS = frozenset("ᅟᅠㅤﾠ⠀")
#: Unicode classes with no shape of their own: controls, formats, separators, marks that
#: only combine, and code points that are not characters.
_SHAPELESS = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zs", "Zl", "Zp", "Mn", "Me"})


def _visible(text: str) -> str:
    """Text as a card shows it when the card is the review: every character that would be
    typed or sent is on the card, in a shape that can be seen.

    One space between two other characters stays a space. Every other white space (a second
    space in a run, one at either end, a tab, a line break), every character with no shape of
    its own and every blank-drawing letter is written out by name or number, and a backslash
    is doubled so a written-out character cannot be mistaken for typed ones. Each of these
    is something the page receives (a line break can submit a form, and a count of spaces is
    data), and the card's one line would otherwise show it as a space or as nothing. The
    result has no run of white space, so the card's own collapsing changes nothing in it.
    """
    names = {"\n": "\\n", "\t": "\\t", "\r": "\\r", "\\": "\\\\", " ": "\\x20"}
    out = []
    for at, ch in enumerate(text):
        if ch == " " and 0 < at < len(text) - 1 and not text[at - 1].isspace() and not text[at + 1].isspace():
            out.append(ch)
        elif ch in names:
            out.append(names[ch])
        elif ch.isspace() or ch in _BLANK_LETTERS or unicodedata.category(ch) in _SHAPELESS:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def _host(address: str) -> str:
    """The host of an address, for a card; "" when it has none that can be read."""
    try:
        host = urlsplit(address).hostname or ""
    except ValueError:
        return ""
    # A name with letters outside ASCII is shown in the form the network uses (xn--…), so
    # one that only looks like a known name does not read as it on the card.
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return host.encode("ascii", "backslashreplace").decode("ascii")


def card(tool: str, args: dict, fallback_reason: str, why: str, *, home: bool,
         composed: list[str] | None = None, typed: list[str] | None = None, page: str = "",
         shell: tuple[str, str] | None = None, masked: bool = False) -> str:
    """The five lines the apps parse into one approval card (``tests/contracts/approval-event.json``).

    ``What`` one plain ask, ``Why`` what started it, ``Impact`` what changes, ``Undoable`` yes or
    no (no = hold to confirm), ``Home`` whether a grant "when I'm home" can apply right now. A
    tool with no entry is read as irreversible: the safe side is the harder button. Every field is
    one line, because the parser reads the last five lines of the reason and an argument must not
    be able to forge them. Not claimed: that the owner's yes was informed; the fields are
    summaries of the call, and the arguments are in the sentence above them.
    """
    ask, impact, undoable = _CARD.get(tool) or (f"Run {tool}", fallback_reason or "Changes something.", "no")
    if tool == "emergency_contacts_set":
        # The card is where a wrong number is caught: every name and number, never a clipped list.
        people = args.get("contacts") if isinstance(args, dict) else None
        # As the tool stores them (aura-emergency tools._clean): a number is read without
        # its spaces and dashes, so the card must not show a different one. Cut only with a mark.
        def shown(text: str, top: int) -> str:
            return text if len(text) <= top else text[:top] + "…"
        named = "; ".join(
            shown(" ".join(str(c.get("name")).split()), 40) + " " + shown(_NUMBER_NOISE.sub("", str(c.get("number"))), 20)
            if isinstance(c, dict) else "(not a contact)"
            for c in (people if isinstance(people, list) else []))
        ask = ask.replace("{contacts}", "{_people}") if named else "Remove all your emergency contacts"
        args = {"_people": named}
        _FIELD_CLIPS.setdefault("_people", 600)
    if tool == "memory_import":
        # The card is the review: every fact of the batch whole, as it will be stored.
        from . import _memory_import
        listed = _memory_import.card_facts(args if isinstance(args, dict) else {})
        ask = ask if listed else "Read an export that has no facts in it"
        args = {"_facts": listed}
        _FIELD_CLIPS.setdefault("_facts", _memory_import.BATCH * (_memory_import.MAX_FACT_CHARS + 6))
    if tool == "memory_proposals":
        # The card is the owner's yes to each fact (T-1500): every one whole, who proposed it
        # and what that turn had read.
        from . import _memory_proposals
        listed = _memory_proposals.card_facts(args if isinstance(args, dict) else {})
        ask = ask if listed else "Keep proposed facts that are not waiting (nothing would be stored)"
        args = {"_proposed": listed}
        _FIELD_CLIPS.setdefault("_proposed", _memory_proposals.CARD_CLIP)
    if tool in ("memory", "fact_store"):
        # An addition is only ever asked about after outside text (T-1490), and the card is
        # where a planted sentence is caught: every fact whole, as it will be stored, and where
        # it will be read. The table's words above are a rewrite's and would mislead here.
        from ._matrix import memory_additions
        added = memory_additions(tool, args if isinstance(args, dict) else {})
        if added is not None:
            ask, impact, undoable = (
                "Remember: {_remembered}",
                "Jarvis reads it back as your own in every later conversation.", "yes")
            args = {"_remembered": "; ".join(f"“{fact}”" for fact in added)}
            _FIELD_CLIPS.setdefault("_remembered", 2400)
    if composed:
        # A fetch asked about because its address was not given to the turn (T-1492): the card
        # is the address, whole, because what was written into it is what would leave.
        ask, impact, undoable = (
            "Open {_address}",
            "The address leaves the house as written, with anything that was put into it.", "no")
        whole = " and ".join(_visible(address) for address in composed)
        if len(" ".join(whole.split())) > TYPED_SHOWN:
            # As for typed text below: an address that does not fit is said not to.
            ask = "Open an address of {_count} characters, too long to show whole; it begins {_address}"
            impact += " Not all of it is on this card."
        args = {"_address": whole, "_count": sum(map(len, composed))}
        _FIELD_CLIPS.setdefault("_address", TYPED_SHOWN)
    if typed:
        # Text asked about because the owner did not say it (T-1494): the card is the text,
        # whole, and the page it goes to: the last one the browser reported, which a click
        # may have left since.
        host = _host(page)
        whole = "; ".join(f"“{_visible(text)}”" for text in typed)
        ask, impact, undoable = (
            "Type {_typed} into {_page}",
            "Whoever runs the page can read it as it is typed, before anything is submitted.", "no")
        if len(" ".join(whole.split())) > TYPED_SHOWN:
            # A yes to a card is a yes to what is on it: text that does not fit is said not
            # to, with its length, never cut and passed off as the whole.
            ask = "Type {_count} characters into {_page}, too long to show whole; it begins {_typed}"
            impact += " Not all of it is on this card."
        args = {"_typed": whole, "_count": sum(map(len, typed)),
                "_page": f"the page last opened at {host}" if host else "the open page"}
        _FIELD_CLIPS.setdefault("_typed", TYPED_SHOWN)
        _FIELD_CLIPS.setdefault("_page", 300)
    if shell and shell[0]:
        # A shell call asked about because the turn has read text from beyond the house
        # (T-1493): the card is the command, whole, with the folder when the call names one.
        # The table's own line for the shell stops at 60 characters.
        command, folder = shell
        whole = _visible(command) + (f" in the folder {_visible(folder)}" if folder else "")
        ask, impact, undoable = (
            "Give a running process {_command}" if tool in ("process", "process_manage") else "Run {_command}",
            "A shell command runs on this host and can reach the network: anything this turn "
            "has read can leave with it. A delete has no undo.", "no")
        if len(whole) > TYPED_SHOWN:
            # As for typed text above: a command that does not fit is said not to.
            ask = "Run a command of {_count} characters, too long to show whole; it begins {_command}"
            impact += " Not all of it is on this card."
        args = {"_command": whole, "_count": len(command) + len(folder)}
        _FIELD_CLIPS.setdefault("_command", TYPED_SHOWN)
    if masked and (typed or composed or (shell and shell[0])):
        impact += " Part of it looks like a credential and is masked on this card."
    if tool == "session_delete":
        # The card is where the person sees which chat goes: its title, then the id the model gave.
        from . import _chats
        stored = str(args.get("session_id") if isinstance(args, dict) else "")
        title = _chats.title_of(stored)
        args = {"_chat": (f"“{' '.join(title.split()) or 'untitled'}” ({stored})" if title is not None
                          else f"with the id {stored} (not found)")}
        _FIELD_CLIPS.setdefault("_chat", 200)
    if tool == "connect_write":
        # The card is the review of the task as it will be added (T-1370): the whole title, then
        # the due text and description when there are any, never the 60-character clip.
        from ._matrix import clean_line
        given = args if isinstance(args, dict) else {}
        parts = [f"“{clean_line(given.get('content'))}”"]
        for label, key in (("due", "due"), ("note", "description")):
            text = clean_line(given.get(key))
            if text:
                parts.append(f"{label} {text}")
        args = {"_task": ", ".join(parts)}
        _FIELD_CLIPS.setdefault("_task", 1700)
    what = _fill(ask, args)
    if tool == "ha_call_service" and not (args.get("domain") or args.get("service")):
        what = "Change something in the house"
    if tool == "ha_call_service" and str(args.get("domain")) in ("lock", "cover", "alarm_control_panel", "siren"):
        undoable = "no"
    one = lambda s: " ".join(str(s).split()) or "none on record"  # noqa: E731
    return (f"\nWhat: {one(what)}?\nWhy: {one(why)}\nImpact: {one(impact)}\n"
            f"Undoable: {undoable}\nHome: {'yes' if home else 'no'}")


_CARD_RE = re.compile(
    r"\nWhat: (?P<what>[^\n]+)\nWhy: (?P<why>[^\n]+)\nImpact: (?P<impact>[^\n]+)\n"
    r"Undoable: (?P<undoable>yes|no)\nHome: (?P<home>yes|no)\s*$")


def parse_card(reason: str) -> dict[str, str] | None:
    """The card in an approval reason, or None (the apps then show the plain reason)."""
    m = _CARD_RE.search(reason or "")
    return m.groupdict() if m else None


#: How much of one value a settings approval shows: one readable line on the phone's sheet.
_CHANGE_CLIP = 72


def words(value: object) -> str:
    """A setting's value as a person reads it, the way the console's proposal card words it."""
    if value is None or value == "":
        return "not set"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) or "none"
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return str(value)


def change(key: str, before: object, after: object) -> str:
    """The three lines a settings approval carries directly before its card (T-1317; the
    ``change`` section of ``tests/contracts/approval-event.json``).

    Each field is one clipped line, so a value can neither forge a card line below it nor push
    the card off the phone's sheet. Not done here: masking. The caller passes a secret's values
    already masked and puts the others through the audit's redactor; this only words and clips.
    """
    def one(value: object) -> str:
        text = " ".join(words(value).split()) or "not set"
        return text if len(text) <= _CHANGE_CLIP else text[:_CHANGE_CLIP - 1] + "…"
    return f"\nSetting: {one(key)}\nNow: {one(before)}\nBecomes: {one(after)}"


_CHANGE_RE = re.compile(
    r"\nSetting: (?P<key>[^\n]+)\nNow: (?P<before>[^\n]+)\nBecomes: (?P<after>[^\n]+)(?="
    + _CARD_RE.pattern + ")")


def parse_change(reason: str) -> dict[str, str] | None:
    """The change lines of an approval reason, or None. Only directly before a card: the same
    three lines anywhere else are someone's prose."""
    m = _CHANGE_RE.search(reason or "")
    return {k: m.group(k) for k in ("key", "before", "after")} if m else None


__all__ = [
    "AT_HOME", "parse_card", "parse_change", "GOAL_GLYPHS", "LEVEL_STATE", "TEMPLATE_LEVEL",
    "alert", "approval", "blind", "card", "change", "glyph", "goal", "line", "recovered", "words",
]
