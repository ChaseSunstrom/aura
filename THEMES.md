# Themes

A theme is how the assistant looks, sounds and wakes: name, colours, type, shape, motion,
background, core renderer, car map style, voice, wake word, icons, earcons and haptics. Two ship:
**AURA** (the default) and **JARVIS**. Adding a theme means adding a folder. App code does not change.

## Make one

1. Copy a pack: `cp -r themes/jarvis themes/sunrise`. Set `"id": "sunrise"`; the id must match the folder name.
2. Edit `themes/sunrise/theme.json`. `themes/theme.schema.json` says what each field takes:
   - `color` — role → hex. Text roles must reach 4.5:1 over the surfaces they are drawn on.
   - `typography` — `display` and `mono` families, plus `faces` such as `sora-300`. Each face must be vendored: add it to `FACES` in `design/fonts.py` and run `python3 design/fonts.py --missing`.
   - `core.renderer` — `galaxy` or `reactor`; `core.states` holds each state's parameters.
   - `map.style` — `space-drive` or `hologram`.
   - `voice` — a stock Kokoro voice id (`af_heart`, `bm_george`, …). Never a clone of a real person.
   - `wake` — the phrases, plus `model`: `builtin:<name>` or `assets/wake/<id>.onnx`, which needs a `<id>.metrics.json` beside it (see "Wake word" below).
   - `icons` — the app icon, every size in `sizes` (its key is the pixel size), monochrome and favicon.
3. Put the files the pack names under `themes/sunrise/assets/`. `python3 scripts/themes/earcons.py sunrise` synthesises a set of earcons.
4. Generate and check:

   ```bash
   python3 design/build.py                  # every platform's theme files
   python3 scripts/verify/theme_check.py    # schema, contrast, assets, fonts, wake, voice
   python3 -m pytest deploy/tests/test_themes.py -q
   ```

   `theme_check.py --live http://127.0.0.1:8890` also asks the voice server whether it has the voice.

5. Give the theme a persona. A persona whose name is a pack id wears that pack; every other persona wears JARVIS's. To add one, list it in `tests/contracts/persona.json` (`personas`, `hues` and `themes`) and in each reader that file names.

## What gets generated

`design/build.py` runs `design/themes.py`. Do not edit its outputs by hand:

| File | Read by |
|---|---|
| `aura-web/src/lib/styles/themes.css`, `aura-desktop-app/ui/themes.css` | the console, the desktop app and the overlay: one `:root[data-theme='<id>']` block per pack |
| `aura-web/src/lib/themes.ts` | the console's renderers, the gallery and the switch |
| `android-app/…/ui/theme/JarvisThemes.kt` | the phone and the car |
| `aura-desktop/aura_desktop/themes.py` | the desktop agent and its terminal console |
| `hermes/plugins/aura-personas/themes.json` | the persona switch: name, voice, wake phrases, strings |

A pack re-points the existing `--jv-*` names, so a screen that uses tokens is themed without being edited. The JARVIS pack is the base look and moves nothing.

## Wake word

Train the head off CI, with openWakeWord's synthetic pipeline. The negatives must include near-misses of the phrase. Then measure it on held-out positives and background speech, and write `<model>.metrics.json`:

```json
{"falseAcceptsPer24hSpeech": 0.6, "falseRejectRate": 0.04, "measured": "how and on what"}
```

A head that misses the false-accept target stays on only when `wake.useSpeakerVerification` is true. AURA's does (DECISIONS 103):
on the phone and in the car, a turn her head opens sends nothing until the house's speaker gate recognises the voice.
A head named after a pack fires at that pack's `wake.threshold`, two frames running (`WakeSensitivity.scorerFor`).
