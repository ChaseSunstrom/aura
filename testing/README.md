# testing/ — the end-to-end harness

The real `jarvis-core`, booted against fake model and voice backends, so any
client can be driven end to end with no GPU, no models and no hardware.

```bash
pip install -r aura-core/requirements.txt -r testing/requirements.txt
python3 -m pytest testing/live/tests -q   # the rig's own tests
python3 testing/harness/harness.py --wait # keep one up for a device/emulator
```

| File | What it is |
|---|---|
| `harness/fake_ollama.py` | Ollama's `/api/tags` + NDJSON `/api/chat`, scripted. Stdlib only. |
| `harness/fake_wyoming.py` | whisper/piper/openWakeWord over the real Wyoming framing. Stdlib only. |
| `harness/harness.py` | Writes a config, starts all three processes, waits for `/healthz`, prints JSON. |
| `harness/client.py` | The async REST+websocket client the suites drive it with. |
| `harness/scripts/default.json` | The fake model's default brain. |

Nothing here is mocked on the server side — a real `python -m aura` process
is listening on a real socket. Only the two things that would need a GPU are
replaced, and both are replaced at the wire protocol.

The full map, including what still needs real hardware:
[`docs/how-to/testing.md`](../docs/how-to/testing.md).
