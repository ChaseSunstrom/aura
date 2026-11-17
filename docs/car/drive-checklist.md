# The drive that proves the car (ADR-0047)

Everything the JVM suites and the emulator cannot prove, in the order a first drive meets it.
Each step that passes moves its row in `docs/verification.md` (the car section) from Unproven
to Manual, with the exported drive log's path beside it: Settings → Car → Diagnostics →
**Export the drive log**.

Needs: the phone with this build installed and Headway uninstalled (both claim the same
car-app permission, so one of them cannot install), the car, and about forty minutes.

0. **Before leaving: the server side of a car turn.** From the host,
   `python3 scripts/hermes-probes/car_turn_probe.py` (arguments in its docstring) must pass: it
   sends car-microphone audio through the same transcribe, socket and speak path the phone uses
   in the car, so a failure here is the server's, not the car's, and not worth a drive.
1. **Setup, timed.** Settings → Car → **Set up a car**. From the first tap to the car screen
   showing Jarvis. Target: under five minutes, with the static-IP step the only manual one on a
   car that announces `access_point_type=STATIC`.
2. **Jarvis stays reachable.** The phone's in-app WireGuard tunnel on for the whole drive (Settings' WireGuard line
   reads Running, not "saved but not up"). Ask a question on the Jarvis
   tab ten minutes in: it must be answered (the car's network carries only the car's socket).
3. **Ignition with the phone in a pocket.** With the car's connect-device-first on, start the
   car and do not touch the phone: the session must start on its own.
4. **Voice.** The steering-wheel voice button, then "what's on my calendar today". The answer
   must play over the car's speakers with the music ducked under it, and the Jarvis tab's
   reactor must listen, think and speak with it (`car-tab-jarvis-speaking.png` is the frame). Then "next track": it must
   act at once and never reach the server. On a car that ignored the microphone setup at
   session start, each press also asks the car again: the drive log's `microphone probe:`
   lines say what it answered, and `voice: hands-free:` whether the Bluetooth microphone
   opened. Once the car streams on a press, the next press listens with the car's microphone.
5. **Touch.** Every tab — Jarvis, Maps, Music, Apps, Phone, Messages, Car — selected by touch,
   and a control inside each used once.
6. **Music.** Twenty minutes of a music app over AAP. Afterwards the drive log's
   "worst gap in capture" and "stalls" must be non-zero-able numbers (they were never assigned
   in Headway; Task 11 of the plan pins that they are) and sane.
7. **A thirty-minute drive**, then the drive log exported and filed beside this checklist's
   results. Every 10 s the log carries a `wi-fi:` line (signal, band, link rates, screen
   on or off, the low-latency lock) and one more `as it ended,` when a session drops: read
   each reset against them. Android honours the lock only with the screen on, so a stretch
   with the phone's screen on tells whether the resets follow the screen (T-0370).

## After the drive

Every car problem reported from an earlier drive stays open until the next drive's exported log has been read for it, whoever fixed it and however many tasks the fix was spread over. A report fixed piecemeal by several tasks is the case that slipped (T-1166): each task looked closed, and nobody re-read a later log. For each open report, the reader records the log line that shows it gone or the line that shows it still there.
