# Private chats

A private chat is a conversation with Jarvis that is not logged or stored on the server. It
lives in the open page's memory. Switch it off, close the chat or close the page, and it is
gone. The owner asked for it on 2026-10-30: "ephemeral or incognito chats, that won't be
logged or stored absolutely anywhere" (T-0462).

## Using it

Open the chat in the console (or the phone, which shows the console) and press **Private**
in its header. The title changes to *Private conversation* and a banner says what that
means. Type or hold the mic as usual. Press **Private** again, start a new chat, open a past
one or close the chat, and the conversation is forgotten.

A private Jarvis can talk but cannot act. It has no tools, no memory of other conversations
and no access to the house. Ask it to turn on a light and it says it can't from here.

## How it works

An ordinary turn goes to Hermes. Hermes writes every turn to its session store, its logs and
its memory, so a private turn never goes there.

| Step | Ordinary | Private |
| --- | --- | --- |
| Where the conversation lives | Hermes' session store (`state.db`, `sessions/`) | the page's memory ([`private.svelte.ts`](../../aura-web/src/lib/hud/private.svelte.ts)); the whole conversation is sent each turn, up to 80 lines and 40,000 characters |
| Who answers | Hermes, with tools, memory and skills | the adapter's `POST /api/private/chat`, straight to the model with a fixed persona and no tools, on the model picked in the chat's select (it starts on the chat's own; the uncensored one is a pick away) ([`app.py`](../../aura-adapter/aura_adapter/app.py)) |
| Request log | the adapter's access line names the path | the access line and the outgoing model call's log line are dropped (`_NoPrivateLines`) |
| Model server | llama-swap could hold request and reply bodies for its Captures page | `captureBuffer: 0` for every request ([`base.yaml`](../../deploy/models/seed/base.yaml)) |
| Spoken reply | made on the voice card, short lines kept in the phrase cache | made on the voice card, never put in the cache. If the voice card is down, the line is not spoken: the house's fallback voice would log it |
| Heard words | the clip goes to the voice card (or Hermes' Whisper, which writes it to disk); an unknown voice's clip is held for the owner to claim | the clip goes to the voice card's RAM (`/dev/shm`) only; with no voice card the turn is refused, and an unknown voice's clip is dropped, not held |
| Browser | sessions listed and resumable | nothing written to `localStorage` or `sessionStorage`; spellcheck and autocomplete off |

## What it does not cover

- **This device.** The page holds the conversation in memory while it is open, and the
  operating system may page that memory to swap. A phone keyboard can learn words you type:
  there is no web setting to stop it, so use the mic or the keyboard's own incognito mode.
  Copy puts a reply on the clipboard, and clipboard history keeps it.
- **The model's working memory.** vLLM keeps recent prompts in its GPU cache until newer
  requests overwrite them. Nothing writes it to disk.
- **That you used it.** The voice routes (`/api/audio/speak-pcm`, `/api/audio/transcribe`)
  still get an access line, the same as an ordinary one, with no words in it. A reverse
  proxy in front of the console logs whatever it is set to log; it sees paths, and bodies
  only if set to record them.
- **Acting on it.** Device actions go through Hermes and the house, which record them.
  Leave private mode to act.
- **The car and the desktop app's native voice.** Android Auto always talks to Hermes. The
  desktop app (Tauri) shows the console, so private works there as in a browser.
- **Barge-in.** Push-to-talk does not interrupt a private reply that is being spoken; a new
  question stops the old one.
- **Hermes' own chat API.** `/v1/chat/completions` through the adapter also loses its
  outgoing log line, a side effect of the same filter. That is not a private chat: Hermes
  still keeps it.

A private chat with tools would need a second Hermes that keeps everything in memory. That
is T-0463, not built.

## Proving it

[`scripts/verify/private-chat-canary.sh`](../../scripts/verify/private-chat-canary.sh) checks
the running stack. It sends a one-off word in a private chat, speaks it privately, then
searches for that word in:

- every container's log since the chat;
- Hermes' whole home;
- the adapter's artifacts and data;
- the voice card's and the house's storage.

It also confirms that the model server keeps no capture. Each check fails if its store could
not be read, so an unreadable store never passes as "nothing there". The tests are
`aura-adapter/tests/test_private_chat.py`, `aura-web/src/lib/hud/private.test.ts` and
the private case in `aura-web/e2e/v2-chat.spec.ts`. The browser test checks that
the word never appears in a WebSocket frame or in browser storage.
