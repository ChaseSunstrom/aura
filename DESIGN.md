# Design

## Direction

AURA is a core in the middle of a deep ground, and everything else is light it projects:
starlight on deep space, a violet accent spent on what is current, labels in tracked capitals,
figures in a light display face. JARVIS, the second theme, is the same system in projected cyan.

The design system itself (components, the four screen states, motion) is the console's:
`aura-web/src/lib/ds` and the `jarvis-design-system` skill. Every value the apps use lives in
`design/tokens.json` and the theme packs under `themes/`; this file does not add to them.

## Tokens

The palette the README's figures are drawn in (`sampo readme diagram` reads this table). The
dark column is AURA's own colours, copied from `themes/aura/theme.json`
(`deploy/tests/test_readme_media.py` fails when they part). The light column exists only for
figures on a light page: the same hues, darkened until they read on white.

| Token | Kind | Light | Dark | Source |
| --- | --- | --- | --- | --- |
| `--bg` | colour | #F7F4FF | #05030D | bg.void |
| `--surface-raised` | colour | #FFFFFF | #120828 | bg.deep |
| `--text` | colour | #170A2E | #F7F2FF | text |
| `--text-muted` | colour | #5E5288 | #AFA1D6 | text.label |
| `--border` | colour | #D9D0EE | #2A1250 | bg.nebula |
| `--accent` | colour | #6B4FD8 | #B79CFF | accent |
| `--ok` | colour | #0F8F63 | #6EE7B7 | ok |
| `--warn` | colour | #9A5B07 | #FFC36B | attention |
| `--danger` | colour | #C2334F | #FF6B8B | danger |
