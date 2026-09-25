# FastH3 Comfy Server

A production-grade, dedicated API server for **AI video generation with
synchronized native audio** — built on the [FastVideo FastH3](https://huggingface.co/FastVideo/FastVideo-FastH3-8-Step-V2)
8-step distilled MiniMax H3 checkpoint, running a **headless ComfyUI** engine
on bare-metal cloud GPU instances. This replaces the ComfyUI default UI with a
professional REST API designed as the backend for your own frontend platform.

> **New here?** Start with **[docs/quickstart.md](docs/quickstart.md)** — a
> beginner-friendly guide that gets the engine running in one terminal and the
> API in another (plus a single command that installs everything).

```
[Your Frontend]  ──HTTPS──▶  [FastH3 API Server (this repo)]
                                   │  job queue · auth · storage · SSE
                                   ▼  HTTP / WebSocket (localhost only)
                             [Headless ComfyUI]  ──▶  NVIDIA GPU
```

## Capabilities

| | |
|---|---|
| Model | `fastvideo_fasth3_8step_v2_pruned_int8_convrot` (DMD2-distilled MiniMax H3) |
| Modes | text-to-video, image-to-video, first/last-frame conditioning |
| Audio | synchronized native audio (audio VAE + `VAEDecodeAudio`) |
| Sampling | `res_multistep` / `simple`, **fixed 8 steps** (do not change) |
| Speedups | VSA sparse attention (`keep_percent` 10 @ 20% start), H3 sigma shift, kitchen attention backend |
| Resolution | 32px grid, up to 1344px, ~0.4 MP budget (server snaps & validates) |
| Duration | 1–10 s, snapped to the model's 17k+5 frame grid @ 24 fps |
| Queue | persistent SQLite store, single-GPU FIFO worker, survives restarts |
| Environments | uv-managed (Python 3.11), lockfile-pinned + hash-verified installs |
| Observability | `/health`, `/metrics` (queue depth + GPU telemetry from ComfyUI), per-job SSE |

## API

All endpoints are versioned under `/v1`. Auth is a single header:
`X-API-Key: <your key>` (configure keys via `FASTH3_API_KEYS`). Interactive
OpenAPI docs are served at `/docs` — handy for frontend development.

### Create a video (text-to-video)

```bash
curl -X POST https://api.example.com/v1/videos \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"prompt": "integrated_multimodal_description: [Shot 1] ...",
       "duration_seconds": 5, "seed": 42, "aspect_ratio": "16:9", "megapixels": 0.4}'
```

Response `202`:

```json
{
  "id": "a1b2c3d4e5f6a7b8",
  "status": "queued",
  "progress": 0.0,
  "effective_frames": 124,
  "effective_width": 832, "effective_height": 480,
  "links": {
    "self": "https://api.example.com/v1/videos/a1b2c3d4e5f6a7b8",
    "events": "https://api.example.com/v1/videos/a1b2c3d4e5f6a7b8/events"
  }
}
```

### Create a video (image-to-video / first+last frame)

```bash
curl -X POST https://api.example.com/v1/videos \
  -H "X-API-Key: $KEY" \
  -F 'request={"prompt": "the barrier arm rises, servo hum", "duration_seconds": 3};type=application/json' \
  -F 'first_frame=@first.png' \
  -F 'last_frame=@last.png'
```

Optional: `"width"`, `"height"` override the image-derived resolution.

### Track, stream, fetch, cancel

| Endpoint | Purpose |
|---|---|
| `GET /v1/videos/{id}` | poll status (`queued → running → succeeded/failed`) |
| `GET /v1/videos/{id}/events` | Server-Sent Events progress stream |
| `GET /v1/videos/{id}/content` | download the MP4 (signed URL from `links.content` — no key needed, TTL-limited) |
| `DELETE /v1/videos/{id}` | cancel (interrupts ComfyUI mid-run) |
| `GET /v1/videos?status=running` | list/filter jobs |
| `GET /v1/models` | model card (constraints, aspect ratios) |
| `GET /health`, `GET /metrics` | load-balancer & ops telemetry |

**Prompting tips** (from the FastH3 docs): describe the whole scene first,
then timed shots with camera moves + the accompanying audio (`dialogue`, SFX,

## Repository layout

```
fast-h3-comfy-server/
├── comfy-workflows/          # official FastH3 template workflows (source of truth)
├── docs/
│   ├── minimax-h3-fastvideo.md    # FastH3 model/workflow documentation
│   ├── bare-metal-deployment.md   # step-by-step GPU instance deployment guide
│   └── api-usage.md               # API guide + sample prompts (T2V & I2V)
├── scripts/
│   └── convert_workflows.py  # flattens subgraph templates -> API-format JSON
├── server/                   # the API server (FastAPI)
│   ├── app/
│   │   ├── main.py           # app factory + lifespan
│   │   ├── config.py         # env-based settings (FASTH3_*)
│   │   ├── schemas.py        # request/response models
│   │   ├── db.py             # persistent SQLite job store
│   │   ├── worker.py         # single-GPU FIFO worker
│   │   ├── security.py       # API keys + HMAC-signed result URLs
│   │   ├── storage.py        # uploads / outputs on disk
│   │   ├── events.py         # SSE hub
│   │   ├── serialize.py      # job -> API JSON
│   │   ├── engine/
│   │   │   ├── comfy_client.py   # headless ComfyUI HTTP/WS client
│   │   │   ├── h3.py             # model constants + resolution/frame-grid math
│   │   │   ├── templates.py      # workflow parameterization
│   │   │   └── workflows/        # flattened API-format templates (generated)
│   │   └── routers/          # videos + system endpoints
│   ├── requirements.txt      # runtime deps (source of truth)
│   ├── requirements-dev.txt  # + pytest (test/CI)
│   ├── requirements.lock     # uv-compiled, pinned + hashed (universal)
│   ├── requirements-dev.lock # uv-compiled, pinned + hashed (universal)
│   └── tests/                # 15 tests: templates, API flow, auth, limits
└── deploy/
    ├── Dockerfile            # uv-based image (lockfile install)
    ├── docker-compose.yml    # API container (+ bring-your-own engine)
    ├── systemd/              # comfyui.service + fasth3-api.service
    ├── bare-metal/install.sh # one-shot GPU instance bootstrap (--mode=…)
    └── traditional/          # non-systemd launcher: run.sh, fasth3-ctl,
                              # test-run-sh.sh (lifecycle smoke test)
```

## Local development

All Python environments are managed by [uv](https://docs.astral.sh/uv/) —
no manual venv/pip steps, and dependencies come from a hashed lockfile:

```bash
cd server
uv venv                                           # uv-managed CPython env
uv pip sync requirements-dev.lock                 # pinned + hashed (dev/test set)
uv run pytest                                     # 15 tests, no GPU needed
uv run uvicorn app.main:app --reload               # serves on :8000
```

If you don't have uv yet: `curl -LsSf https://astral.sh/uv/install.sh | sh`.

Without a ComfyUI backend the API still boots: `/health` reports `degraded`,
jobs are accepted and fail with a clean error — useful for frontend dev.

If you *do* run ComfyUI locally (any machine with the 4 model files), start it
headless: `python main.py --listen 127.0.0.1 --port 8188 --fast`, then set
`FASTH3_COMFY_URL=http://127.0.0.1:8188`.

### Dependency locks

`requirements.txt` / `requirements-dev.txt` are the source of truth;
`requirements.lock` / `requirements-dev.lock` are the pinned, hash-verified
universal resolutions used by every deployment. Regenerate after editing them:

```bash
uv pip compile --universal --python-version 3.11 --generate-hashes \
    requirements.txt -o requirements.lock
uv pip compile --universal --python-version 3.11 --generate-hashes \
    requirements-dev.txt -o requirements-dev.lock
```

Install in production with `uv pip sync <lockfile>` (exact set, verified
hashes) — never `pip install -r requirements.txt`.

### Regenerating templates

After editing `comfy-workflows/*.json` (e.g. bumping template settings), run:

```bash
python scripts/convert_workflows.py
```

It flattens the subgraph-based template into `/prompt` API format, validates
all links, enforces the 8-step schedule, and writes
`server/app/engine/workflows/fasth3_{t2v,i2v}.{api,meta}.json`.

## Bare-metal GPU deployment

Every Python environment (ComfyUI engine + API server) is created and managed by
**uv** — uv installs its own CPython 3.11, so no distro Python packages are
required, and the API is installed from the hashed lockfile for reproducibility.
See `docs/bare-metal-deployment.md` for the full step-by-step guide.

### One-shot (Ubuntu 22.04/24.04, NVIDIA driver preinstalled)

```bash
sudo deploy/bare-metal/install.sh api.example.com your-api-key                  # systemd (default)
sudo deploy/bare-metal/install.sh api.example.com your-api-key --mode=traditional
sudo deploy/bare-metal/install.sh api.example.com your-api-key --mode=none      # install only
```

Installs uv + Python 3.11, ComfyUI (headless, in a uv environment), the 4 FastH3
models, the API server (uv environment from `requirements.lock`), and an nginx
TLS reverse proxy with certbot.

### Supervising the services — two options

Both modes run the same processes; pick what fits the box.

**Option A — systemd** (default; privilege-separated units, auto-restart, boot autostart):

```bash
sudo systemctl status comfyui fasth3-api
sudo systemctl restart fasth3-api      # running jobs auto-requeue
journalctl -u fasth3-api -f
```

**Option B — traditional, no systemd** (containers, WSL, minimal images, or by preference):

```bash
sudo fasth3-ctl start          # engine + API as background processes (nohup + pid files)
sudo fasth3-ctl status         # pids + HTTP health
sudo fasth3-ctl logs api       # tail -f logs
sudo fasth3-ctl restart all
sudo fasth3-ctl stop
```

Under the hood that is `deploy/traditional/run.sh start|stop|restart|status|logs|foreground`
— same privilege separation as the units (`comfy` for the engine, `fasth3` for
the API, separate pid/log dirs), plus a `foreground` mode for interactive/tmux
use. Add `@reboot /usr/local/bin/fasth3-ctl start all` (cron) for boot autostart.

Full walkthrough, switching modes, and a reference table:
`docs/bare-metal-deployment.md`. The launcher ships with a self-contained
lifecycle smoke test: `bash deploy/traditional/test-run-sh.sh`.

### Docker Compose

Compose containersizes the **API server**; the ComfyUI engine stays external
(ComfyUI has no first-party image — bring your own engine container or use the
host install from the bare-metal path):

```bash
cd deploy
echo "FASTH3_API_KEYS=your-api-key" > .env
echo "FASTH3_COMFY_URL=http://host.docker.internal:8188" >> .env   # your engine
docker compose up -d --build
```

The API image is built on the official uv image (`ghcr.io/astral-sh/uv`) and
installs dependencies with `uv pip sync` from the hashed lockfile.

### Models (download into `ComfyUI/models/`)

| File | Directory |
|---|---|
| `fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors` | `diffusion_models/` |
| `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `text_encoders/` |
| `minimax_h3_video_vae_int8_convrot.safetensors` | `vae/` |
| `minimax_h3_audio_vae_fp32.safetensors` | `vae/` |

### Production checklist

- [ ] `FASTH3_API_KEYS` set (auth disabled when empty — dev only)
- [ ] `FASTH3_URL_SIGNING_SECRET` set (32+ random bytes) so signed URLs survive key rotation
- [ ] ComfyUI bound to `127.0.0.1` only; only the API port exposed, behind TLS (nginx config in `deploy/bare-metal/install.sh` is SSE-safe)
- [ ] TLS-restrict CORS origins in `app/main.py` once your frontend domain is known
- [ ] Monitor `/health` (returns 503 when the engine is down) and `/metrics`
- [ ] GPU: ≥48 GB VRAM recommended for the int8 + nvfp4 AWQ FastH3 stack; queue is single-GPU FIFO by design — scale by adding instances behind a load balancer

## Design notes

- **Steps stay at 8.** The distilled checkpoint is trained for exactly 8
  steps; the server enforces this and rejects manual step overrides by design.
- **Determinism:** a given `seed` + inputs reproduces the same video
  (`control_after_generate` is pinned to `fixed` server-side).
- **Why flattened templates?** ComfyUI's `/prompt` endpoint only accepts flat
  API-format JSON; the official templates wrap the pipeline in a subgraph.
  `scripts/convert_workflows.py` bridges that gap generically, so upstream
  template updates are a one-command re-import.
- **Failure semantics:** ComfyUI errors (node validation, OOM, interrupt) are
  captured into the job record; the worker never dies, and jobs interrupted by
  a server restart are automatically requeued.

## Roadmap

- [ ] S3/R2 result storage backend (drop-in via `app/storage.py`)
- [ ] Multi-instance queue (Redis) for >1 GPU per API host
- [ ] Webhook callbacks on job completion (in addition to SSE)
- [ ] Frontend SDK + reference frontend app

`overall_soundscape`, `non_diegetic_music`). With an input image, focus the
prompt on motion and audio. See `docs/minimax-h3-fastvideo.md` and the
[MiniMax H3 prompt guide](https://docs.comfy.org/tutorials/video/minimax/minimax-h3-prompt-guide).
