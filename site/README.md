# The Pages site

The project's public page: one scroll, in the AURA theme, with the console's own galaxy core
live on the first screen, a section and a film for each feature, and the install line.

```sh
cd jarvis-web && npm ci   # once: the build uses the console's bundler
make site                 # type-checks site/src, then builds into _site
```

`_site` is a plain folder. To look at it, serve it from any static server, under a sub-path if
you want what GitHub Pages does (`.github/workflows/pages.yml` deploys this same folder from
`main`).

## Where each part comes from

The site has no words, addresses or values of its own. `build.mjs` fills `index.html` from:

| part | source |
|---|---|
| the opening paragraph, each feature's heading, paragraph and picture caption, the install lines, what the install needs, what the films are | the root `README.md` |
| which features there are, in what order, and each film's size | `tests/contracts/readme-clips.json` |
| the films and the still each shows before it plays | `site/media/*.mp4` and `site/posters/*.jpg`, both written by `make readme-clips` |
| the core on the first screen | the console's renderer, `jarvis-web/src/lib/ds/core`, bundled into `site.js` (the galaxy only: the reactor needs three.js and is left out) |
| colours, type, spacing, durations | the console's generated `tokens.css` and `themes.css` |
| the fonts | `jarvis-web/static/fonts`, the AURA theme's two faces with their licences |

So a reworded README, a new clip or a changed token changes the site at its next build. A
paragraph the template asks for that the README no longer has stops the build and names it;
`deploy/tests/test_site.py` says so first.

To add a section for a new feature, add its clip (a row in the table, a script in
`jarvis-web/e2e/readme-clips.spec.ts`, a section in the README): the site picks it up. To place
a film by hand, as the install section does, write `@@film:<name>@@` in `index.html`.

## What it promises

- **Self-contained.** Every file it loads is in the folder; its content policy is
  `default-src 'self'`; its script fetches nothing and stores nothing.
- **Reduced motion.** The core stands still, the tour of its states does not run, no film
  starts by itself and each film keeps the browser's own controls.
- **No script.** The page still reads: a still of the core, the install lines as text, every
  film with the browser's controls.
- **Phones.** One column under 900px, nothing wider than the screen.

`jarvis-web/e2e/site.spec.ts` builds the folder, serves it under a sub-path and holds each of
these in a browser. It does not judge the look: `SITE_SHOTS=<folder>` makes it write the
screenshots, and `docs/screens/site/` holds the pair that was looked at.

Not covered: GitHub's deploy itself, which runs only there, and a browser with neither WebGL2
nor a 2D canvas, which keeps the still picture.
