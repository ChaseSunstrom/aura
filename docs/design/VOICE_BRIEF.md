# The voice brief

How JARVIS speaks. This is the spoken half of the copy rules in
the Glass HUD plan §11; the console writes in that register and the
voice says it in the same one. It is a description of *delivery* — pace, pitch
movement, attitude — and deliberately not an impression of any performer,
character or real person.

## The brief

> **Withdrawn from the service 2026-11-10 (T-0955).** The brief below was CosyVoice 3's delivery
> direction, sent with every line from `aura_voice/brief.py`. CosyVoice was removed and
> Kokoro, which speaks every pack now, takes no direction: a caller's `instructions` is dropped.
> The page stays as the description of how the voice should sound.

> Speak in a calm, precise British-English voice. Unhurried and level, with a
> dry, understated warmth. Land the ends of sentences rather than rising into a
> question. No excitement, no salesmanship, no apology.

Short on purpose. CosyVoice takes the instruction in the same context window as
the sentence it is speaking, so a paragraph of stage direction crowds out the
line itself.

Read alongside §11, that means:

- **Calm, precise, brief.** The voice does not perform. A tool call that
  succeeded is stated, not celebrated.
- **No exclamation in system copy.** Nothing in the spoken register is
  exclaimed. Dry wit belongs only in the assistant's own replies, and even
  there it is delivered flat.
- **Errors keep the same pitch as everything else.** "Couldn't reach the
  house. The adapter answered 502." is read at the level of any other sentence.
  A voice that dramatises a failure makes the failure feel worse than it is.
- **Numbers are read as figures with their units**, at the same pace as the
  words around them, not rushed.
- **Falling ends.** English statements that rise at the end read as uncertain,
  and an assistant that sounds unsure of a fact it just verified is worse than
  one that sounds dull.

## Where the voice actually comes from

CosyVoice 3 has no voice of its own. The released weights
(`FunAudioLLM/Fun-CosyVoice3-0.5B-2512`) ship no `spk2info.pt`, so the two modes
that take a speaker id — `inference_sft` and `inference_instruct` — have an
empty table to draw from. Every mode that works (`inference_zero_shot`,
`inference_cross_lingual`, `inference_instruct2`) takes a reference clip. The
brief above steers how a voice speaks; it cannot originate one.

So the identity is whichever WAV is registered as `jarvis`, and that is a
choice with a rights question attached. Three routes, in the order they should
be considered:

1. **Bootstrap from the voice already in the stack.** Synthesise the reference
   clip with the Piper voice this house already ships and is already licensed
   to use, then let the brief shape its delivery. Nobody's recording is
   collected, JARVIS keeps sounding recognisably like itself across the change,
   and the reference is reproducible from a command rather than a file somebody
   has to keep. This is the recommended default.
2. **A recording the operator owns.** Register as the voice (a `<persona>.wav`
   under `COSYVOICE_VOICES_DIR`) a WAV that is your own voice, or one you hold the rights to. Six to ten seconds of
   clean, unhurried speech is enough. The runbook says this too, because it is
   the setting most likely to be pointed at something it should not be.
3. **A speaker from a consented, permissively licensed corpus** — a voice whose
   owner released it for exactly this. Attribution follows whatever the corpus
   licence asks for.

Not on the list, and not implementable here: audio of a film performance, or
any other recording of a real identifiable person who has not agreed to it. The
character of the speech described above is what the house is after, and it is
reachable from any of the three routes.

Whichever the operator settles on is then frozen as the named preset `jarvis`
in the settings schema (`tts.voice`), so the console, the phone, the desktop
and the benchmark all ask for one name and get one voice.
