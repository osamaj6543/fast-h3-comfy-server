# FastH3 API Usage Guide

REST usage of the FastH3 Comfy Server: **text-to-video** and
**image-to-video** (with optional last-frame control), with synchronized
native audio. Every sample below has a different resolution / dimension /
duration variant. Interactive OpenAPI docs are also served at `/docs`.

- **Base URL:** `https://api.example.com` (your nginx front)
- **Auth header:** `X-API-Key: <key>` (all `/v1/*` endpoints)
- **Media types:** `application/json` for text-to-video; `multipart/form-data`
  for image-to-video (a `request` JSON field + image files)

All examples below assume these two shell variables:

```bash
export API=https://api.example.com      # your deployment's public URL
export KEY=<your-api-key>               # from FASTH3_API_KEYS on the server
```

## Job lifecycle

```
POST /v1/videos  ──▶ 202 {status:"queued"}  ──▶ worker claims ──▶ "running"
   │                                                        (SSE progress)
   └── GET /v1/videos/{id}  poll  ──────────────▶ "succeeded" ──▶ links.content (signed MP4)
                                                    or "failed" / "cancelled"
```

### Request fields

| Field | Type | Range / options | Default | Notes |
|---|---|---|---|---|
| `prompt` | string | 1–20000 chars | required | see Prompting Guide below |
| `duration_seconds` | float | 1.0 – 10.0 | 5.0 | snapped to the 17k+5 frame grid @ 24 fps |
| `seed` | int | 0 – 2^53-1 | random | same seed + inputs = same video |
| `aspect_ratio` | string | `1:1` `4:3` `3:4` `3:2` `2:3` `16:9` `9:16` `21:9` `9:21` | `1:1` | T2V only |
| `megapixels` | float | 0.05 – 0.4 | 0.4 | pixel budget (0.4 ≈ H3 native canvas) |
| `width` / `height` | int | 64 – 1344 | — | explicit dims; snapped to 32px grid; override ratio/MP; provide BOTH |
| `first_frame` | file | png / jpg / webp | — | multipart only; makes the job image_to_video |
| `last_frame` | file | png / jpg / webp | — | optional; requires `first_frame` |

### Resolution system (T2V)

The server computes dimensions from `aspect_ratio` × `megapixels`, snaps to the
32px grid, and clamps each dimension to 320–1344. Precomputed results:

| Aspect | 0.4 MP (default) | 0.2 MP (fast draft) |
|---|---|---|
| `1:1` | 640 × 640 | 448 × 448 |
| `16:9` | 832 × 480 | 608 × 320 |
| `9:16` | 480 × 832 | 320 × 608 |
| `4:3` | 736 × 544 | 512 × 384 |
| `3:4` | 544 × 736 | 384 × 512 |
| `3:2` | 768 × 512 | 544 × 352 |
| `2:3` | 512 × 768 | 352 × 544 |
| `21:9` | 960 × 416 | 672 × 320 |
| `9:21` | 416 × 960 | 320 × 672 |

### Duration → frame grid

| `duration_seconds` | 1 | 2 | 3 | 4 | 5 | 6 | 8 | 10 |
|---|---|---|---|---|---|---|---|---|
| effective frames | 39 | 56 | 73 | 107 | 124 | 158 | 192 | 243 |

The response always reports the exact `effective_frames`,
`effective_width`, `effective_height` — use those, not your inputs.

### Response shape (202)

```json
{
  "id": "a1b2c3d4e5f6a7b8", "kind": "text_to_video", "status": "queued",
  "prompt_preview": "integrated_multimodal_description: [Shot 1] …",
  "created_at": "2026-09-25T02:10:00+00:00", "updated_at": "…",
  "duration_seconds": 5.0, "effective_frames": 124,
  "effective_width": 832, "effective_height": 480,
  "seed": 42, "progress": 0.0, "queue_position": 0, "error": null,
  "links": {
    "self":    "https://api.example.com/v1/videos/a1b2c3d4e5f6a7b8",
    "events":  "https://api.example.com/v1/videos/a1b2c3d4e5f6a7b8/events",
    "content": "https://api.example.com/v1/videos/…/content?token=…&expires=…"
  }
}
```

`links.content` appears only once the job `succeeded`; the signed URL needs no
API key and expires after `FASTH3_RESULT_URL_TTL_SECONDS` (default 24 h).

## Text-to-Video — sample requests

### T2V-1 — Square, default budget, 5 s (the "hello world")

```bash
curl -X POST $API/v1/videos -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{
  "prompt": "integrated_multimodal_description: [Shot 1] A slow overhead dolly shot descends onto a marble kitchen counter at dawn. Golden light slants through a window. A chef'\''s hands sprinkle flour across the surface, and the flour dust hangs in the air. [Shot 2] At 00:02.300, the camera cuts to a close-up of sourdough dough being folded over itself, soft focus, steam rising. overall_soundscape: a distant espresso machine hiss, the gentle thud of dough on stone, and morning birdsong outside. non_diegetic_music: none.",
  "duration_seconds": 5,
  "seed": 42
}'
```
→ 640 × 640, 124 frames.

### T2V-2 — 16:9 landscape, cinematic title sequence, 10 s

```json
{
  "prompt": "integrated_multimodal_description: [Shot 1] Extreme wide shot of a lone motorcycle crossing a desert salt flat at dusk, heat shimmer distorting the horizon, the sky burning orange to violet. Bold white typography reading VOL. 1 bursts into the frame behind the rider. [Shot 2] At 00:03.500, the camera cuts to a low tracking shot just above the tarmac, the bike roaring past in slow motion, dust trail billowing. [Shot 3] At 00:07.000, the rider dismounts in silhouette against the setting sun and removes their helmet. overall_soundscape: a sustained wind gust, the high whine of the engine dopplering past, boots crunching on salt. non_diegetic_music: electronic bass music, fast tempo, heavy rhythmic drum machine, aggressive bass drops.",
  "duration_seconds": 10,
  "aspect_ratio": "16:9",
  "megapixels": 0.4,
  "seed": 123456789
}
```
→ 832 × 480, 243 frames (the longest allowed clip).

### T2V-3 — 9:16 vertical, social-media spot, 3 s

```json
{
  "prompt": "integrated_multimodal_description: [Shot 1] A vertical macro shot of iced coffee being poured into a tall glass, ice cubes tumbling in slow motion, condensation beading on the glass, neon cafe lighting reflecting in the surface. The camera tilts up to a smiling barista winking at the lens. overall_soundscape: crisp ice clatter, the glug of the pour, and a bright cafe ambience. non_diegetic_music: upbeat lo-fi with a single bright piano motif.",
  "duration_seconds": 3,
  "aspect_ratio": "9:16",
  "seed": 7
}
```
→ 480 × 832, 73 frames — ideal for a vertical feed.

### T2V-4 — Explicit dimensions, portrait poster, 6 s

Use `width`/`height` when you need an exact canvas (both must be given; the
server snaps them to the 32px grid, 320–1344 each).

```json
{
  "prompt": "integrated_multimodal_description: [Shot 1] A tall vertical shot of a red-crowned crane standing in a misty lake at sunrise, the water like polished glass. The crane slowly spreads its wings and lifts off, sending ripples outward. Large elegant serif typography reading SERENITY fades in along the left edge. overall_soundscape: soft water ripples, one distant crane call, and a faint wind through reeds. non_diegetic_music: a sparse shakuhachi melody.",
  "duration_seconds": 6,
  "width": 768,
  "height": 1344,
  "seed": 20260925
}
```
→ 768 × 1344 (maximum H3 canvas), 158 frames.

### T2V-5 — 21:9 cinema, low-budget draft, 8 s

Halve the megapixels for fast iteration drafts; re-run the winner at 0.4.

```json
{
  "prompt": "integrated_multimodal_description: [Shot 1] An ultrawide shot of a retro-futuristic bullet train gliding across an elevated track above a rain-soaked neon city at night, reflections streaking along its chrome body. [Shot 2] At 00:04.100, the camera whip-pans inside the empty carriage, city lights smearing past the windows. overall_soundscape: the rhythmic clack of rails, muffled station announcements, rain against glass. non_diegetic_music: synthwave with arpeggiating analog leads and a deep pulse.",
  "duration_seconds": 8,
  "aspect_ratio": "21:9",
  "megapixels": 0.2,
  "seed": 99
}
```
→ 672 × 320 draft (960 × 416 at 0.4), 192 frames.


## Image-to-Video — sample requests

I2V uses `multipart/form-data` with a `request` JSON field plus image files.
Resolution is **derived from the input image** (scaled to `megapixels`,
32px grid) unless you pass explicit `width`/`height`. Prefix the prompt with the
`<Picture 1>` reference line — that is the format the FastH3 template uses.

### I2V-1 — First frame only, image-derived resolution, 4 s

```bash
curl -X POST $API/v1/videos -H "X-API-Key: $KEY" \
  -F 'request={
        "prompt": "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\nintegrated_multimodal_description: [Shot 1] The subject remains centered as the camera performs a slow, steady push-in. A gentle breeze lifts the fabric of the clothing, and the background bokeh lights drift softly out of focus. overall_soundscape: a low ambient city hum and a single soft footstep. non_diegetic_music: a minimal piano theme, slow tempo.",
        "duration_seconds": 4,
        "seed": 11
      };type=application/json' \
  -F 'first_frame=@portrait.png'
```
→ resolution from `portrait.png` @ 0.4 MP (e.g. a 1:1 image → 640 × 640), 107 frames.

### I2V-2 — First + last frame, 16:9 morph, 5 s

Generate the motion *between* two keyframes (product turnarounds, before/after
shots, transitions):

```bash
curl -X POST $API/v1/videos -H "X-API-Key: $KEY" \
  -F 'request={
        "prompt": "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced; at 05.000 seconds, <Picture 2> is fully referenced.\nintegrated_multimodal_description: [Shot 1] The door of the matte-black camera body rotates smoothly open along its hinge as the internal lens barrel extends forward. Lighting stays constant, with a soft gradient sweep across the metal. overall_soundscape: a precise mechanical whir and a firm click at the end of the motion. non_diegetic_music: none.",
        "duration_seconds": 5,
        "aspect_ratio": "16:9",
        "seed": 505
      };type=application/json' \
  -F 'first_frame=@product_closed.png' \
  -F 'last_frame=@product_open.png'
```

### I2V-3 — Explicit 768 × 768 canvas, 2 s, fast draft

```bash
curl -X POST $API/v1/videos -H "X-API-Key: $KEY" \
  -F 'request={
        "prompt": "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\nintegrated_multimodal_description: [Shot 1] A subtle handheld push-in on the character\u2019s face; they blink once, and a strand of hair moves across the cheek. Light rain begins, streaking the background glass. overall_soundscape: rain tapping on glass and a distant thunder roll. non_diegetic_music: none.",
        "duration_seconds": 2,
        "width": 768,
        "height": 768,
        "megapixels": 0.2,
        "seed": 77
      };type=application/json' \
  -F 'first_frame=@closeup.png'
```
→ 768 × 768, 56 frames — cheapest possible iteration loop.

### I2V-4 — 9:16 vertical animation, 10 s

```bash
curl -X POST $API/v1/videos -H "X-API-Key: $KEY" \
  -F 'request={
        "prompt": "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.\nintegrated_multimodal_description: [Shot 1] The camera glides upward along the vertical subjects, revealing more of the neon signage above. Sparks drift down through the frame, and steam curls from a vent in the foreground. [Shot 2] At 00:05.200, the camera settles into a static frame as the signage flickers on, then holds. overall_soundscape: electrical buzz from the sign, hissing steam, and wet footsteps. non_diegetic_music: a slow analog synth pad rising into the final beat.",
        "duration_seconds": 10,
        "aspect_ratio": "9:16",
        "megapixels": 0.4,
        "seed": 31337
      };type=application/json' \
  -F 'first_frame=@alley.png'
```

> **Note:** file parts must be real files; `image/png`, `image/jpeg` and
> `image/webp` are accepted (max 32 MB per file by default nginx config).


## Tracking jobs

### Poll (simplest)

```bash
JOB=<job id>
curl -s "$API/v1/videos/$JOB" -H "X-API-Key: $KEY" | python3 -m json.tool
# repeat until status is succeeded | failed | cancelled
```

### Subscribe (SSE, recommended for UIs)

```bash
curl -N "$API/v1/videos/$JOB/events" -H "X-API-Key: $KEY"
# data: {"id":"a1b2…","status":"running","progress":0.5,"status_message":"sampling 4/8",…}
```

```javascript
// Browser / frontend
const es = new EventSource(`/v1/videos/${jobId}/events`);
es.onmessage = (e) => {
  const job = JSON.parse(e.data);
  setProgress(job.progress);
  if (["succeeded", "failed", "cancelled"].includes(job.status)) es.close();
};
// Note: EventSource cannot send headers — proxy it through your own backend,
// or poll GET /v1/videos/{id} from the browser with the key attached.
```

### Python end-to-end client

```python
import httpx, time

API, KEY = "https://api.example.com", "your-key"
c = httpx.Client(base_url=API, headers={"X-API-Key": KEY}, timeout=60)

job = c.post("/v1/videos", json={
    "prompt": "integrated_multimodal_description: [Shot 1] A lighthouse beam sweeps "
              "across a foggy sea at night. overall_soundscape: waves and foghorn.",
    "duration_seconds": 5, "aspect_ratio": "16:9", "seed": 1,
}).json()

while job["status"] in ("queued", "running"):
    time.sleep(3)
    job = c.get(f"/v1/videos/{job['id']}").json()
    print(job["status"], job["progress"])

if job["status"] == "succeeded":
    video = httpx.get(job["links"]["content"])          # signed, no key needed
    open(f"{job['id']}.mp4", "wb").write(video.content)
```

### Cancel / list / discover

```bash
curl -X DELETE "$API/v1/videos/$JOB" -H "X-API-Key: $KEY"      # cancel (interrupts the GPU run)
curl -s "$API/v1/videos?status=succeeded&limit=20" -H "X-API-Key: $KEY"
curl -s "$API/v1/models" -H "X-API-Key: $KEY"                  # capabilities + constraints
curl -s "$API/health"                                          # LB probe (503 = engine down)
curl -s "$API/metrics" -H "X-API-Key: $KEY"                    # queue depth + GPU telemetry
```

## Error handling

| Status | Meaning | Action |
|---|---|---|
| `401` | missing / wrong `X-API-Key` | send the header; keys come from the server operator |
| `404` | unknown job id | check the id |
| `409` | `content` requested before `succeeded` | keep polling / wait for the SSE terminal event |
| `422` | invalid body (bad enum, width without height, >10 s, >0.4 MP) | fix the payload; see the field table |
| `429` | queue full, or per-key concurrent cap reached | back off and retry with jitter; ask ops to raise limits |
| `400` | `last_frame` without `first_frame`, or images sent with a JSON body | send I2V as multipart with a `request` field |
| `503` | `/health` only — the GPU engine is down | retry later; queued jobs are unaffected |
| job `failed` | engine-level error (OOM, missing model) | read `error` on the job; lower `megapixels`/duration and retry |

## Prompting guide (FastH3)

FastH3 responds best to **structured, shot-based** prompts — the same style the
official workflows use:

1. **Whole scene first** — location, subject, lighting, style.
2. **Then timed shots** — `[Shot 1] … [Shot 2] At 00:03.500, …`. Cuts and
   camera moves (`push-in`, `whip-pan`, `truck right`, `tilt up`) belong here.
3. **Audio always** — `overall_soundscape:` (ambience, SFX, dialogue) and
   `non_diegetic_music:` (score). FastH3 generates synchronized native audio,
   so describing it is what makes the output feel finished.
4. **Wrap it** in `integrated_multimodal_description:` if you want to mirror
   the official template format exactly.
5. **I2V:** lead with `For the target video, at 0.00 seconds into the target
   video, <Picture 1> (from [Shot 1]) is fully referenced.` and focus the rest
   on **motion + camera + audio** — the image already carries the look.
6. **Iterate cheap:** draft at `megapixels: 0.2` and 2–3 s, then re-run the
   winning prompt/seed at 0.4 MP and the final duration. Seeds are stable, so
   only the changed parameter moves.

Reference: `docs/minimax-h3-fastvideo.md` and the
[MiniMax H3 prompt guide](https://docs.comfy.org/tutorials/video/minimax/minimax-h3-prompt-guide).

## Quick reference

| Want | Do |
|---|---|
| Fastest draft | `duration_seconds: 2`, `megapixels: 0.2`, `aspect_ratio: "1:1"` |
| Best quality | `duration_seconds: 10`, `megapixels: 0.4`, native canvas dims |
| Vertical / social | `aspect_ratio: "9:16"` (T2V) or a vertical input image (I2V) |
| Widescreen / cinema | `aspect_ratio: "16:9"` or `"21:9"` |
| Exact canvas | `width` + `height` (both, 32px grid, 320–1344) |
| Reproducible output | pin `seed` |
| Image → motion | multipart with `first_frame` |
| Two keyframes | multipart with `first_frame` **and** `last_frame` |
| Live UI progress | `GET /v1/videos/{id}/events` (SSE) |
| Download result | `links.content` (signed URL, TTL default 24 h) |

