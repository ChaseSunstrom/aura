# Runbook: map data (tiles, buildings, geocoding, routing)

Self-hosted map data for the car's hologram drive and the console (v2 brief §5,
T-0614). Nothing is fetched from a third-party map API: no key, no bill. The
renderer is a separate task (T-0613); this is the data and the one API it calls.
Since T-0765 it is also where every car position goes: the speed limit, routes,
place search and the console's `/api/route` ask the house and nothing public
("What leaves the house", below).

| Need | Source | Where it runs |
| --- | --- | --- |
| Vector tiles | Protomaps daily OSM build, the region cut out by HTTP range (`pmtiles extract`) | a static file, served by jarvis-core with HTTP ranges |
| Buildings | OSM `height` / `building:levels` from the Geofabrik extract; Overture buildings only where OSM has no footprint, or no height | two `.geojsonl` files read by jarvis-core |
| Places (names, kinds, addresses) | the Geofabrik extract's named and addressed features, `osm-places.geojsonl` | jarvis-core, a scan of the file |
| Geocoding | Photon, its own country index | `photon` (profile `geocode`, already in the stack) |
| Routing, map matching | Valhalla, built from the Geofabrik extract | `valhalla` (profile `maps`), loopback only |
| Live traffic | no source; not offered | |

Why Valhalla over OSRM: it map-matches a GPS trace to a road and names the street
(`trace_attributes`), and it costs for cars, bikes and walking from one build. OSRM
is faster per route and has none of those.

## The API (jarvis-core, bearer token, `docs` for the shapes are in `aura/integrations/maps/__init__.py`)

| Call | Returns |
| --- | --- |
| `GET /api/maps/region` | the bbox the data covers and where it came from: `setting` (`MAPS_BBOX`, or `MAPS_CENTER` and `MAPS_RADIUS_KM`), `fetched` (what `map-region.sh` last fetched), `drives` (the 0.1 degree cell the car is driven in most, once it has 20 fixes there, counted from `match` calls and kept in the house's store as `maps_drives`), `house` (the house coordinates); none is a `404 no_region` |
| `GET /api/maps/tiles.pmtiles` | the PMTiles file; supports `Range` |
| `GET /api/maps/buildings?bbox=W,S,E,N` | `{buildings: [{ring, height_m, source, colour?, roof_colour?, roof_shape?, roof_height_m?, roof_dir?, roof_orient?, min_height_m?, kind?, part?, sign?, trade?}], roads: [{line, kind, name, lanes?, oneway?}], details: [{kind, geom, text?}], ground: [{cell, n, h}], truncated}` (the looks and `details` -- trees, lamps, signals, crossings, tree rows, roadworks, parking and its bays, construction, water, green, wood, and since T-1188 farmland, residential and commercial land as the area kinds `field`, `yard` and `paved` -- come only from a pack built since T-1066, and only where the map has them); the bbox may be at most 0.1 degree across, at most 5000 buildings and 5000 roads; a road is every way through the bbox, whole, with its OSM `highway` class (streets, driveways, paths: the car's ground draws them, T-0980) |
| `GET /api/maps/live?bbox=W,S,E,N` | `{zones: [{kind, line}], source, as_of}`: the roadworks (`kind: roadworks`) and closed roads (`closure`) under way now in the box, from the work-zone feed (below). `source` is `off` (no feed set, or the privacy switch: nothing was asked), `down` (the feed has not been read for three TTLs: no zones) or `on` |
| `GET /api/maps/geocode?q=&limit=&lat=&lon=` | `{results: [{name, label, lat, lon}]}`, biased to the house (or `lat`/`lon`) |
| `POST /api/maps/route` `{from:[lat,lon], to:[lat,lon], costing}` | `{distance_m, duration_s, current_street, next_turn:{instruction, street, distance_m, at}, shape}`; only the current street and the next turn are named |
| `POST /api/maps/match` `{points:[[lat,lon],...]}` (oldest first, at most 100) | the last fix snapped to a road: `{lat, lon, street, limit_mph, limit_source, road}`; `limit_source` is `posted` (the way's `maxspeed`), `typical` (the usual limit for its road class) or `none` |
| `POST /api/maps/directions` `{from, via:[...], to}` (at most 5 stops) | the route in OSRM's shape (`routes[].legs[].steps`), which the console's `/api/route` and the car read |
| `GET /api/maps/places?q=&kind=&lat=&lon=&limit=` | `{results: [{name, address, kind, lat, lon, distance_km?}]}` from the house's own index (`osm-places.geojsonl`): by name or address (`q`), a kind of place within 5 km (`kind`, an OSM tag such as `amenity=fuel`), or the address within 150 m of a point (`lat`/`lon` alone); no index is a `404 no_places` |

A service that is down is a `502` naming it; no tiles yet is a `404` saying to run
the script. Dead reckoning between fixes is the client's job (it knows speed and
heading); `match` is the correction it applies on each fix.

`scripts/maps-smoke.sh [CONTAINER]` asks a running house for all of it from inside the
console's container (the region, the buildings around its middle, a route and a match of
that route) and prints counts only; it exits 1 when any answer comes back empty.

## The owner's steps

The short way: `scripts/map-region-from-house.sh` asks the house for its region,
picks the smallest Geofabrik extract that covers it (`scripts/geofabrik_pick.py`),
runs `map-region.sh` with both and starts Valhalla, printing its steps with every
coordinate and URL masked (`map-region.sh`'s own log, which names the bbox, is
`maps-data/map-region.log`). With no setting it waits for the car: drive once with
the car page open and the house learns the region from the drive. `--house` takes
the house coordinates (off by default: they are still the placeholder, GAPS G17);
`--dry-run` plans only; `--cron` is quiet when there is nothing to do, for a
nightly line (`10 5 * * * …/map-region-from-house.sh --cron`, which the owner
installs: crontab changes are theirs to grant). After a change to an extraction
script (`osm_roads.py`, `osm_detail.py`, `osm_buildings.py`) or the pack writer,
`--rebuild` runs `map-region.sh` on the region already fetched, downloading
nothing; without it the wrapper answers "fetched already" and stops. The long way, by hand:

1. **Set the region.** In `aura-core/.env` (never in the repo), one of:
   `MAPS_BBOX=west,south,east,north`, or `MAPS_CENTER=lat,lon` with `MAPS_RADIUS_KM`.
   Unset, the region is the house coordinates in `configuration.yaml` (`jarvis:
   latitude/longitude`, a London placeholder while the clock says America/Winnipeg:
   set them, in the console under Settings > House). The zone-default radius is 25 km.
2. **Pick the Geofabrik extract** that contains the region, from
   <https://download.geofabrik.de/> (a US state is 20 MB (Delaware) to roughly 1 GB
   (California, Texas); a bbox that crosses a state line needs the larger region).
   Set `MAPS_PBF_URL=https://download.geofabrik.de/.../<region>-latest.osm.pbf`.
3. **Run the script** from the repo root (it reads the same variables):

   ```bash
   scripts/map-region.sh --dry-run      # the plan, with sizes; writes nothing
   scripts/map-region.sh                # fetch it
   ```

   It writes `aura-core/maps-data/` (gitignored). A second run with the same bbox
   downloads nothing; a different bbox refetches; `--force` refetches. It needs
   `curl`, `python3` with `pip` (it installs `osmium` and `overturemaps` under
   `maps-data/tools`, once) and downloads the `pmtiles` binary there. Refuses an
   extract over `MAPS_MAX_GB` (20).
4. **Start routing** (first start builds the routing tiles: minutes for a state, an
   hour or more for a country; `docker compose ps` shows `starting` until then):

   ```bash
   docker compose -f aura-core/docker-compose.yml --profile maps up -d valhalla
   ```

5. **Places** come with step 3: `osm-places.geojsonl` (named shops, amenities,
   offices, tourism and addressed buildings from the extract, `scripts/osm_places.py`)
   is what `place_search` and `/api/maps/places` read. `osm-roads.geojsonl` (every
   street, driveway, path and track, `scripts/osm_roads.py`) is the car's road
   network; a region fetched before T-0980 gets it by running the script again.
6. **Geocoding** (optional, and the large one): `PHOTON_REGION=us` (a country code)
   then `docker compose --profile geocode up -d photon`. Photon's index is per
   country or the planet, never a state, and the US one is among the largest: check
   `df` before the first start. **Not measured on this host.** Gap: without Photon,
   `/api/maps/geocode` is a `502` and the car cannot take an address; routing by
   coordinates still works.
7. `docker compose up -d jarvis-core` (to mount `maps-data` and read the settings).

`make up` is unchanged: Valhalla and Photon only start under their profiles.

## Sizes

Measured here, 2026-11-08, bbox `-75.54,39.15,-75.50,39.18` (Dover, Delaware, about 3 by 3.4 km),
`MAPS_MAX_GB=1`:

| Piece | Size |
| --- | --- |
| `region.pmtiles` (z0 to z15) | 1.5 MB (28 range requests; the planet file is 133 GB) |
| Geofabrik Delaware extract | 22 MB |
| `osm-buildings.geojsonl` | 0.7 MB, 2041 buildings |
| `overture-buildings.geojsonl` | 4.6 MB, 5642 buildings (Overture includes OSM-sourced ones; jarvis-core drops those that overlap an OSM footprint) |
| `pmtiles` binary | 17 MB download |

Tiles scale with area, so a 25 km radius is a few hundred MB at z15; a whole state is
a few GB. Lower `MAPS_MAXZOOM` (default 15) to trade detail for size.

Live check, 2026-11-08: Valhalla 3.9.1 built the Delaware extract into routing tiles
and answered `/route` (maneuvers with `street_names`, `begin_shape_index`) and
`/trace_attributes` with `shape_match: map_snap` (`matched_points`, `edges[].names`) in
the shapes `aura/integrations/maps` reads. The unit tests use fakes of those shapes.

## Regions for a trip (T-0979)

The home region is what `map-region.sh` fetched. Any other region (a state, a country) is
one the owner asks for, and the house keeps it as a **pack**: one SQLite file,
`maps-data/packs/<id>.pack`, holding the region's footprints, roads and places indexed by
0.01 degree cell (`aura/integrations/maps/pack.py`; the format is pinned by
`tests/contracts/map-pack.json`, which the phone's reader binds to as well). `/api/maps/buildings`
answers from the packs that cover the bbox and falls back to the home lists when none does.
A read that must drop features at a cap (5000, or the phone's memory guard) keeps the nearest
to the box's centre, not the oldest ids (T-1198; the `capped` case in the same contract).

A pack with roads also carries a **coverage grid** (T-1048; meta key `coverage`, the `coverage`
case in the same contract): one bit per 0.01 degree cell of its bbox, set when a road's own box
touches the cell or one of its eight neighbours, base64, none over 4,000,000 cells. The phone's
`covers()` answers from it, so a drive's end in a hole in the extract (inside the bbox, with
no road near) is refused and the car asks the house for the route, not a silent detour. A pack
with no row, or one that is not valid base64 or the right size, answers by its bbox as before.

A pack also says how the street looks (T-1066): a building's wall and roof colour (the
mapper's word, or a usual colour for its material), roof shape, kind, and where a
`building:part` starts; a road's lanes; and the details beside it as kind `d`. The format
is still 1, so a phone holding an older pack keeps reading it and an older app skips what
it does not know. A region packed before this has none of it: run `scripts/map-region.sh`
again for home (a list older than the script that writes it is read again; nothing is
downloaded), and remove and re-add any other region. OSM holds the trees and colours
someone mapped, which in most towns is a fraction of them.

A building holding a named business carries its name and trade (T-1106) as `sign` and
`trade`: from the building's own tags (`name` or `brand` with `shop` or `amenity`), else from
a named place inside its outline, else from one within 15 m of its wall (the nearest
building takes it). One sign a building: a shop before an office, then the nearest. A name
longer than 32 characters is not a sign. Logos are not carried: the car writes the name.

The home pack also holds the ground's height (T-1105), as kind `g`: one row per 0.01 degree
cell, 17 by 17 whole metres above sea level. `map-region.sh` samples them
(`scripts/osm_ground.py`) from public 1 degree elevation tiles (the keyless
`elevation-tiles-prod` "skadi" set, about 30 m between samples), downloaded once into
`maps-data/terrain/`, and `/api/maps/buildings` answers with the cells its box reaches as
`ground: [{cell: [cx, cy], n, h}]`. A run that cannot fetch a tile says so and builds the
pack flat; the next run tries again. Not covered: regions the worker fetches (they are flat:
a state is dozens of tiles and a gigabyte of rows at this grain), anything finer than the
source (a cutting, a bridge deck), and the request for a tile names its 1 degree square to
the bucket's host.

Since T-1167 a road also carries its route number and whether it is a bridge: `ref` (the
way's `ref`, at most 40 characters, several joined by `;`), `bridge` and `tunnel` (true when
tagged), and `layer` (-5 to 5). They are optional road fields in the pack and in
`/api/maps/buildings`; the format is still 1 and a pack without them draws as before.
Rebuild home (`MAPS_BBOX="$(cat aura-core/maps-data/region.bbox)" bash scripts/map-region.sh`,
then `docker compose up -d --build jarvis-core`: the wrapper skips a region it has fetched,
T-1169) and re-add other regions to get them. On the car a numbered street says the first of
its numbers on a plate beside it about every 100 m (not on a bridge), a named street has its
name blade wherever it meets another street (twelve at most, nearest first), and a bridge is
drawn as a deck 5.5 m a layer above the line between the ground at its two ends, with rails,
a shaded side and columns; the car on a bridge rides its deck. The deck is level, bridge
ways that share an end are one deck, and the roads at its ends climb to it at 1 in 20 at
most, walked along the roads that join there (T-1181). Not covered: a deck over steep ground
is lifted till it clears the ground under it, so its approaches grow long; a deck none of
whose points is near the car is not planned; a lamp or
sign on a bridge stands on the ground under it, a car on the ground under a bridge that runs
within 45 degrees of its heading is taken to be on it, and overhead gantry signs are not drawn.

A tunnel (T-1184) is not drawn on the surface from outside: its road, paint and pavement are
left out, and a portal (a wall with a dark opening, 8 at most, nearest first) stands at each
end of a run of tunnel ways. With the car on a tunnel its road, walls, roof and lights are
drawn within 200 m, the roof only ahead of the car (the camera is above it), and the
buildings over the tunnel and everything standing outside (trees, lamps, signs, bridges) are
left out. Not covered: the tunnel is not lowered (the painter has no depth), the whole tunnel
within 200 m shows once the car is on any piece of it, and a road crossing over a tunnel is
drawn through the tube. A way tagged `tunnel=building_passage` or `covered` has nothing to go
under and is a road: `osm_roads.py` leaves `tunnel` off it, so a pack built before T-1184
still draws a passage as a tunnel until it is rebuilt.

To look at the map against the world, `scripts/verify/street_compare.py POINTS OUT` puts an
open street-level photo (Panoramax) beside the drive map drawn at the same spot and heading
for each `lat,lon` line of POINTS, and writes `OUT/index.html` (T-1180). The points file names
places: keep it out of the repository. Not covered: the map is drawn from the chase camera,
not the photo's eye height, and nothing judges the pairs; a person looks.

A road a car may not pass (T-1049) carries `access` (`private` or `no`) in the pack and in the
house's answer. `osm_roads.py` sets it from the first of `motorcar`, `motor_vehicle`, `vehicle`,
`access` the way has, so `motor_vehicle=yes` opens an `access=no` way and `destination`,
`customers` or `delivery` are not shut. The ground draws it as before. The phone's pack router
leaves it out of the search unless an end of the trip is on it (nearer than every open road), so a
drive to a door up a private drive is routed and no trip is cut through one; where it is the only
way the router answers nothing and the house is asked. Not covered: a destination in a compound
of several private roads, where only the roads nearest the end are opened; a pack built before
T-1049 has no mark and routes as it did until it is rebuilt; the house's own router (Valhalla) is
not changed here.

Since T-1073 the details also name signs and cameras, as points: `stop`, `give_way`,
`speed_sign` (a limit; its number is not kept), `sign` (any other `traffic_sign`),
`speed_camera`, `camera` (`man_made=surveillance` outdoors) and `alpr` (a plate reader:
`surveillance:type` says ALPR, or its maker, brand or operator is Flock). The same rule
applies: rebuild home, re-add other regions. A camera is where someone mapped one.

Since T-1104 a sign keeps what it says, as `text` on its detail (at most 24 characters): a
`speed_sign`'s number (the node's `maxspeed`, else the `[n]` or `=n` in its `traffic_sign`)
and a `sign`'s `inscription`, `destination` or `name`. Two more points, `bollard`
(`barrier=bollard|block`) and `gate` (`barrier=gate|lift_gate|swing_gate`), and one more
line, `barrier` (`barrier=guard_rail|jersey_barrier`). A road in the answer says `oneway`
(1 or -1) when it is one. Rebuild home and re-add other regions to get them. A `signal` is
where one stands: nothing says what it shows at this moment, so the car draws its three
lenses unlit.

Since T-1235 a road also carries what each of its lanes may do at its end (`turn:lanes`,
and `turn:lanes:forward` / `:backward` on a two-way way), and the car paints an arrow per
lane, left, through, right and their pairs, 5 m before the end of the way the lanes lead to.
The list is lanes left to right in the way's direction, `|` between lanes and `;` between
one lane's values; it is kept whole or not at all (80 characters, 8 lanes), because a cut
list puts an arrow in the wrong lane, and it is drawn only where the road's `lanes` count
fits it (all of them on a one-way road, half on a two-way one). A way's own `turn:lanes` is
the one-way road's; `:forward` is for the line's end and `:backward` for its start. A pack
built before T-1235 has no lists, so its roads show no arrows until the region is rebuilt.

Since T-1122 the pack also holds limit plates nobody mapped. OpenStreetMap maps few limit
signs, so as it is built a pack writes a `speed_sign` wherever two posted roads meet end to
end with different limits (compared in km/h): the road's number as posted, 8 m into the
road and right of someone driving in, for each way it may be entered (a one-way street
only from its start). A mapped sign within 30 m that says the same, or was read with no
number, is believed and not doubled. Not derived: a plate where a side street meets the
middle of another road, where only one of the two roads posts a limit, repeats along a
long road. The plate is on the right also where traffic keeps left. Where three posted
roads meet and one differs, each gets a plate, so going straight on between the two alike
passes one that repeats the limit. Derived plates are the last details in a pack: an ask
whose box holds more details than its limit loses them first. Rebuild home and re-add
other regions to get them; the phone draws them as it does mapped ones.

The car lights what it draws by the sun (T-1107), with nothing from the house: the phone
works out the sun's height and bearing from its own clock (UTC) and the car's position once a
minute (`HoloLight`). In daylight the surfaces are brighter under a blue sky and the walls
facing the sun are the lit ones; with the sun on the horizon the horizon is warm; once it is
6 degrees under (the end of civil twilight) the map is its night look, each mapped `lamp`
throws its pool on the road and some windows are lit. Since T-1164 buildings within 160 m
and trees throw a shadow away from the sun (longer as it sinks, at most three times their
height and 60 m, none with the sun under 2 degrees), a near wall is darker where it meets the
ground, and after dark the car's own lights fan out on the road ahead. These are drawn
shapes, not traced light: a shadow does not darken a wall it falls on, a part of a building
that starts above the ground throws none of its own, and the beam does not light what stands
in it. Not covered: weather, the
moon, and a phone with the wrong time (it gets the wrong sky). A theme's own
sky (the space drive's stars) is the night sky: daylight is laid over it.

## Detail everywhere, in bounded space (T-1074)

The owner, 2026-11-11: "how can we have that data for all of those things everywhere? not
just around me? and prevent it from eating up too much space?" Three parts:

- **The house fetches where the car is.** A `/api/maps/buildings` answer that is empty for a
  place no pack covers says `covered: false`, and the house asks its worker for the smallest
  region round it (what `cover` does for a route's end). `maps: auto_cover: false` in
  `configuration.yaml` turns that off; the privacy switch (`privacy.quiet_house`) stops it
  too. Geofabrik hears the region's name (a state), never the place. The region is there
  minutes later (a download and a build), not on the first pass through.
- **The house makes room.** A region fetched that way has a `<id>.auto` beside its ask and
  `auto: true` in the list. When `MAPS_MAX_GB` is reached the worker drops the oldest-built
  of them (pack, extract, routing) until the new region fits, and fails with "remove a region
  first" only when none is left to drop. Home and a region asked for by name (the console,
  `map_region_download`) are never dropped; asking for a self-fetched region by name makes it
  the owner's. A region the owner removes (`DELETE /api/maps/regions/{id}`) leaves a
  `<id>.removed` marker in the jobs directory that the worker never reads: while it is there a
  drive that ends in the region asks for nothing (`cover` answers `removed: true`), and asking
  for the region by name deletes it (T-1019). Delete the file by hand to let drives fetch it
  again without an ask. At most three self-fetched regions wait on the worker at once. It drops by
  age of build, not by last use, and makes room for a full `MAPS_REGION_MAX_GB`: a cap that
  fits one region only will drop the one the car just left and build it again on the way
  back, restarting routing each time, so keep `MAPS_MAX_GB` several regions wide.
- **The phone keeps tiles, not states.** Off its packs the ground is drawn from tiles of
  0.01 degrees (a pack's cell), each the buildings answer for that box, asked for once and
  kept gzipped in the app's cache (`MapTiles.kt`): at most 1 GB, the tiles used longest ago
  deleted first, nothing written with under 512 MB free, a tile asked for again after 30
  days. An uncovered answer is never kept. When a route is planned its tiles are fetched in
  the order they are driven, one at a time, up to 600 (about 150 km). Tile calls go two at a
  time on their own queue, and the adapter gzips the answer. The phone copies whole packs for
  home and regions asked for by name; self-fetched ones only while they total 1 GB, newest
  first (`MapPackSync.AUTO_BYTES`), the rest dropped from the phone. A ready region's row in
  `GET /api/maps/regions` carries the `sha256` of its pack file (T-1024; hashed once per size
  and mtime, not per call); the phone hashes what it copied and installs it only when that
  matches, otherwise keeps the old pack and drops the copy. A house that sends no `sha256`
  (one older than this) gets no packs copied until it is updated.

Not covered: the first drive into a region with no signal, or before the house has built it,
is plain ground; a region larger than `MAPS_REGION_MAX_GB` is never built; turn-by-turn
with no connection needs the region's pack on the phone, which a self-fetched region past
the budget does not have; the cache size is a constant, not a setting; a smaller pack format
and a whole country built ahead are T-1076.

| Call (house and adapter) | Does |
| --- | --- |
| `GET /api/maps/regions` | the regions held or asked for: `{id, name, state, auto, note, bytes, built}` (`auto`: the house fetched it on its own, T-1074); `state` is queued, working, ready, failed or removing |
| `GET /api/maps/catalogue?q=` | Geofabrik's regions whose name matches, smallest first: `{ready, results: [{id, name, parent, bbox}]}` |
| `POST /api/maps/regions` `{id}` | ask for a region; `DELETE /api/maps/regions/{id}` removes it (never `home`) |
| `POST /api/maps/cover` `{lat, lon}` | ask for the smallest region round a point no pack covers (what `car_navigate` calls) |
| `GET /api/maps/packs/{id}` | the pack file; supports `Range` (the phone copies it on an unmetered network). Answers `ETag: "<sha256>"` (the list's `sha256`), `304` to a matching `If-None-Match`, and a `Range` with an `If-Range` resumes only while the tag still matches; a pack rebuilt since is sent whole (T-1019) |

The house only writes an ask (`config/maps-jobs/<id>.want`); the **`maps-worker`** service
(`--profile maps`, `scripts/map_worker.py`) carries it out: Geofabrik's extract into
`maps-data/regions/`, the four lists read out of it, the pack built, and the extract linked
into `maps-data/valhalla/` with a `.rebuild` flag. Valhalla's wrapper entrypoint exits on that
flag, restarts, and builds tiles over every extract there, so routing covers the region too
(routing is down for that build: minutes per state).

```bash
cd aura-core && docker compose --profile maps up -d maps-worker valhalla
docker logs -f maps-worker                      # one line per fault; state is in maps-data/regions/<id>.json
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/api/maps/regions
```

The console has it under Devices, "Offline maps": the list with each region's state and
size, a search of the catalogue with Download, and Remove on a hold (never the home
region). The console relays the same calls for a phone paired to it
(`aura-web/src/lib/server/maps.ts` `regionsTarget`), a pack's bytes as a stream.

AURA has it as `map_regions` (free; with a `query` it searches the catalogue),
`map_region_download` (free) and `map_region_remove` (asked). `maps.auto_fetch`
(`AURA_MAPS_AUTO_FETCH`, on by default) lets `car_navigate` ask for the region an owner's
route ends in; a guest's route never does. Measured here: the home 50 km box (250k
footprints, 113k roads, 77k places) packs in 47 s to 116 MB and answers a 0.01 degree box
in 6 ms; Monaco end to end in 3 s.

The phone keeps its own copy. On an unmetered network `MapPackSync` copies each pack the
house lists (to `<id>.<built>.pack.part`, renamed when whole; a cut copy resumes with
`Range`), and drops one only when a complete list no longer names it. With the house away
the car's ground draws from those packs (`DriveFeed.fromPacks`), the route in progress is
the one `RouteGuide` kept on disk (it survives a restart of the phone), and a reroute or a
new destination is found on the phone by `PackRouter`: A* over the pack's roads, one-way
streets honoured, each end tried on each of the four nearest roads within 150 m and the
quickest of those ways kept (getting to and from the road counts, at a crawl), three
seconds at most for all of it. A house that did not answer is not asked again for a minute. What the
phone's router does not do: traffic, turn restrictions, private-access roads (the pack does
not carry the tag), or a search for a place by name with the house away. It finds no route
when an end is more than 150 m from every road it holds, when the two ends are more than a
tenth of a degree apart (about 11 km north to south: it is for getting back on the way, not
for a trip across the state), or when the read of the area's roads came back short (more
roads than one read returns, more points than one read holds, or a road too big to read:
a road left out may be the way); the
car then shows "Maps unavailable" and asks again every ten seconds, so the house answers as
soon as it is back. A house that answered "no route" is believed: the packs are not tried.
Valhalla refusing the route (`valhalla_refused`, which is what a region over
`MAPS_ROUTE_MAX_GB` with no routing tiles answers) is not that: the packs are tried, the house is asked again next time, and
with no way over the packs either the car says there is no route (the house gives the
same code for "no tiles here" and "no way between these", so the phone cannot tell them apart).

A reroute whose destination is past that tenth of a degree (T-1047: a wrong turn on a long
drive) joins the route the car was following instead of being refused: `PackRouter` routes
from the car to the first vertex of that kept line at least 100 m ahead of where the car
projects onto it (then 600 m and 2.5 km if no road leads to the first), and the answer is that
piece, the rest of the kept line to its end, and the maneuver the kept route held if it is
ahead of the join. It does not cover: a line with no vertex ahead within a tenth of a degree
of the car (refused, as before), the maneuvers between the join and the end that the kept route
never held (the guide asks again once the car is past the one it has, and joins again), a trip
with no kept route (a first route to a far place is still refused), the console's own page
(`packroute`, which has no kept line), or any check that the kept line is still the good way:
roads closed since are not known to it.

Not covered: a pack outside the home region has OSM footprints only (no Overture heights)
and no street tiles (`tiles.pmtiles` stays the home box); a failed ask is retried hourly.

**A ready region follows its extract (T-1270).** Once a week the worker sends Geofabrik a
HEAD for each ready region's extract and compares the `ETag` and `Last-Modified` the download
left in `regions/<id>.json`; a different one rebuilds the region (one region per look, the
pack in place until the new one replaces it). A look or a rebuild that fails leaves the pack
as it is and is tried again in an hour. A region built before this kept no validators, so its
first look only records them and the rebuild comes a week later at the earliest. Home is the
owner's own file and is not looked at.

**A quiet house holds region asks (T-1270).** With `AURA_QUIET_HOUSE` on, the worker makes
the catalogue's first fetch and nothing else outward: a `present` ask is listed as `held`
("the quiet house is on"), no extract is downloaded, pyosmium is not pip-installed and no
weekly look is made. Turn the switch off and the ask runs on the next tick. A removal is
local work and still runs.

Sizes: `MAPS_MAX_GB` (20) is everything the maps hold (extracts, packs and Valhalla's
tiles), so a house whose trips fetch their own regions does not fill its disk; past it an
ask fails with "remove a region first". `MAPS_REGION_MAX_GB` (1.25) is one extract: the
readers held every node in memory, measured at 4.5 times the extract (581 MB for a 128 MB
one), and the worker has 6 GB, which is any US state. Since T-1270 an extract over 256 MB
is read with a file-backed node index (`with_locations("sparse_file_array,<data>/tmp/<extract>.nodes")`,
`scripts/osm_nodes.py`), made fresh for each read and removed after it, so the node index is
no longer what sets the cap; a smaller one stays in memory, which is faster. The footprint
and place readers still hold every multipolygon's ways in memory for the area pass, and no
country-sized extract has been read, so for one raise the cap and the worker's `mem_limit`
together and watch the first read. The file is as big as the extract's nodes (about the
extract's size again), counted nowhere in `MAPS_MAX_GB`; a read killed outright leaves it in
`<data>/tmp` until the next read of that extract. `MAPS_ROUTE_MAX_GB` (0.6) is the largest
extract handed to Valhalla, which builds in its own 4 GB; a larger region is packed and
drawn but not routed. A region the worker died building (killed for memory, a reboot) is
listed as failed, not retried on every start. A cut download is resumed for six hours and
started again after that.

## Live roadworks and closures (T-1108)

The owner, 2026-11-12: "traffic data, construction, road barriers". Road authorities publish
what is being worked on today as one open GeoJSON document (WZDx; the US registry of feeds is
on datahub.transportation.gov, "WZDx feed registry", and lists which need a key). To turn it on:

1. Put the feed's `https` address in `aura-core/.env` as `MAPS_WORKZONES_URL=` and
   `make up`. `MAPS_WORKZONES_TTL_S` (600) is how long one read serves.
2. Ask: `curl -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8080/api/maps/live?bbox=W,S,E,N"`
   (a box at most 0.1 degrees across). `source: on` with `as_of` is a feed that reads.

The house reads the whole feed when a car asks and the last read is older than the TTL, keeps
the zones in the map's region (`/api/maps/region`), and answers those whose dates say they
are under way and whose status does not say planned, pending, completed or cancelled. A zone
with every lane closed is a `closure`; anything else is `roadworks`. The fetch is `https`
only, follows no redirect, gives up after 30 s in all (10 s on any one piece) and stops at
20 MB; a failed read is tried again a TTL
later, and the last good one answers for three TTLs and no longer. The car asks with its
first fix, every three minutes, and each 500 m (never inside 15 s), through the adapter or the
console's relay (`tests/contracts/house-maps.json`); it never keeps the answer on disk. It
draws roadworks as the striped boards mapped ones get, and a closure with a barricade across
the road at each end.

Does not cover: how fast traffic is moving (no keyless source has it; a keyed flow API is its
own task), lanes (one closed lane and three both read as works), detours and restrictions (left out), a zone its publisher never
entered or left stale, a feed in another shape (WZDx 4 and CWZ; 3.x reads as empty), and the
words on a barricade. The car shows what the house answered at most three minutes ago.

## The flat map (T-1252, T-1255, T-1272)

The car's map is a flat street map by default: seen from above, the way the car is heading at
the top, the car's arrow in the lower part, the map drawing back as the car goes faster. It
shows land, water and green, building footprints, roads by class (slip roads a class down),
street names along the roads, and the route in the accent. It is drawn by `FlatMap.kt` from the
same lists the 3D drive takes (the pack, the house's near map, the route), so nothing about
the data changes; its colours are the `map` group in `design/tokens.json`.

The 3D drive is paused, not removed (the owner, 13 Nov 2026). The way back is the phone's
Settings → Car → "3D map in the car"; it takes effect on the next frame.

What is ahead (T-1255), each from what the scene already holds, with no new fetch:

- the part of the route already driven is dimmed; the route is asked again at each turn, so
  that is what was driven since the last ask;
- the next turn carries a bright arrow round its corner until the car is past it;
- a closure from the live list (above) is solid amber on its road and roadworks, live or
  mapped, amber dashes;
- one-way streets carry small arrows (not motorways, trunk roads or their slip roads, which
  only ever go one way, nor driveways);
- a main road (motorway, trunk, primary) carries its number from `ref` on a plate, the first
  of several;
- a building whose trade is fuel, a charger or parking is named by its sign, and a mapped
  car park at least 20 m across carries a `P`; a place's label gives way to a street's;
- in a close view only (the street zoom, not at speed or in an overview), the same goes for
  the shops and services a driver looks for (T-1277): supermarket, convenience, restaurant,
  fast food, cafe, pharmacy, hospital, clinic, doctors, hotel, motel, bank, school and post
  office, named by their sign or a plain word (`CAFE`, `PHARMACY`) when it has none. They are
  placed after the road names, the numbers and the fuel, charging and parking labels, through
  the same gap, at most ten in a view; text only, dim, no icon;
- a route that ends somewhere new, given while the car is standing, is first shown whole
  with north up for 8 s, then the drive comes back; driving off ends it at once, and the
  same route asked again does not show it.

Labels and the first fix (T-1272):

- labels keep their distance on the screen, not on the ground: a name gives way to another
  within what 70 m is in the standing drive (180 m for the same name), and a long road is
  named every 240 m of it, whatever the zoom. Drawn back at speed or in an overview the map
  so carries as many names as the standing drive does, and they are placed again each time
  the view is drawn back or in by a quarter;
- an overview wider than the drive ever is shows main roads only, with their names and
  numbers. A name still needs one mapped piece of road long enough to lie along, so a main
  road mapped in short pieces can go unnamed there;
- a route given before the phone has its first fix is shown whole from where it starts,
  north up and with no car, for as long as there is no fix; the first fix puts the car on
  it and the drive comes back in its own time, once. A route given to a car that is already
  driving still opens no overview: that is the rule above, not a missing fix.

Not drawn on the flat map: footpaths, signs and signals, places' icons, how fast traffic is
moving, and places other than the trades above (the pack's own place rows are not read by the
phone, and an area's name is dropped on the way in). A construction site's area: the house's
live list (`/api/maps/live`) carries each work zone as a line, never a ring, so roadworks are
amber dashes along the road and there is nothing to hatch. The scene holds the map only near
the car, so a long route's overview is its line over bare ground with roads near the car. It
is always the dark look, whatever the hour. Pictures: `car-flat-drive-*.png` and `car-flat-overview-*.png`
(`CarNavGoldenTest`); a real place, without committing it:
`STREET_COMPARE_FLAT=1 STREET_COMPARE_DIR=<folder> gradle :app:testDebugUnitTest --tests '*StreetCompareRenderTest*'`.

## What leaves the house

The owner, 2026-11-08: "I would like it so my location and everything is private and
not making requests out to stuff" (T-0765). With the defaults:

- **Never sent anywhere:** the car's position and the house's. The speed limit is the
  house's map-match (it was Overpass), routes are the house's Valhalla (they were
  `router.project-osrm.org`), places are the house's index (they were Photon's and
  Nominatim's public servers), and the street tiles the car page drew from
  `tile.openstreetmap.org` are gone. `deploy/tests/test_location_egress.py` fails
  a new public map host in any of those paths.
- **The region, once, when it is fetched:** Geofabrik serves the extract (and its
  index, which reveals nothing), Protomaps the tile range reads and Overture the
  building rows, so each sees an area of a state or a few kilometres, not a point,
  and only when `map-region.sh` runs.
- **The work-zone feed, if one is set (T-1108):** its publisher sees the house's address
  read the whole feed, at most once a TTL and only while a car is asking. Nothing about
  where the car is goes with it: the house cuts the feed to the region itself. Unset by
  default, and stopped by the privacy switch.
- **A region asked for (T-0979):** Geofabrik serves that region's extract and, weekly, its
  catalogue; it hears a region's name (a state, a country), never a point. With
  `maps.auto_fetch` on, a route's own region is asked for without a second question, so
  Geofabrik learns which state a trip ends in. The privacy switch (`AURA_QUIET_HOUSE`) stops
  that and the weekly catalogue refresh; a region the owner asks for still downloads.
- **Weather:** Open-Meteo is asked on a point rounded to 0.1 degree (about 11 km);
  there is no private forecast source.
- **Only if switched on:** `PHOTON_PUBLIC=true` sends a place search to Photon and
  Nominatim's public servers, and `OVERPASS_URL` sends "near me" searches to that
  Overpass. Both are off by default.
- **The model:** a car turn's prompt says where the car is ("In the car, at …"), and
  a question that names a place carries it, to whichever model answers: on this host
  the local one, unless a cloud provider is configured. That is T-0767 (everything
  that leaves the house), not this runbook.

`scripts/verify/maps-live.py` checks the live stack: the region is the house's own, a
route, the car's turn-by-turn and a match with a limit come from Valhalla, the place
index answers, the console's `/api/route` answers from the house, and no public place
service is switched on. It prints pass or fail, never a coordinate.

## What it does not cover

- Tiles are the Protomaps *basemap* schema; the car's building heights come from the
  buildings endpoint, not from the tile layer.
- A bbox crossing two Geofabrik regions routes only inside the one extract it was given.
- Overture and Protomaps builds change daily/monthly; rerun with `--force` to refresh.
  Nothing refreshes by itself; the nightly `map-region-from-house.sh --cron` fetches
  a region the house has not fetched yet, it does not refresh one.
- A region learned from drives is the cell the car is driven in most, with the
  default 25 km round it; a long trip outside it routes nothing until `MAPS_BBOX`
  covers it.
- Valhalla tiles are rebuilt only when `valhalla/valhalla_tiles.tar` is absent
  (the script removes it when the bbox changes). They are written by the container's
  user (`AURA_UID`), the same as the host user if set as `.env.example` says.
- Photon (`host` network, existing service) binds as its image does; the stack's
  firewall (`docs/runbooks/operations.md`) is what keeps it off the LAN.
