# Habitat view (agent-4)

An immersive 3D view of what the Pod is watching. You stand in the middle of the habitat and drag to look around, like a 360° video. The habitat (forest, savanna, desert, snow), the weather, and the animals seen all come from one scene JSON.

**Plain HTML + plain JavaScript, no libraries, no internet, no build step.** It draws with the browser's built-in 2D canvas (no WebGL/GPU needed). Trees, rocks, hills and animals are flat pictures placed around you in 3D and drawn far-to-near with perspective and fog. If frames are slow, it lowers its own resolution.

## Run

From the repo root:

```
python3 -m http.server 8080 --directory ground/dashboard/sim
```

Open http://localhost:8080. You can also just double-click `index.html`: it then uses a built-in copy of the fake scene, because browsers block reading the JSON from disk.

URL options, for testing or the demo:

| Param | Example | Effect |
|-------|---------|--------|
| `biome` | `?biome=desert` | forest, savanna, desert, snow |
| `weather` | `?weather=storm` | clear, cloudy, rain, storm, snow, fog |
| `hour` | `?hour=19.5` | time of day (sunset, night) |
| `scene` | `?scene=/api/scene` | where to load the scene JSON from |
| `scale` | `?scale=0.5` | fixed render resolution (lower = faster on weak devices) |
| `api` | `?api=http://localhost:8000` | use agent-3's ground API for the scene (`/api/scene`) and chart history (`/api/events`) |
| `charts` | `?charts=1` | open the charts panel on load |
| `refresh` | `?refresh=2` | seconds between data reloads (default 30) |

The panel in the bottom right does the same live. It is temporary until the nav bar exists.

## Charts panel

The **Charts** button (top right) opens a panel about the animals seen in this area:

- **Appearances over time:** date/time on the x axis, number of appearances per time period on the y axis, one line per species (top 4 + "other"). Hover for exact numbers; tick **Table** for the same data as a table.
- **Time of day:** when each species tends to show up (hour × species).
- **Most seen:** totals for the selected range.
- Headline numbers: appearances, species, most seen, last seen.

Ranges: last hour, 24 h, 7 days, 30 days, all. History comes from `?history=URL`, else the ground API (`?api=`), else `live_history.json` from the laptop scanner (with `?scene=live_scene.json`), else **generated fake history**, labelled as fake in the panel. Plain JS + SVG, no libraries; colours from a colour-blind-checked palette.

## On the QNX Pi

Files are just HTML/JS, so the Pi can **serve** them with its Python (no install):

```
cd ~/repos/HackWestern13 && python3 -m http.server 8080 --directory ground/dashboard/sim
```

Then open `http://qnxpi.local:8080` on any laptop, tablet or phone on the same network.

**Showing it on a monitor plugged into the Pi** needs a web browser on the QNX image. That is not confirmed yet (check with `ls /usr/bin /system/bin 2>/dev/null | grep -iE 'browser|chrom|web'`). The page only uses features from about 2017-era browsers and needs no GPU, so any browser that exists should cope; use `?scale=0.5` if it is slow.

Keep in mind: the Pi's main job is real-time capture and inference. Drawing the view on the Pi takes CPU from that and would muddy the latency numbers. Serving the files costs almost nothing; drawing is better done on the viewing device.

## Scene JSON (proposed; FAKE for now)

`fake_scene.json`:

```
{ "pod_id": "pod-01",
  "biome": "forest",            "biome_source": "fake",
  "weather": { "condition": "rain", "temp_c": 11, "wind_kmh": 18, "hour": 14, "source": "fake" },
  "animals": [ { "species": "deer", "count": 3, "image": "images/deer.png", "faces": "right" } ] }
```

- `hour` is optional. If it is missing, the view uses the laptop clock.
- `image` is optional. Without it, a cartoon plushie placeholder is drawn. With it, put a **transparent PNG** of the plushie in `images/`. `faces` is the way the animal looks in the photo, so it walks the right way.
- The page reloads the JSON every 30 s, so live weather changes show up on their own.

## Where the real data will come from (not built yet, other lanes)

| Field | Source | Owner |
|-------|--------|-------|
| `animals` | counts of today's visit events (`GET /api/summary`) | agent-3 |
| `weather` | SDR on the laptop (`rtl_433` local weather stations) or Environment Canada; mapped to a `condition` | needs an owner |
| `biome` | The current Pod model detects animals, not habitats. Either a scene classifier (agent-2) or a per-Pod setting | needs a decision |

Once agent-3 serves this shape at e.g. `GET /api/scene`, load the page with `?scene=http://<laptop>:8000/api/scene`. The API will need CORS, or the page served by the API.
