---
name: home-presence
description: "Who's home, is Isa home, what's on my network, name this device: who is in the house from the devices on the home Wi-Fi."
version: 0.1.0
---

# Who is home

The house sweeps the home LAN every two minutes and publishes what answered.

- **"Who's home?" / "is Isa home?"**: `ha_list_entities(domain="device_tracker")`, then
  `ha_get_state` the ones that matter. `home` means the device answered within the last ten
  minutes; `not_home` means it has not; `unknown` means the house restarted and has not swept
  yet. Answer with the person, not the device ("Isa's phone is on the Wi-Fi, so she's home").
- **"What's on my network?" / "anything new?"**: `ha_get_state("sensor.lan_devices")`. Its
  `devices` list has `mac`, `ip`, `name` (if named), `private` and `first_seen`. A
  `private: true` device is almost always a phone or tablet. "New" is the ones with the
  latest `first_seen`. Say how many and which are unnamed; never read MAC addresses aloud
  unless asked.
- **Naming one** ("that new one is Isa's phone", "the one at .141 is the TV"): find its
  `mac` in `sensor.lan_devices`, then `ha_call_service("netscan", "name_device",
  {"mac": ..., "name": "Isa's phone"})`. It becomes `device_tracker.isas_phone`, which a
  routine or automation can trigger on ("when Isa gets home …"). Forgetting one is
  `netscan.forget_device` with its name.
- To tell which unnamed device is someone's phone, ask them to toggle Wi-Fi off and on, then
  look for the device whose `first_seen` just changed.
- A device the house has never seen before is announced once, by itself ("a device I have not
  seen before joined the network at …"; the `netscan_new_device` event, for a routine). "Who
  was that?" is its row in `sensor.lan_devices`; offer to name it. Never call it an intruder:
  most are a guest's phone.
- **"Do not disturb" / "be quiet for an hour" / "you can talk again"**:
  `ha_call_service("companion", "set_dnd", {"on": true, "minutes": 60})` (no `minutes`: until
  turned off; `on: false` ends it). While it is on, nothing proactive is spoken, only left as a
  notification, except a critical alert. `companion.presence` says whether it is on.

## Around the phone, anywhere

"What's around me?", "what Wi-Fi networks are here?", "is my watch nearby?", "what Bluetooth
devices can you see?": `phone_action scan_nearby` (optional `seconds`, default 6). It returns
`wifi` (one row per network name, strongest first, with `rssi` and `band`), `bluetooth`
(devices advertising nearby, named ones first; `rssi` closer to 0 is nearer, about -60 is the
same room), `bluetooth_total` and `paired`. Phones rarely show by name on Bluetooth and change
their address every few minutes, so it finds watches, earbuds, cars, TVs and trackers, not
people; say so if asked who is near. Summarise: how many, and the few that matter.

The house's sweep sees only the home LAN: a phone on mobile data, a guest network, or a device asleep long
enough to stop answering reads as away.
