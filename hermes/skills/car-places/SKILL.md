---
name: car-places
description: "Nearest place, where did I park, navigate, take me to, stops on the way, how long, text my ETA, change the route: in the car, finding the nearest coffee, fuel or an address near the car and sending the route to its navigation app."
version: 0.3.0
---

# Places and routes from the car

A turn from the car ends with a line saying where the car is:

```
(In the car, at 41.8781,-87.6298, heading 90°, 50 km/h. For places, place_search near 41.8781,-87.6298; to go, car_navigate.)
```

That line is the phone's, not Sir's words. Use the position; do not read it back to him.

## Finding a place

1. **A kind of place** ("the nearest coffee", "a gas station", "a pharmacy near me"):
   `place_search` with `kind` as an OpenStreetMap tag -- `amenity=cafe`, `amenity=fuel`,
   `amenity=pharmacy`, `amenity=parking`, `amenity=restaurant`, `amenity=fast_food`,
   `shop=supermarket`, `shop=hardware`, `amenity=charging_station` -- and the car's position
   as `near` ("41.8781,-87.6298"). Everything of that kind within 5 km, nearest first.
   **A name or an address** ("Intelligentsia on Randolph", "1600 Pennsylvania Ave"): `query`
   with his words, and `near` too. Results carry `name`, `address`, `lat`, `lon`, `distance_km`.
2. Answer in one or two spoken sentences: the nearest one or two, by name and how far
   ("Blue Door Hardware, a mile away on State Street"). He is driving: no lists, no numbers
   he has to read, no coordinates.
3. No line about the car (he asked from the phone): `phone_action get_location` gives the
   point for `near`. An address needs no point at all. No results: say so and ask where to look.
4. **"Where am I?"**: `place_search` with only `near` (no `query`, no `kind`) answers the
   address there. Say the street and the town, never the coordinates.
5. **"Where did I park?" / "where's my car?"**: `phone_action where_parked` gives the
   car's `latitude`, `longitude` and `minutes_ago` from when it last disconnected. Answer
   with `place_search` `near` that point (the street, not coordinates) and how long ago;
   on foot, it also gives `metres_away` and `direction` from where the phone is now --
   "About 200 metres north of you, on Quarry Road". "Take me to it" is `car_navigate`
   to that point. No fix saved: say the phone keeps one
   from the next time the car disconnects.
6. A failed search says why in `error`; try once more with the town added, then say so.
   Do not reach for the browser to geocode: `place_search` already falls back to
   OpenStreetMap's Nominatim when Photon is slow.

## Going there, or changing the route

1. When he says go, take me there, navigate, or picks one: `car_navigate` with its `lat`,
   `lon` and `name` as `label`. Leave `app` empty unless he named one (Organic Maps is
   `organic`, Google Maps `google`, HERE WeGo `here`).
2. A different destination later ("go to the other one", "somewhere with parking instead")
   is another `place_search` then another `car_navigate`: the new route replaces the old.
   "Cancel the route" / "stop navigating" is `car_navigate` with `cancel: true` and no place.
3. **Stops on the way** ("go home, but stop at the pharmacy and the bank first"): find each,
   then one `car_navigate` to the final place with `via` as the stops in order, each
   `{lat, lon, label}` (up to 5). Jarvis's map routes through them and says each stop; a
   named phone app gets only the final place, so say so if he asked for one.
4. **How long / ETA** ("how long to the store?", "when will I get home?"): `route_eta` from
   the car's position to the place; say its `said`. "Text Ren my ETA" is `route_eta` to his
   destination, then `phone_action send_sms` with the minutes and arrival time in his words.
5. It works from the phone too, with the car not linked yet: the phone opens his maps app.
   Say it once, briefly: "Heading to Blue Door Hardware." The route shows on Jarvis's own
   map on the car screen, and the phone says each turn aloud itself, timed to the car's
   speed -- do not describe turns yourself, you would be late.

## How the route is driven

Two settings, which Sir can have you change ("use Google Maps from now on", "stop saying the
turns"): `settings_set` `hud.car_navigation` to `jarvis` (Jarvis's map, the default),
`organic`, `google` or `here`; and `hud.car_speak_directions` to `true` or `false`. A named
app in one request (`app` on `car_navigate`) is for that trip only.
