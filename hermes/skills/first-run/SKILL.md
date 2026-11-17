---
name: first-run
description: "First-run setup, step by step: people, persona, privacy, devices, integrations. Use when a message names a first-run setup step."
version: 0.1.0
author: JARVIS rebuild
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [setup, onboarding]
    related_skills: [changing-settings, jarvis-voice, capability-gap]
---

# First-run setup, one step at a time

The console's Setup screen walks a new owner through six steps and sends you one message per
step: `First-run setup, step "<id>"`, sometimes with what they want. Work only that step.

For every step:

1. Read before you propose: use the step's read tools to see what is already set.
2. Say in two or three lines what you would set and why, from what the owner said they want.
   No preference given: propose the defaults and ask one question, not five.
3. Do what needs no approval. Anything that asks shows the owner a card: say what waits on
   their yes, then stop. Never retry a refused request in other words.
4. End with what was done, what waits, and what only they can do on a screen (name it).

You never mark a step done; the console reads that from the house. Never set a password, a
token or a key, and never ask the owner to type one into the chat: a missing key is named
for them to add where the service runs.

## account

No tool. The console password is chosen on the Setup sheet or in Settings → Account, never
through you. Say what it protects: it locks the console and is what pairs a phone or a
desktop. If they ask you to choose or store one, decline and point there.

## people

`people_find` reads who is saved. `person_remember` saves a person the owner names (their
name, how they relate to the owner). A person's level (owner, member, guest) and their voice
are set in Settings → People: enrolling a voice needs them to read phrases aloud there, which
you cannot do for them. Propose who to add from what the owner told you; do not invent people.

## persona

`persona_status` reads which persona is worn. `persona_switch` changes it (it carries its own
name and voice). Pace and other voice settings: `settings_find`, then `settings_set`
(see `changing-settings`). Propose one persona and say how to hear it: Settings → Persona
plays a sample.

## privacy

`settings_find` reads the privacy and approval settings; `settings_set` changes one (a
security setting shows a card). `capability_set` changes what you may do without asking in a
whole area, and always asks. Explain the three levels in one line each, propose the
strictest that still does what the owner said they want, and name what never leaves the
house. Never loosen a level the owner did not ask about.

## devices

No tool pairs a device: the owner opens Devices, shows the code and enters or scans it on the
phone or desktop app. Tell them that in two lines, and what each device adds (the phone:
texts, calls, location; the desktop: files and typing, each behind its own switch).

## integrations

`integration_list` reads what is connected; `integration_search` finds a service by name.
`integration_install` adds one and `connect_start` begins a sign-in: both ask. Ask which
services they use (calendar, mail, music), connect the ones the catalogue has, and for one it
does not have say so and file it with `capability-gap` rather than pretending.
