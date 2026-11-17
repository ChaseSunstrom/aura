"""A neural voiceprint beside the classical one: who is speaking, by CAM++.

The owner, 2026-09-25: "enrollment isnt great it still picks up on other
people". The classical verifier (`speaker.py`, MFCC statistics and pitch) says
of itself that it "is not a neural speaker embedder" and that one "is markedly
better". The house already runs one: CosyVoice ships CAM++ (3D-Speaker) for its
own voice cloning, and `jarvis-cosyvoice` answers `POST /v1/speaker/embed` with
its 192-float voiceprint of a WAV.

Each enrolment sample stores one such print per person (`speaker_neural` in the
config store). A person with at least :data:`MIN_PRINTS` of them is judged by
cosine to the mean of their prints, and that judgement replaces the classical
verdict; a person with fewer (enrolled before this, or with the voice service
down) is judged as before. So re-enrolling is what turns it on.

Does not cover the voice service being away at verify time: the classical
verdict stands, and the log says so.
"""

from __future__ import annotations

import dataclasses
import io
import logging
import math
import os
import time
import wave
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from ..core import Jarvis
    from .speaker import Verdict

_LOGGER = logging.getLogger(__name__)

STORE_KEY = "speaker_neural"
DATA_KEY = "voice_speaker_neural"
#: Where each print was recorded, parallel to the prints: ``{label: [source, ...]}``.
SOURCES_KEY = "voice_speaker_neural_sources"
#: A print stored before sources were kept, or from a client that did not say.
DEFAULT_SOURCE = "console"
#: jarvis-cosyvoice on the host's loopback; `AURA_SPEAKER_NEURAL_URL` moves it.
DEFAULT_URL = "http://127.0.0.1:8890"
#: Prints a person needs before they are judged by them rather than classically.
MIN_PRINTS = 3
#: Prints kept per person: the newest, so a re-enrolment replaces the old voice.
MAX_PRINTS = 20
#: Cosine to the person's mean print at or above which it is them. CAM++ puts one
#: speaker's clips around 0.6-0.8 and different speakers under 0.4.
ACCEPT = float(os.environ.get("AURA_SPEAKER_NEURAL_ACCEPT", "0.5"))
#: Cosine to the person's anchor microphone (their most self-consistent one) under which
#: a print or a clip is not them at all. The owner's car prints sat 0.35-0.49 from his
#: console mean; a passenger's 0.06-0.11, silence 0.01 (2026-10-29).
FLOOR = float(os.environ.get("AURA_SPEAKER_NEURAL_FLOOR", "0.25"))
#: The lowest bar a noisy microphone's own spread may set for itself.
LOWEST_BAR = 0.35
TIMEOUT_S = 8.0
#: Prints learned per person per microphone from turns the house was sure of (T-0771), kept
#: beside the enrolled ones: learning never pushes out what was read at enrolment.
LEARNED_KEY = "voice_speaker_neural_learned"
LEARNED_AT_KEY = "voice_speaker_neural_learned_at"
MAX_LEARNED = 5
#: How far over its bar a turn must score before its print is learned: a voice just inside
#: the line is the one an adaptive gate must never move toward.
LEARN_MARGIN = 0.15
#: One learned print per person per this long, so one long session cannot fill a microphone.
LEARN_EVERY_S = 600.0


def url() -> str:
    return os.environ.get("AURA_SPEAKER_NEURAL_URL", DEFAULT_URL).rstrip("/")


def as_wav(body: bytes, rate: int, width: int) -> bytes:
    """The body as a WAV: a phone sends raw PCM, the voice service reads WAV."""
    if body[:4] == b"RIFF":
        return body
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(body)
    return out.getvalue()


async def embed(wav: bytes) -> list[float] | None:
    """The clip's voiceprint, or None when the voice service cannot give one."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.post(f"{url()}/v1/speaker/embed", content=wav, headers={"content-type": "audio/wav"})
        if r.status_code != 200:
            _LOGGER.info("no neural voiceprint: the voice service answered %s", r.status_code)
            return None
        vector = r.json().get("embedding")
        return [float(x) for x in vector] if isinstance(vector, list) and vector else None
    except (httpx.HTTPError, ValueError) as err:
        _LOGGER.info("no neural voiceprint: %s", err)
        return None


async def prints(jarvis: "Jarvis") -> dict[str, list[list[float]]]:
    """Every person's prints, loaded once from the store."""
    held = jarvis.data.get(DATA_KEY)
    if held is None:
        from ..store import Store

        saved = await Store(jarvis.config_dir, STORE_KEY).load() or {}
        held = {str(k): [list(map(float, v)) for v in vs] for k, vs in (saved.get("people") or {}).items()}
        jarvis.data[DATA_KEY] = held
        stored = saved.get("sources") or {}
        jarvis.data[SOURCES_KEY] = {
            k: [str(x) for x in stored.get(k, [])][-len(vs):] if len(stored.get(k, [])) == len(vs)
            else [DEFAULT_SOURCE] * len(vs)
            for k, vs in held.items()
        }
        jarvis.data[LEARNED_KEY] = {
            str(k): {str(src): [list(map(float, v)) for v in vs] for src, vs in by.items()}
            for k, by in (saved.get("learned") or {}).items() if k in held
        }
    return held


async def sources(jarvis: "Jarvis") -> dict[str, list[str]]:
    """Each person's print sources, parallel to :func:`prints`."""
    await prints(jarvis)
    return jarvis.data.setdefault(SOURCES_KEY, {})


async def learned(jarvis: "Jarvis") -> dict[str, dict[str, list[list[float]]]]:
    """Each person's learned prints by microphone: ``{label: {source: [vector, ...]}}``."""
    await prints(jarvis)
    return jarvis.data.setdefault(LEARNED_KEY, {})


async def learn(jarvis: "Jarvis", vector: list[float], source: str, only: str | None = None) -> bool:
    """Keep the print of a turn the house was sure of, for its microphone (T-0771).

    Decided by the enrolled prints alone: a match LEARN_MARGIN over its bar, on a microphone
    the person enrolled on, one per person per LEARN_EVERY_S, the newest MAX_LEARNED per
    microphone. So a learned print never teaches the next one, and learning never gives a
    microphone a group of its own (T-0777). Does not cover a person who is matched well over
    the bar and is not them: that voice is already let in, and forgetting the person clears
    what it taught.
    """
    if os.environ.get("AURA_SPEAKER_LEARN", "1") == "0":
        return False
    held, srcs = await prints(jarvis), await sources(jarvis)
    who, score, bar = closest(held, vector, only, srcs)
    source = source or DEFAULT_SOURCE
    if who is None or score < bar + LEARN_MARGIN or (srcs.get(who) or []).count(source) < MIN_PRINTS:
        return False
    at = jarvis.data.setdefault(LEARNED_AT_KEY, {})
    now = time.monotonic()
    if now - at.get(who, -math.inf) < LEARN_EVERY_S:
        return False
    mine = (await learned(jarvis)).setdefault(who, {}).setdefault(source, [])
    mine.append(vector)
    del mine[:-MAX_LEARNED]
    at[who] = now
    await _save(jarvis, held)
    _LOGGER.info("learned a %s print for %s (%.2f against %.2f)", source, who, score, bar)
    return True


async def _save(jarvis: "Jarvis", held: dict[str, list[list[float]]]) -> None:
    from ..store import Store

    srcs = jarvis.data.get(SOURCES_KEY) or {}
    extra = jarvis.data.get(LEARNED_KEY) or {}
    await Store(jarvis.config_dir, STORE_KEY).save(
        {"people": held, "sources": {k: srcs.get(k, []) for k in held},
         "learned": {k: extra[k] for k in held if extra.get(k)}} if held else {})


async def add(jarvis: "Jarvis", label: str, vector: list[float], source: str = DEFAULT_SOURCE) -> int:
    """Keep the print, and which microphone it came from (MAX_PRINTS newest per source)."""
    held = await prints(jarvis)
    srcs = await sources(jarvis)
    mine, where = held.setdefault(label, []), srcs.setdefault(label, [])
    mine.append(vector)
    where.append(source or DEFAULT_SOURCE)
    # The newest MAX_PRINTS of each source: re-enrolling on the console must not push
    # out the car's prints, which would put the car back below the line.
    keep: list[int] = []
    seen: dict[str, int] = {}
    for i in range(len(mine) - 1, -1, -1):
        seen[where[i]] = seen.get(where[i], 0) + 1
        if seen[where[i]] <= MAX_PRINTS:
            keep.append(i)
    keep.reverse()
    held[label] = [mine[i] for i in keep]
    srcs[label] = [where[i] for i in keep]
    await _save(jarvis, held)
    return len(held[label])


async def rename(jarvis: "Jarvis", old: str, new: str) -> None:
    held = await prints(jarvis)
    if old in held:
        held[new] = held.pop(old)
        srcs = await sources(jarvis)
        if old in srcs:
            srcs[new] = srcs.pop(old)
        extra = await learned(jarvis)
        if old in extra:
            extra[new] = extra.pop(old)
        await _save(jarvis, held)


async def forget(jarvis: "Jarvis", label: str | None) -> None:
    held = await prints(jarvis)
    srcs = await sources(jarvis)
    extra = await learned(jarvis)
    if label is None:
        held.clear()
        srcs.clear()
        extra.clear()
    else:
        held.pop(label, None)
        srcs.pop(label, None)
        extra.pop(label, None)
    await _save(jarvis, held)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _mean(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    return [sum(col) / n for col in zip(*vectors)]


def _loo(group: list[list[float]]) -> list[float]:
    """Each print's cosine to the mean of the others (cosine ignores the 1/(n-1))."""
    total = [sum(col) for col in zip(*group)]
    return [_cosine(v, [t - x for t, x in zip(total, v)]) for v in group]


def _groups(
    vectors: list[list[float]],
    where: list[str],
    taught: dict[str, list[list[float]]] | None = None,
) -> tuple[list[tuple[list[float], float]], int]:
    """``([(mean, bar), ...], set aside)`` for the microphones that judge a person, anchor first.

    One mean per microphone rather than one over all of them: the owner enrolled on the
    console and was refused on the phone and in the car (2026-10-07) -- a mean across mics
    sits between them and near none. The anchor is the most self-consistent microphone and
    keeps the ACCEPT bar. Another one's prints under FLOOR to the anchor are set aside
    (kept, not judged): "That was me" on a passenger's clip put two of theirs among the
    owner's car prints (T-0389). What is left is judged by its own spread, between
    LOWEST_BAR and ACCEPT, since a car mic's prints are further apart than the console's.

    ``taught`` (learned prints by microphone, T-0771) only widens the mean of a microphone
    with enough enrolled prints. It never chooses the anchor or sets a bar: five learned
    prints are more self-consistent than any enrolled group, and an anchor chosen from them
    would have taken in the car's set-aside passenger prints (T-0777).
    """
    if len(where) != len(vectors):
        where = [DEFAULT_SOURCE] * len(vectors)
    by: dict[str, list[list[float]]] = {}
    for v, src in zip(vectors, where):
        by.setdefault(src, []).append(v)
    # A microphone counts once it holds MIN_PRINTS of its own: a lone claimed or odd
    # print would otherwise be its own "mean" and stand for the person by itself.
    # Until then its prints still count in the mean over every microphone.
    full = {src: g for src, g in by.items() if len(g) >= MIN_PRINTS}
    if not full:
        return [(_mean(vectors), ACCEPT)], 0
    home = max(full, key=lambda src: sum(_loo(full[src])) / len(full[src]))
    centre = _mean(full[home])

    def more(src: str) -> list[list[float]]:
        return [v for v in (taught or {}).get(src, []) if _cosine(v, centre) >= FLOOR]

    out, aside = [(centre, ACCEPT)], 0
    if more(home):
        # The anchor's own microphone, widened, judged beside it: the anchor itself stays as
        # enrolled, since the floor and the noise guard are measured from it.
        out.append((_mean(full[home] + more(home)), ACCEPT))
    for src, g in full.items():
        if src == home:
            continue
        kept = [v for v in g if _cosine(v, centre) >= FLOOR]
        aside += len(g) - len(kept)
        if len(kept) >= MIN_PRINTS:
            out.append((_mean(kept + more(src)), max(LOWEST_BAR, min(ACCEPT, min(_loo(kept))))))
    return out, aside


def set_aside(held: dict[str, list[list[float]]], srcs: dict[str, list[str]]) -> dict[str, int]:
    """How many of each judged person's prints are set aside (:func:`_groups`)."""
    out = {}
    for label, vectors in held.items():
        if len(vectors) >= MIN_PRINTS:
            n = _groups(vectors, srcs.get(label) or [])[1]
            if n:
                out[label] = n
    return out


def unlike(held: dict[str, list[list[float]]], srcs: dict[str, list[str]], label: str, vector: list[float]) -> float | None:
    """The print's cosine to `label`'s anchor when it is under FLOOR, else None (or not judged yet)."""
    for name, vectors in held.items():
        if name.casefold() == label.casefold() and len(vectors) >= MIN_PRINTS:
            c = _cosine(_groups(vectors, srcs.get(name) or [])[0][0][0], vector)
            return c if c < FLOOR else None
    return None


def closest(
    held: dict[str, list[list[float]]],
    vector: list[float],
    only: str | None = None,
    srcs: dict[str, list[str]] | None = None,
    taught: dict[str, dict[str, list[list[float]]]] | None = None,
) -> tuple[str | None, float, float]:
    """The judged person nearest the print: ``(label, cosine, bar)`` of their best microphone.

    Not a max over single prints, which would let one odd sample stand for a person.
    Best is by margin over the bar; a microphone other than the anchor counts only for a
    clip at least FLOOR from the anchor (silence sat near the car's mean, :func:`_groups`).
    """
    best, score, bar = None, -1.0, ACCEPT
    for label, vectors in held.items():
        if only is not None and label.casefold() != only.casefold():
            continue
        if len(vectors) < MIN_PRINTS:
            continue
        groups, _ = _groups(vectors, (srcs or {}).get(label) or [], (taught or {}).get(label))
        near = _cosine(groups[0][0], vector)
        c, b = near, groups[0][1]
        for mean, own in groups[1:]:
            other = _cosine(mean, vector)
            if near >= FLOOR and other - own > c - b:
                c, b = other, own
        if best is None or c - b > score - bar:
            best, score, bar = label, c, b
    return best, score, bar


def judged(held: dict[str, list[list[float]]], only: str | None = None) -> bool:
    """Whether anyone (or `only`) has enough prints to be judged by them."""
    return any(len(v) >= MIN_PRINTS and (only is None or k.casefold() == only.casefold()) for k, v in held.items())


def verdict(
    classical: "Verdict",
    held: dict[str, list[list[float]]],
    vector: list[float],
    levels: dict[str, str],
    only: str | None = None,
    srcs: dict[str, list[str]] | None = None,
    taught: dict[str, dict[str, list[list[float]]]] | None = None,
) -> "Verdict":
    """The classical verdict, replaced by the neural one for people who have prints."""
    who, score, bar = closest(held, vector, only, srcs, taught)
    if who is None:
        return classical
    ok = score >= bar
    return dataclasses.replace(
        classical,
        accepted=ok,
        score=round(1.0 - score, 4),
        threshold=round(1.0 - bar, 4),
        similarity=round(score, 4),
        confidence=round(max(0.0, min(1.0, score)), 4),
        reason="neural-match" if ok else "neural-mismatch",
        label=who if ok else None,
        level=levels.get(who) if ok else None,
        nearest=who,
    )
