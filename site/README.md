# The Pages site

The project's public page: one scroll, with the console's own core live on the first screen, a
section and a film for each feature, then what AURA is made of: its numbers, the path of a turn,
the approval matrix, everything it can reach, and its two faces. A switch in the masthead
changes the page between AURA (the galaxy) and JARVIS (the reactor) in place;
`?theme=jarvis` opens it in JARVIS.

```sh
cd aura-web && npm ci   # once: the build uses the console's bundler
make site                 # type-checks site/src, then builds into _site
```

`_site` is a plain folder. To look at it, serve it from any static server, under a sub-path if
you want what GitHub Pages does (`.github/workflows/pages.yml` deploys this same folder from
`main`).

## Where each part comes from

The site has no words, addresses or values of its own. `build.mjs` fills `index.html` from:

| part | source |
|---|---|
| the opening paragraph, each feature's heading, paragraph, bullets and picture caption, the steps of a turn, the install lines, what the install needs, what the films are | the root `README.md` |
| the numbers, the plugins and their tools, Hermes's toolsets, the approval matrix's verdicts and reasons, the skill count | the generated reference pages, `docs/reference/{plugins,tools,approval-matrix,skills}.md`, read by `site/data.mjs` |
| the benchmark's categories | `CAPABILITIES.md` |
| the two faces (name, core, wake words, voice, manner, ready line) | the theme packs, `themes/*/theme.json` |
| which features there are, in what order, and each film's size | `tests/contracts/readme-clips.json` |
| the films and the still each shows before it plays | `site/media/*.mp4` and `site/posters/*.jpg`, both written by `make readme-clips` |
| the cores (the first screen, the docked one, one in each face card) | the console's renderer, `aura-web/src/lib/ds/core`, bundled into `site.js`; the reactor draws with its 2D drawer, so three.js is left out |
| colours, type, spacing, durations | the console's generated `tokens.css` and `themes.css` |
| the fonts | `aura-web/static/fonts`, both themes' faces with their licences (a face downloads only when the page wears it) |

So a reworded README, a new clip, a new plugin or a changed token changes the site at its next
build. The drawing of a turn in `index.html` is the one thing drawn by hand: each part names
the README steps it lights (`data-on`), and `test_site.py` fails when those and the README's
numbered steps part. A
paragraph the template asks for that the README no longer has stops the build and names it;
`deploy/tests/test_site.py` says so first.

To add a section for a new feature, add its clip (a row in the table, a script in
`aura-web/e2e/readme-clips.spec.ts`, a section in the README): the site picks it up. To place
a film by hand, as the install section does, write `@@film:<name>@@` in `index.html`.

## What it promises

- **Self-contained.** Every file it loads is in the folder; its content policy is
  `default-src 'self'`; its script fetches nothing and stores nothing (the face it wears lives
  only in its address).
- **Reduced motion.** The cores stand still, the tour of states does not run, no film starts by
  itself and each keeps the browser's own controls, the figures do not count, the approval
  reasons are a list rather than a ticker, and nothing arrives.
- **No script.** The page still reads: a still of the core, every figure written, the steps as a
  list, the install lines as text, every film with the browser's controls; the switch, the hold
  and the dock, which need the script, are not shown.
- **Phones.** One column under 900px, nothing wider than the screen.

`aura-web/e2e/site.spec.ts` builds the folder, serves it under a sub-path and holds each of
these in a browser. It does not judge the look: `SITE_SHOTS=<folder>` makes it write the
screenshots, and `docs/screens/site/` holds the pair that was looked at.

Not covered: GitHub's deploy itself, which runs only there, and a browser with neither WebGL2
nor a 2D canvas, which keeps the still picture.
