"""Enrolling a voice, and asking what the gate thinks.

Four routes, all on the authenticated router:

    GET    /api/voice/speaker            who is enrolled, and how well each fits
    POST   /api/voice/speaker/people     name a person and set their level
    POST   /api/voice/speaker/enrol      add one sample (WAV or raw PCM)
    POST   /api/voice/speaker/verify     score a sample without enrolling it
    DELETE /api/voice/speaker            forget one person, or everyone

Every route takes an optional ``label`` — the person's name — in the query
string. Without one, `enrol` adds to :data:`DEFAULT_LABEL` ("owner"), which
is what a phone or a console written before people had names is enrolling;
`verify` compares with everyone and says who it was; `DELETE` forgets
everyone, which is what the console's FORGET has always meant.

## What never crosses this boundary

The **vectors**. A voiceprint is biometric data about one person, and the
answer to "is somebody enrolled?" must not also be the answer to "what do they
sound like?" — so :meth:`VoiceProfile.summary` is what every response is built
from, and it carries counts, scores and timestamps only. `DELETE` is a real
delete: the store is overwritten, not tombstoned.

The **audio** is not stored at all. A sample is embedded in a worker thread and
the bytes are dropped when the request ends. Nothing here writes a recording to
disk, and there is no debug flag that makes it.

## Why this is a REST write and not a tool

Enrolment is a durable write about a person: it changes whose voice Jarvis
answers, for good, until somebody deletes it. It is deliberately NOT in the
model's toolbox and has no websocket command, so no turn — and in particular
no turn that has read untrusted content — can reach it. The credentials that
can are the bearer token (the phone) and the console password (the browser),
both things a person holds. `docs/explanation/security.md` says why that is the right
tier, and `tests/test_speaker_gate.py` pins that no tool and no command can
enrol.

## Why enrol takes one sample at a time

Because the useful feedback is per sample. Enrolment has to cover the range of
how you actually sound (see `voice/speaker.py` — this is the difference between
a gate that works and one that locks you out), and the surface asking for the
phrases needs to be able to say "that one was too quiet, say it again" between
them. A batch endpoint can only fail the whole set.

Each response carries the running :meth:`VoiceProfile.summary` for the person
just enrolled, so a client watches `self_scores` and `suggested_threshold`
settle as it goes.
"""

from __future__ import annotations

import time

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ..paired_access import REFUSED_MESSAGE
from ..voice.audio import DEFAULT_RATE, DEFAULT_WIDTH
from ..voice.speaker import (
    DEFAULT_LABEL,
    ENROLMENT_PROMPTS,
    LEVEL_OWNER,
    LEVELS,
    MAX_ENROLMENT_SAMPLES,
    MAX_LABEL_CHARS,
    MAX_PEOPLE,
    MIN_ENROLMENT_SAMPLES,
    MODES,
    LabelError,
    SpeakerError,
    embed,
    embed_wav,
    normalise_label,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Jarvis

_LOGGER = logging.getLogger(__name__)

#: Biggest sample accepted, in bytes. 30 s of 16 kHz mono 16-bit plus slack for
#: a WAV header — well past useful, and a bound so an authenticated client
#: cannot hand the server an arbitrary amount of audio to hold in memory.
MAX_SAMPLE_BYTES = 16000 * 2 * 30 + 4096


class EnrolError(Exception):
    """The sample could not be used. Carries an HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _gate(jarvis: "Jarvis") -> Any:
    from ..integrations.voice import get_voice_data

    data = get_voice_data(jarvis)
    if data is None:
        raise EnrolError("the voice integration is not set up", 503)
    return data.speaker


def _label(raw: Any) -> str:
    """A person's name from the query string, or a 400 that says what is wrong."""
    try:
        return normalise_label(raw)
    except LabelError as err:
        raise EnrolError(str(err)) from err


def _refuse_paired(gate: Any, wanted: str | None, *, level: str | None = None) -> None:
    """A paired device's token may not make, change or erase the house's owner (T-1378).

    The owner's voiceprint is what the speaker gate takes for the owner's authority, so a
    lost phone that could enrol a voice into that label, promote someone to owner or forget
    everyone would hold it. `wanted` None is "everybody" (the label-less forget). A label
    that would be made the owner while the house has none is allowed: that is the first
    enrolment on a new house. This does not cover `POST /voice/speaker/enrolling`, which
    only pauses the pipeline for a few seconds (and is bounded for a paired token by
    `PAIRED_RUN_MAX`, T-1379), nor what an owner already enrolled says to the phone in its
    own voice.
    """
    if level == LEVEL_OWNER or wanted is None:
        raise EnrolError(REFUSED_MESSAGE, 403)
    person = gate.profile_for(wanted)
    if person is not None:
        names_owner = person.level == LEVEL_OWNER
    else:
        names_owner = wanted.casefold() == DEFAULT_LABEL.casefold() and any(
            p.level == LEVEL_OWNER for p in gate.profiles
        )
    if names_owner:
        raise EnrolError(REFUSED_MESSAGE, 403)


def _empty_person(label: str) -> dict[str, Any]:
    """The summary of somebody not yet enrolled: the same keys, all at zero."""
    return {
        "enrolled": False,
        "samples": 0,
        "anchor_samples": 0,
        "adapted_samples": 0,
        "label": label,
        "level": None,
        "self_scores": [],
        "self_score": None,
        "worst_self_score": None,
        "threshold_measured": False,
    }


def _neural_key() -> str:
    from ..voice import neural

    return neural.DATA_KEY


def _neural_min() -> int:
    from ..voice import neural

    return neural.MIN_PRINTS


def _neural_learned(jarvis: "Jarvis") -> dict[str, int]:
    from ..voice import neural

    return {k: sum(map(len, by.values())) for k, by in (jarvis.data.get(neural.LEARNED_KEY) or {}).items() if by}


def _neural_aside(jarvis: "Jarvis") -> dict[str, int]:
    from ..voice import neural

    return neural.set_aside(jarvis.data.get(neural.DATA_KEY) or {}, jarvis.data.get(neural.SOURCES_KEY) or {})


def status(jarvis: "Jarvis", label: str | None = None) -> dict[str, Any]:
    """What the console and the phone draw the enrolment screen from.

    The top level describes ONE person — `label` when given, otherwise the
    first enrolled — under the keys the screens have always read, so a client
    that knows nothing about labels keeps working; `people` lists everyone.
    `enrolled` at the top level is whether ANYBODY is, because that is what
    "does the gate do anything" means.
    """
    gate = _gate(jarvis)
    payload: dict[str, Any] = {
        "mode": gate.mode,
        "modes": list(MODES),
        "on_reject": gate.on_reject,
        "allow_unverifiable": gate.allow_unverifiable,
        "active": gate.active,
        # The phrases live in one place so the console and the phone cannot
        # drift apart on them, and they are load-bearing rather than decorative
        # — see voice/speaker.py.
        "prompts": list(ENROLMENT_PROMPTS),
        "min_samples": MIN_ENROLMENT_SAMPLES,
        "max_samples": MAX_ENROLMENT_SAMPLES,
        "max_people": MAX_PEOPLE,
        "max_label_chars": MAX_LABEL_CHARS,
        "default_label": DEFAULT_LABEL,
        "levels": list(LEVELS),
        "people": gate.people(),
        # Each person's neural voiceprints (voice/neural.py), and how many judge them:
        # a person under the minimum is still judged classically and should re-enrol.
        "neural": {
            "prints": {k: len(v) for k, v in (jarvis.data.get(_neural_key()) or {}).items()},
            "min_prints": _neural_min(),
            # Prints kept but not judged: nothing like the person's anchor mic (T-0389).
            "set_aside": _neural_aside(jarvis),
            # Prints learned from turns it was sure of, beside the enrolled ones (T-0771).
            "learned": _neural_learned(jarvis),
        },
        # Whether `voice: speaker: threshold:` is in force over every
        # profile's own measurement. A screen that shows "enrolment suggests
        # 5.1" beside a gate running at 8.8 must be able to say which is live.
        "configured_threshold": gate.configured_threshold,
    }
    if label is not None:
        wanted = _label(label)
        profile = gate.profile_for(wanted)
    else:
        wanted = DEFAULT_LABEL
        profile = gate.profile
        if profile is not None:
            wanted = profile.label
    payload.update(profile.summary() if profile is not None else _empty_person(wanted))
    payload["enrolled"] = gate.enrolled
    payload["person_enrolled"] = profile is not None and profile.enrolled
    # Whether the house is still inside an enrolment's window (M79) — the
    # twenty seconds after a sample in which every spoken turn yields. A
    # client that has just enrolled someone can wait for this to clear before
    # inviting them to speak; the live rig did not, and its first turn after
    # enrolling got no answer at all (27 Aug 2026).
    payload["enrolling"] = enrolling(jarvis)
    return payload


async def _embed(body: bytes, content_type: str, rate: int, width: int) -> Any:
    """Turn a request body into an embedding, off the event loop."""
    if not body:
        raise EnrolError("no audio in the request body")
    if len(body) > MAX_SAMPLE_BYTES:
        raise EnrolError(f"sample is larger than {MAX_SAMPLE_BYTES} bytes", 413)

    looks_like_wav = body[:4] == b"RIFF" or "wav" in content_type.lower()
    try:
        if looks_like_wav:
            embedding = await asyncio.to_thread(embed_wav, body)
        else:
            embedding = await asyncio.to_thread(embed, body, rate, width)
    except SpeakerError as err:
        raise EnrolError(str(err)) from err
    except Exception as err:  # a malformed WAV is a bad request, not a 500
        raise EnrolError(f"could not read the audio: {err}") from err

    if embedding is None:
        raise EnrolError(
            "not enough speech in that sample to enrol from — say the whole "
            "phrase, and closer to the microphone"
        )
    return embedding


#: How long after a sample, a test, or a client saying "recording now" the
#: house counts an enrolment as in progress. Twenty seconds: a phrase is read,
#: uploaded and judged well inside it, and the client refreshes the mark on
#: the next one; a person who walks away is listened to again half a minute
#: later without touching anything.
ENROLLING_WINDOW = 20.0
DATA_ENROLLING_UNTIL = "voice_enrolling_until"


#: A paired device's marks are honoured for `PAIRED_RUN_MAX` seconds from the first, then ignored
#: for `PAIRED_COOLDOWN` more, then a new run starts. Five phrases take about two minutes, so a
#: person enrolling is never cut short; a stolen token calling every few seconds keeps the house
#: deaf about 13% of the time (a 240 s run in each 2040 s), not all of it. A second person
#: enrolling right after the first on the same phone may find the pause off: their phrases are
#: then judged as commands, as before M79.
PAIRED_RUN_MAX = 240.0
PAIRED_COOLDOWN = 1800.0
DATA_ENROLLING_RUN = "voice_enrolling_run_since"


def mark_enrolling(jarvis: "Jarvis", window: float = ENROLLING_WINDOW, paired: bool = False) -> float:
    """An enrolment is in progress (M79): the phrases read aloud for a
    voiceprint are not commands. Every pipeline turn that starts inside the
    window yields — see `PipelineRun._run_intent`. A ``paired`` mark is bounded
    (`PAIRED_RUN_MAX`): the owner's token and the console are not."""
    now = time.monotonic()
    current = float(jarvis.data.get(DATA_ENROLLING_UNTIL) or 0.0)
    if paired:
        since = jarvis.data.get(DATA_ENROLLING_RUN)
        if since is None or now - since >= PAIRED_RUN_MAX + PAIRED_COOLDOWN:
            since = jarvis.data[DATA_ENROLLING_RUN] = now
        if now - since > PAIRED_RUN_MAX:
            return current
    else:
        jarvis.data.pop(DATA_ENROLLING_RUN, None)
    until = jarvis.data[DATA_ENROLLING_UNTIL] = now + window
    return until


def enrolling(jarvis: "Jarvis") -> bool:
    return time.monotonic() < float(jarvis.data.get(DATA_ENROLLING_UNTIL) or 0.0)


#: The microphones a print may be filed under; anything else is filed as the console's.
SOURCES = ("console", "android", "car", "desktop", "house")


def _source(value: Any) -> str:
    text = str(value or "").strip().lower()
    # A client that says console is the console in a browser or the desktop app (or "That was
    # me" there): a computer's microphone. "console" itself is what prints were filed under
    # before clients said, which were the phone page's, and a computer's prints averaged into
    # them matched neither (T-0770).
    if text == "console":
        return "desktop"
    return text if text in SOURCES else "console"


async def async_enrol(
    jarvis: "Jarvis",
    body: bytes,
    content_type: str = "",
    rate: int = DEFAULT_RATE,
    width: int = DEFAULT_WIDTH,
    label: str | None = None,
    source: str | None = None,
    paired: bool = False,
) -> dict[str, Any]:
    """Add one sample to a person's profile and report where enrolment stands.

    ``source`` names the microphone (console, android, car, desktop): prints are judged
    per source (voice/neural.py), so the car's own prints are what the car is matched by.
    ``paired`` is a paired device's token: it may not add to the owner's voiceprint
    (`_refuse_paired`), the label it names or defaults to included.
    """
    from ..integrations.voice import async_save_profiles

    gate = _gate(jarvis)
    wanted = _label(label)
    if paired:
        _refuse_paired(gate, wanted)
    mark_enrolling(jarvis, paired=paired)
    embedding = await _embed(body, content_type, rate, width)

    # A pitchless enrolment sample would teach the profile a pitch histogram
    # that is a placeholder, and every later turn would be measured against it.
    # Refusing here costs one retry; accepting it costs the whole profile.
    if not embedding.has_pitch:
        raise EnrolError(
            "that sample has no measurable pitch — it is too quiet, too "
            "breathy, or too far from the microphone to enrol from"
        )

    # The neural print of the same sample, when the voice service can give one:
    # enough of them and this person is judged by CAM++ (voice/neural.py).
    from ..voice import neural

    vector = await neural.embed(neural.as_wav(body, rate, width))
    # Checked before either print is kept: "That was me" on a passenger's held clip
    # enrolled their voice as the owner's in the car (T-0389).
    far = None if vector is None else neural.unlike(await neural.prints(jarvis), await neural.sources(jarvis), wanted, vector)
    if far is not None:
        raise EnrolError(
            f"that sample does not sound like {wanted}'s voiceprint ({far:.2f} against "
            f"{neural.FLOOR:.2f}); if the voiceprint is wrong, forget {wanted} and enrol again"
        )
    try:
        profile = gate.profile_for(wanted, create=True)
    except SpeakerError as err:
        # The store is full. 409 rather than 400: the request was fine, the
        # state is what refuses it, and the remedy is a DELETE.
        raise EnrolError(str(err), 409) from err
    profile.add(embedding)
    if vector is not None:
        await neural.add(jarvis, profile.label, vector, _source(source))

    # The threshold follows the samples. Enrolment is the only place that has
    # the leave-one-out spread to work it out from, so it is recomputed on
    # every sample rather than left at whatever the first three implied. The
    # measurement is what is STORED; a configured threshold is re-applied over
    # it afterwards, so the config wins in memory and the profile keeps its
    # own number for the day the config line is removed.
    if profile.enrolled:
        profile.threshold = profile.suggested_threshold()

    await async_save_profiles(jarvis, gate.profiles)
    gate.apply_threshold()
    payload = status(jarvis, wanted)
    payload["accepted"] = True
    payload["sample"] = embedding.as_dict() | {"vector": None}
    return payload


async def async_verify(
    jarvis: "Jarvis",
    body: bytes,
    content_type: str = "",
    rate: int = DEFAULT_RATE,
    width: int = DEFAULT_WIDTH,
    label: str | None = None,
    source: str | None = None,
    learn: bool = False,
    paired: bool = False,
) -> dict[str, Any]:
    """Score a sample against the profiles without changing anything, unless ``learn``.

    This is how you find your threshold without being locked out while you look
    for it: record yourself, and a friend, and read the two numbers. It is also
    what TEST MY VOICE calls, on the phone and on the console. With no `label`
    the sample is compared with everyone and the verdict names who it was; with
    one it is compared with that person only. ``learn`` is the adapter's, for a real turn: a
    match well over its bar is kept as a learned print of ``source`` (neural.learn, T-0771).
    """
    mark_enrolling(jarvis, paired=paired)
    gate = _gate(jarvis)
    if not gate.enrolled:
        raise EnrolError("nobody is enrolled yet", 409)
    wanted = _label(label) if label is not None else None
    if wanted is not None:
        person = gate.profile_for(wanted)
        if person is None or not person.enrolled:
            raise EnrolError(f"{wanted!r} is not enrolled", 404)
    embedding = await _embed(body, content_type, rate, width)
    verdict = gate.verify_embedding(embedding, wanted)
    # Who it was by CAM++, for the people with enough prints (voice/neural.py): the
    # classical verdict let other voices through (the owner, 2026-09-25).
    from ..voice import neural

    held = await neural.prints(jarvis)
    if neural.judged(held, wanted):
        vector = await neural.embed(neural.as_wav(body, rate, width))
        if vector is not None:
            levels = {p.label: p.level for p in gate.profiles}
            verdict = neural.verdict(verdict, held, vector, levels, wanted, await neural.sources(jarvis),
                                     await neural.learned(jarvis))
            if learn and verdict.reason == "neural-match":
                await neural.learn(jarvis, vector, _source(source), wanted)
        else:
            _LOGGER.warning("the neural voiceprint is unavailable; the classical verdict stands")
    return {
        "verdict": verdict.as_dict(),
        "would_block": gate.blocks(verdict),
        "speech_ms": round(embedding.speech_ms, 1),
        "has_pitch": embedding.has_pitch,
    }


async def async_person(
    jarvis: "Jarvis", label: Any, level: Any, rename: Any = None, paired: bool = False
) -> dict[str, Any]:
    """Name a person, or set the level of one already named.

    This is the People screen's write: a person exists from the moment they
    are named, before any phrase is read, so a household can be set up and
    each voice enrolled at leisure — and so a level can be lowered without
    re-enrolling. It creates nothing the gate acts on: a person with no
    samples is never matched, and the level only means something once the
    verdict carries it. The same tier as enrolment (a durable write about a
    person, by the bearer token or the console password, never by a tool),
    for the reason the module docstring gives.
    """
    from ..integrations.voice import async_save_profiles

    gate = _gate(jarvis)
    if label is None:
        raise EnrolError("label is required: the person's name")
    wanted = _label(label)
    if paired:
        _refuse_paired(gate, wanted, level=str(level or "").strip().casefold())
    if rename is not None:
        # A new name for someone already here (the owner, 2026-09-25: "I cant edit the
        # names of enrolled voices"): the voiceprint, samples and level stay theirs.
        return await _rename(jarvis, gate, wanted, _label(rename))
    chosen = str(level or "").strip().casefold()
    if chosen not in LEVELS:
        raise EnrolError(f"level must be one of {', '.join(LEVELS)}")
    try:
        profile = gate.profile_for(wanted, create=True)
    except SpeakerError as err:
        raise EnrolError(str(err), 409) from err
    profile.level = chosen
    profile.updated = time.time()
    await async_save_profiles(jarvis, gate.profiles)
    _LOGGER.info("Person %r is a %s (%d people)", profile.label, chosen, len(gate.profiles))
    return status(jarvis, profile.label)


async def _rename(jarvis: "Jarvis", gate: Any, wanted: str, new: str) -> dict[str, Any]:
    from ..integrations.voice import async_save_profiles

    profile = gate.profile_for(wanted, create=False)
    if profile is None:
        raise EnrolError(f"{wanted!r} is not here to rename", 404)
    other = gate.profile_for(new, create=False)
    if other is not None and other is not profile:
        raise EnrolError(f"{new!r} is already someone here", 409)
    old = profile.label
    profile.label = new
    profile.updated = time.time()
    await async_save_profiles(jarvis, gate.profiles)
    from ..voice import neural

    await neural.rename(jarvis, old, new)
    _LOGGER.info("Person %r is now %r", wanted, new)
    return status(jarvis, new)


async def async_forget(
    jarvis: "Jarvis", label: str | None = None, paired: bool = False
) -> dict[str, Any]:
    """Delete one person's voiceprint, or everyone's. A real delete, not a flag.

    A paired device's token may forget a person who is not the owner, never everyone.
    """
    from ..integrations.voice import async_save_profiles

    gate = _gate(jarvis)
    if paired:
        _refuse_paired(gate, None if label is None else _label(label))
    from ..voice import neural

    if label is None:
        gate.profiles = []
        await async_save_profiles(jarvis, [])
        await neural.forget(jarvis, None)
        _LOGGER.info("Every voiceprint deleted; the speaker gate is inert until re-enrolled")
        return status(jarvis)
    wanted = _label(label)
    if not gate.remove(wanted):
        raise EnrolError(f"{wanted!r} is not enrolled", 404)
    await async_save_profiles(jarvis, gate.profiles)
    for known in list((await neural.prints(jarvis)).keys()):
        if known.casefold() == wanted.casefold():
            await neural.forget(jarvis, known)
    _LOGGER.info("Voiceprint for %r deleted; %d people remain", wanted, len(gate.profiles))
    return status(jarvis)
