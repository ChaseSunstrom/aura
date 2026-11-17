# The car stack (from Headway)

The phone side of wireless Android Auto, folded into Jarvis (ADR-0047,
`docs/adr/0047-jarvis-is-the-phone-side-of-android-auto.md`). These are
Headway's modules, with their history:

| Module | Gradle | What |
|---|---|---|
| `core-protocol` | `:car-protocol` | AAP framing, channel multiplexing, the protobuf schemas (JVM) |
| `core-transport` | `:car-transport` | TCP, in-memory TLS, the Bluetooth handshake codec, the reconnect supervisor (JVM) |
| `core-dash` | `:car-dash` | the car screen's layouts, rail, themes and allow-list (JVM) |
| `core-voice` | `:car-voice` | the voice command grammar (JVM) |
| `headunit-emulator` | `:car-emulator` | a head unit on the JVM that drives the acceptance suite |
| `core-video`, `core-input`, `core-audio` | `:car-video`, `:car-input`, `:car-audio` | screen capture, touch injection, the audio channels (Android) |

The app side -- the link service, the car screen and its tabs, setup -- is
`android-app/app/src/main/kotlin/ai/jarvis/app/car/`. The design record is
`docs/car/`: Headway's ADRs (`adr/H-*`), the protocol notes, the blockers and the
progress ledger. What only a drive proves is `docs/car/drive-checklist.md`.

## Working on the protocol

Every protocol constant is cited, file and line, in `docs/car/protocol-notes.md`;
none is guessed. The JVM suites need no device:

```
cd android-app && gradle :car-protocol:test :car-transport:test :car-dash:test :car-voice:test :car-emulator:test
```

Headway's files keep their GPLv3 headers (`LICENSE`); the same owner holds Jarvis.
