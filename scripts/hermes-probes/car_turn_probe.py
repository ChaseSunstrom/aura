"""The car's voice turn, end to end against a running stack (ADR-0047).

    python scripts/hermes-probes/car_turn_probe.py --base http://<tailnet>:8643 --key $AURA_API_KEY \
        --core http://127.0.0.1:8080 --core-token $AURA_TOKEN [--text "Turn on the bed light."]

What the phone does when the steering-wheel button is pressed, minus the car itself:
  1. the question, as audio in the car microphone's format -- 16 kHz mono 16-bit, which is
     what the head unit's AV-input channel delivers (MicrophoneFormat.CAR_MICROPHONE) --
     made here from Jarvis's own streamed voice, resampled
  2. POST /api/audio/transcribe with that WAV (JarvisTranscriber) -> transcript, speaker
     label and ticket from the house's speaker gate
  3. prompt.submit with the ticket over the adapter's /ws (HermesCarJarvis); an
     approval.request is answered "once", as the car's Allow button answers it
  4. the finished reply fetched as streamed PCM from /api/audio/speak-pcm (CarTurn), the
     bytes the car's guidance channel plays
  5. the demo light's state read from the house before and after, which is the proof the
     sentence did something rather than only produced a sentence

With --phone, step 5 is a phone instead of a light: a phone-shaped socket registers on the
house with the app's own torch action (DeviceActions.kt ToggleTorch, as ActionRegistry
publishes it), the question is "Turn on my phone's torch.", and the proof is the
device_command the house sends that socket -- the frame JarvisChannel hands to the phone's
ActionRegistry. The socket answers ok as the phone would; the torch itself is the phone's.

With --read-only (the nightly ride-along, scripts/ride-along.sh) there is no step 5 and
nothing is approved: an approval.request is answered "deny" and fails the run, and the
reply must match --expect. Step 2 then takes the words from the card's Whisper directly and
asks the house's gate on its own, which must refuse Jarvis's own voice: through the adapter
a refused voice close to the owner is held for "That was me", and a nightly claim of the TTS
voice in the owner's tray is one tap from filing it as their print. Read-only denies what
asks; a FREE or AUTO tool acts without asking, so the ride-along's sentence must be a question.

Exits non-zero naming the step that failed. Does not exercise the car's Bluetooth, Wi-Fi,
AAP session or speakers: that is docs/car/drive-checklist.md.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import struct
import sys
import time
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _probe import adapter_ws_url, connect, house, http_json  # noqa: E402

CAR_RATE = 16_000


def speak_pcm(base: str, key: str, text: str) -> tuple[bytes, int]:
    req = urllib.request.Request(
        base.rstrip("/") + "/api/audio/speak-pcm",
        data=json.dumps({"text": text}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        rate = int(r.headers.get("x-sample-rate") or 24_000)
        return r.read(), rate


def resample(pcm: bytes, src: int, dst: int) -> bytes:
    """Linear resampling of 16-bit mono PCM; enough for speech into a transcriber."""
    n = len(pcm) // 2
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    out_n = int(n * dst / src)
    out = []
    for i in range(out_n):
        pos = i * src / dst
        j = int(pos)
        f = pos - j
        a = samples[j] if j < n else 0
        b = samples[j + 1] if j + 1 < n else a
        out.append(int(a + (b - a) * f))
    return struct.pack(f"<{out_n}h", *out)


def wav(pcm: bytes, rate: int) -> bytes:
    return b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack(
        "<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16
    ) + b"data" + struct.pack("<I", len(pcm)) + pcm


def card_words(stt: str, body: dict) -> dict:
    """The card's Whisper (aura_voice/stt.py), which the adapter asks first, asked directly."""
    req = urllib.request.Request(f"{stt.rstrip('/')}/v1/audio/transcriptions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return {**json.load(r), "provider": "whisper-gpu"}


def gate_verdict(core: str, token: str, audio: bytes) -> dict:
    """The house's speaker gate's verdict on a sample: scored, never enrolled, nothing kept (api/speaker.py)."""
    req = urllib.request.Request(f"{core.rstrip('/')}/api/voice/speaker/verify", data=audio,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "audio/wav"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("verdict") or {}


def core_state(core: str, token: str, entity: str) -> str:
    req = urllib.request.Request(f"{core.rstrip('/')}/api/states/{entity}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("state", "")


#: What the app registers for its torch (DeviceActions.kt, ToggleTorch), in ActionRegistry's manifest shape.
TORCH = {"id": "toggle_torch", "tier": 1, "tier_name": "AUTO", "description": "Turn the camera flash (torch) on or off.",
         "params": {"on": "bool: true to switch the torch on"}, "capability": "device", "available": True}


def torch_on(command: dict) -> bool:
    """What the phone does with the command: ToggleTorch reads `on` with a default of true."""
    return bool((command.get("params") or {}).get("on", True))


async def phone(core: str, token: str, ready: asyncio.Event, commands: list[dict]) -> None:
    """A phone on the house's device channel: registers, then answers every device_command ok."""
    async with house(core, token) as ws:
        await ws.send(json.dumps({"id": 1, "type": "jarvis/device/register", "device": {
            "id": f"car-probe-phone-{uuid.uuid4().hex[:6]}", "name": "Car probe phone", "platform": "android",
            "capabilities": ["device"], "app_version": "1.0.0", "actions": [TORCH]}}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == 1 and m.get("type") == "result":
                if not m.get("success"):
                    raise SystemExit(f"FAIL step 0: the phone did not register: {m}")
                ready.set()
            elif m.get("type") == "device_command":
                commands.append(m)
                await ws.send(json.dumps({"type": "device_result", "command_id": m["command_id"], "status": "ok",
                                          "result": {"on": torch_on(m)}}))


async def ask_with_phone(a: argparse.Namespace, text: str, ticket: str | None) -> tuple[str, list[str], list[dict]]:
    ready, commands = asyncio.Event(), []
    task = asyncio.create_task(phone(a.core, a.core_token, ready, commands))
    await asyncio.wait_for(ready.wait(), 30)
    try:
        reply, approvals = await ask(a.base, a.key, text, ticket, a.timeout)
    finally:
        task.cancel()
    return reply, approvals, commands


async def ask(base: str, key: str, text: str, ticket: str | None, timeout: float,
              approve: bool = True) -> tuple[str, list[str]]:
    approvals: list[str] = []
    reply = ""
    async with connect(adapter_ws_url(base, key)) as rpc:
        sid = await rpc.create_session()
        params = {"session_id": sid, "text": text, "surface": {"kind": "car"}}
        if ticket:
            params["speaker_ticket"] = ticket
        await rpc.call("prompt.submit", params)
        async for p in rpc.events(timeout):
            t, pl = p.get("type"), p.get("payload", {})
            if t == "approval.request":
                choices = pl.get("choices") or ["once"]
                # A read-only ride-along approves nothing: it answers deny and fails on it.
                yes = next((c for c in choices if c.lower() != "deny"), choices[0]) if approve else "deny"
                approvals.append(f"{pl.get('tool') or pl.get('command', '')[:40]} -> {yes}")
                await rpc.call("approval.respond", {"session_id": sid, "request_id": pl.get("request_id"), "choice": yes})
            elif t == "message.delta" and pl.get("text"):
                reply += pl["text"]
            elif t == "message.complete":
                reply = pl.get("text") or reply
                break
    return reply.strip(), approvals


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    # From the environment when not given: an argument is in every local user's `ps`.
    ap.add_argument("--key", default=os.environ.get("AURA_API_KEY", ""))
    ap.add_argument("--core", required=True)
    ap.add_argument("--core-token", default=os.environ.get("HASS_TOKEN", ""))
    ap.add_argument("--text", default="Turn on the bed light.")
    ap.add_argument("--entity", default="light.bed_light")
    ap.add_argument("--want", default="on")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--phone", action="store_true", help="act on a phone-shaped device instead of a light")
    # The nightly ride-along (T-0196): the whole path, changing nothing and approving nothing.
    ap.add_argument("--read-only", action="store_true",
                    help="a question, not a command: no entity step; an approval request is denied and fails the run")
    ap.add_argument("--expect", default="", help="a pattern the reply must contain (case-insensitive)")
    ap.add_argument("--stt", default="http://127.0.0.1:8890", help="the voice shim, for --read-only's words")
    a = ap.parse_args()
    if not (a.key and a.core_token):
        ap.error("--key and --core-token (or AURA_API_KEY and HASS_TOKEN in the environment) are required")
    if a.phone and a.text == ap.get_default("text"):
        a.text = "Turn on my phone's torch."

    timings: dict[str, float | str] = {}
    before = "" if a.phone or a.read_only else core_state(a.core, a.core_token, a.entity)
    if not (a.phone or a.read_only):
        print(f"0. {a.entity} before: {before}")

    t = time.time()
    voice, rate = speak_pcm(a.base, a.key, a.text)
    mic = resample(voice, rate, CAR_RATE)
    print(f"1. question as car-microphone audio: {len(mic) // 2 / CAR_RATE:.2f}s at {CAR_RATE} Hz mono ({time.time() - t:.2f}s to make)")

    t = time.time()
    body = {"data_url": "data:audio/wav;base64," + base64.b64encode(wav(mic, CAR_RATE)).decode(),
            "mime_type": "audio/wav"}
    if a.read_only:
        # Jarvis's own voice is not a person: through the adapter the gate refuses it and,
        # close enough to the owner, holds it two hours for "That was me" (speaker.Unrecognised)
        # -- a nightly claim in the owner's tray that would file the TTS voice as their print.
        # So the words come from the same card Whisper the adapter asks first, and the gate is
        # asked on its own, where a sample is scored and nothing is kept.
        verdict = gate_verdict(a.core, a.core_token, wav(mic, CAR_RATE))
        if verdict.get("accepted"):
            sys.exit(f"FAIL step 2: the speaker gate accepted Jarvis's own voice as {verdict.get('label')!r}")
        timings["gate"] = f"refused: {verdict.get('reason') or '?'} (nearest {verdict.get('nearest')!r})"
        heard = card_words(a.stt, body)
    else:
        heard = http_json(a.base, a.key, "/api/audio/transcribe", body)
    transcript = (heard.get("transcript") or "").strip()
    timings["transcribe_s"] = round(time.time() - t, 2)
    print(f"2. transcribed in {time.time() - t:.2f}s: {transcript!r} speaker={heard.get('label') or heard.get('speaker')!r} "
          f"ticket={'yes' if heard.get('speaker_ticket') or heard.get('ticket') else 'no'} dropped={heard.get('dropped', False)}")
    if not transcript:
        sys.exit("FAIL step 2: empty transcript")

    t = time.time()
    ticket = heard.get("speaker_ticket") or heard.get("ticket")
    commands: list[dict] = []
    if a.phone:
        reply, approvals, commands = asyncio.run(ask_with_phone(a, transcript, ticket))
    else:
        reply, approvals = asyncio.run(ask(a.base, a.key, transcript, ticket, a.timeout, approve=not a.read_only))
    timings["answer_s"] = round(time.time() - t, 2)
    print(f"3. Jarvis answered in {time.time() - t:.2f}s: {reply[:200]!r}; approvals: {approvals or 'none'}")
    if not reply:
        sys.exit("FAIL step 3: no reply")
    if a.read_only and approvals:
        sys.exit(f"FAIL step 3: a read-only question asked for approval: {approvals}")
    if a.expect and not re.search(a.expect, reply, re.I):
        sys.exit(f"FAIL step 3: the reply does not say {a.expect!r}")

    t = time.time()
    pcm, out_rate = speak_pcm(a.base, a.key, reply[:600])
    print(f"4. reply voice for the car: {len(pcm)} bytes PCM at {out_rate} Hz ({len(pcm) / 2 / out_rate:.1f}s of audio, {time.time() - t:.2f}s)")
    timings["speak_s"] = round(time.time() - t, 2)
    if not pcm:
        sys.exit("FAIL step 4: no reply audio")
    if a.read_only:
        print("RIDE " + json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "question": a.text,
                                    "transcript": transcript, **timings, "reply": reply[:200]}))
        print("CAR TURN PROBE PASSED")
        return

    if a.phone:
        torch = [c for c in commands if c.get("action") == "toggle_torch"]
        print(f"5. the phone received: {[(c.get('action'), c.get('params')) for c in commands] or 'nothing'}")
        # --want off with --text "Turn off my phone's torch." is the case that needs the
        # action's params: sent as {}, the phone's default switches the torch ON.
        if not torch or torch_on(torch[-1]) != (a.want == "on"):
            sys.exit(f"FAIL step 5: the phone never received a toggle_torch that switches it {a.want}")
        print("CAR TURN PROBE PASSED")
        return

    after = before
    for _ in range(20):
        after = core_state(a.core, a.core_token, a.entity)
        if after == a.want:
            break
        time.sleep(1)
    print(f"5. {a.entity} after: {after}")
    if after != a.want:
        sys.exit(f"FAIL step 5: {a.entity} is {after!r}, wanted {a.want!r}")
    print("CAR TURN PROBE PASSED")


if __name__ == "__main__":
    main()
