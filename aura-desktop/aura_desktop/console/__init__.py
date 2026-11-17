"""The glass console: the terminal surface of the HUD (GLASS_HUD §9.7).

A pane host on the HUD socket — the same JSON-RPC channel the web console
speaks through the adapter — that renders the layout document the ``hud-layout``
plugin emits as tmux-style panes, the ``hud.panel.*`` content the ``jarvis-hud``
plugin pulls up, the chat, the tool timeline, the approvals and the goal, with
the ANSI reactor of :mod:`aura_desktop.reactor` as its status element.

It is not Hermes's own TUI and does not touch it: that program is an Ink/React
app that takes a skin YAML and nothing else, so a layout tree and eleven
reactor states could only reach it by a fork. This package is that separate
pane host.

The modules, in dependency order:

* :mod:`theme` — the generated theme read at runtime, the colour and glyph
  ladders, painting.
* :mod:`panes` — from a layout document to cell rectangles, per mode.
* :mod:`state` — the reducer from gateway events to what the panels show.
* :mod:`render` — a frame: rows of text for a terminal of a given size.
* :mod:`app` — the socket, the keyboard, the loop, and ``--demo``.

What this surface cannot do, it says rather than fakes: there is no blur, no
overlap (Floating tiles), no hover (verbs are keys), and no split/swap/close —
those operations live in the plugin and on the web, and the console renders
every layout they produce.
"""

from __future__ import annotations
