# Bare-Metal GPU Deployment Guide

Step-by-step production deployment of the **FastH3 Comfy Server** on a bare-metal
cloud GPU instance (Ubuntu 22.04/24.04 + NVIDIA).

> **Short path:** `sudo deploy/bare-metal/install.sh api.example.com your-api-key`
> automates everything in this guide (add `--mode=traditional` to run without
> systemd, or `--mode=none` to install without starting anything). This document
> explains each step so you can audit, adapt, or troubleshoot it — follow it
> manually the first time.

> **Rented a GPU instance (Hyper.ai, RunPod, Lambda, Vast, …)?** This guide
> assumes a plain Ubuntu VM with a `sudo`-capable non-root user and `~` meaning
> the project directory. Hosted images differ — you are usually already `root`,
> `~` is **not** your working directory, the driver is preinstalled, and you have
> an IP instead of a domain. Use
> [`docs/hyperai-deployment.md`](hyperai-deployment.md) instead; it maps every
> step below onto a Hyper.ai instance.

## What you will end up with

```
Internet ──HTTPS──▶ nginx (:443, public) ──▶ FastH3 API (:8000, localhost)
                                               │  persistent SQLite job queue
                                               ▼  HTTP + WebSocket
                                        ComfyUI (:8188, localhost ONLY) ──▶ NVIDIA GPU
```

| Port | Bound to | Purpose |
|---|---|---|
| 443 | public | nginx TLS termination (the ONLY public port) |
| 8000 | 127.0.0.1 | FastH3 API server |
| 8188 | 127.0.0.1 | headless ComfyUI engine |
| 22 | public (restrict by IP) | SSH |

## Python environments: uv

Every Python environment on the box is **created and managed by [uv](https://docs.astral.sh/uv/)** —
no `python -m venv`, no `virtualenv`, no distro Python packages required:

| Environment | Path | Contents |
|---|---|---|
| ComfyUI engine | `/home/comfy/.venv` | `comfy-cli` + ComfyUI's torch/deps |
| API server | `/opt/fasth3/.venv` | FastH3 API deps, synced from `requirements.lock` |

Why uv here: it installs its own CPython (so Ubuntu 24.04's Python 3.12-only
defaults don't matter), resolves/installs an order of magnitude faster than pip,
and the hashed lockfile (`server/requirements.lock`) makes installs byte-for-byte
reproducible. Set `UV_LINK_MODE=copy` on network filesystems (RunPod, NFS) — the
scripts already do this.

## Prerequisites

| Item | Minimum | Recommended |
|---|---|---|
| GPU | 24 GB VRAM (models are int8/nvfp4 quantized) | 48–80 GB VRAM: H100, A100 80 GB, RTX 6000 Ada, L40S |
| RAM | 32 GB | 64 GB |
| Disk (free) | 150 GB | 250 GB (models ≈ 60 GB + outputs) |
| OS | Ubuntu 22.04 or 24.04 LTS | — |
| NVIDIA driver | any version where `nvidia-smi` works (≥ 550 recommended) | — |

Notes:
- You do **not** need to install the CUDA toolkit — PyTorch wheels bundle their own CUDA runtime.
- ComfyUI **0.36.0 or later** is required (FastH3 nodes ship with it).
- The queue is single-GPU FIFO by design. To scale, deploy one instance per
  GPU and load-balance at nginx/DNS — the API is stateless per instance.

## Step 1 — OS packages

```bash
sudo apt-get update
sudo apt-get install -y git curl nginx certbot python3-certbot-nginx
```

No `python3.11` package is installed on purpose: uv provides the interpreter.

## Step 2 — NVIDIA driver

> **Skip this step if `nvidia-smi` already works.** Every major GPU rental
> provider (Hyper.ai, RunPod, Lambda, Vast, Paperspace) ships an image with the
> driver and CUDA-matched PyTorch preinstalled. Installing a distro driver over
> the top of a working one — or one that then wants a `reboot` you cannot
> perform inside a container-style instance — is the single most common way to
> brick a rented box. Change image in the provider's console instead.

```bash
sudo ubuntu-drivers install        # or: sudo apt install nvidia-driver-550-server
sudo reboot
nvidia-smi                          # MUST list your GPU before continuing
```

## Step 3 — uv + Python

```bash
export UV_INSTALL_DIR=/usr/local/bin        # system-wide (else ~/.local/bin)
export UV_LINK_MODE=copy                    # safe on network filesystems
export UV_PYTHON_INSTALL_DIR=/opt/uv/python # SHARED interpreter dir (see note)
curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version

mkdir -p "$UV_PYTHON_INSTALL_DIR"
uv python install 3.11                      # uv-managed CPython, not the distro's
chmod -R a+rX /opt/uv                       # readable by the service users
```

> **Why a shared `UV_PYTHON_INSTALL_DIR`?** By default uv installs interpreters
> under `~/.local/share/uv/python`. Since you install as **root** but the
> services run as **comfy** / **fasth3**, an interpreter under `/root` is not
> readable and their environments fail at startup with `Permission denied`.
> Keeping it in `/opt/uv/python` (world-readable) avoids that entirely. Keep
> these three `UV_*` exports for the rest of this guide.

## Step 4 — ComfyUI engine in a uv environment

```bash
sudo useradd -m -s /bin/bash comfy
sudo -u comfy env UV_LINK_MODE=copy UV_PYTHON_INSTALL_DIR=/opt/uv/python bash -s <<'EOS'
set -euo pipefail
# Reuse the shared interpreter from Step 3 (no per-user Python download).
uv venv --python 3.11 "$HOME/.venv"                 # the ComfyUI runtime env
uv pip install --python "$HOME/.venv" comfy-cli     # official ComfyUI CLI
export VIRTUAL_ENV="$HOME/.venv" PATH="$HOME/.venv/bin:$PATH"
cd "$HOME"
# comfy-cli installs ComfyUI into the active uv environment.
# --fast-deps routes dependency resolution through uv; it falls back to pip
# resolution on comfy-cli builds that don't accept the flag.
comfy install --nvidia --version 0.36.0 --fast-deps \
  || comfy install --nvidia --version 0.36.0
comfy which                                          # -> the ComfyUI workspace path
EOS
```

Verify the engine boots headless (using the uv environment's interpreter):

```bash
sudo -u comfy bash -c 'cd ~/ComfyUI && ~/.venv/bin/python main.py \
    --listen 127.0.0.1 --port 8188 --fast'
# in a second shell:
curl -s http://127.0.0.1:8188/system_stats | head -c 300   # JSON with your GPU
# stop with Ctrl+C
```

> `comfy which` prints the workspace directory — use it to confirm the path is
> `/home/comfy/ComfyUI` (what the systemd unit below expects). `comfy env`
> lists the environments comfy-cli knows about.

## Step 5 — Download the 4 FastH3 models

```bash
M=/home/comfy/ComfyUI/models
dl() { curl -L --retry 5 -o "$2" "$1"; }

sudo -u comfy mkdir -p $M/diffusion_models $M/text_encoders $M/vae

sudo -u comfy dl https://huggingface.co/FastVideo/FastVideo-FastH3-Comfy/resolve/main/diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors \
  $M/diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors
sudo -u comfy dl https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors \
  $M/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
sudo -u comfy dl https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_int8_convrot.safetensors \
  $M/vae/minimax_h3_video_vae_int8_convrot.safetensors
sudo -u comfy dl https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors \
  $M/vae/minimax_h3_audio_vae_fp32.safetensors
```

*(Alternative using the official CLI: `comfy model download --url <URL> --relative-path models/vae`.)*

Final layout:

```
ComfyUI/models/
├── diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors
├── text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
└── vae/
    ├── minimax_h3_video_vae_int8_convrot.safetensors
    └── minimax_h3_audio_vae_fp32.safetensors
```


## Step 6 — ComfyUI as a systemd service (Option A)

> Not using systemd? Skip Steps 6 and 8 and use
> **[Option B — traditional mode](#option-b--running-both-services-without-systemd-traditional-mode)** instead.

```bash
sudo cp deploy/systemd/comfyui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now comfyui.service
sleep 10
curl -s http://127.0.0.1:8188/system_stats >/dev/null && echo "engine OK"
journalctl -u comfyui -n 20 --no-pager    # check for model-loading errors
```

The unit runs `/home/comfy/.venv/bin/python main.py` (the uv environment from
Step 4). The engine must listen on `127.0.0.1:8188` **only** — confirm it is not
publicly reachable: `curl -m 3 http://<public-ip>:8188/` must time out.

## Step 7 — Install the API server (uv environment)

```bash
sudo useradd -r -s /usr/sbin/nologin fasth3
sudo git clone https://github.com/osamaj6543/fast-h3-comfy-server.git /opt/fasth3
cd /opt/fasth3/server

# uv environment + pinned, hash-verified dependency install from the lockfile
sudo uv venv --python 3.11 /opt/fasth3/.venv
sudo uv pip sync --python /opt/fasth3/.venv requirements.lock
# (on-box dev/test env instead: sudo uv pip sync --python /opt/fasth3/.venv requirements-dev.lock)

sudo mkdir -p /var/lib/fasth3
sudo chown -R fasth3:fasth3 /opt/fasth3 /var/lib/fasth3
sudo /opt/fasth3/.venv/bin/python -c "import fastapi, httpx, websockets; print('env OK')"
```

> `uv venv` creates a standard `.venv/` — uv just builds it (and owns the
> Python). Make sure the three `UV_*` variables from Step 3 are still exported,
> so both environments resolve the shared `/opt/uv/python` interpreter; run
> `uv cache clean` on a full disk (cache lives in `~/.cache/uv`).

Create the configuration file `/etc/fasth3/fasth3.env`:

```bash
sudo mkdir -p /etc/fasth3
API_KEY=$(openssl rand -hex 32)      # save this — it is your client key!
sudo tee /etc/fasth3/fasth3.env >/dev/null <<EOF
FASTH3_COMFY_URL=http://127.0.0.1:8188
FASTH3_API_KEYS=$API_KEY
FASTH3_URL_SIGNING_SECRET=$(openssl rand -hex 32)
FASTH3_DATA_DIR=/var/lib/fasth3
FASTH3_MAX_QUEUE_SIZE=64
FASTH3_MAX_ACTIVE_PER_KEY=4
FASTH3_RESULT_URL_TTL_SECONDS=86400
FASTH3_LOG_LEVEL=INFO
EOF
sudo chown fasth3:fasth3 /etc/fasth3/fasth3.env && sudo chmod 600 /etc/fasth3/fasth3.env
```

| Variable | Meaning |
|---|---|
| `FASTH3_API_KEYS` | comma-separated client keys. **Empty = auth disabled (dev only).** |
| `FASTH3_URL_SIGNING_SECRET` | HMAC secret for signed download URLs (set it so URLs survive key rotation) |
| `FASTH3_DATA_DIR` | SQLite job store + uploads + generated MP4s — back this up |
| `FASTH3_MAX_QUEUE_SIZE` | system-wide queued+running cap (429 when full) |
| `FASTH3_MAX_ACTIVE_PER_KEY` | per-client-key concurrent job cap |
| `FASTH3_RESULT_URL_TTL_SECONDS` | lifetime of signed download URLs (default 86400) |
| `FASTH3_POLL_INTERVAL_SECONDS` | worker idle poll interval (default 1.0) |
| `FASTH3_LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

## Step 8 — API server as a systemd service (Option A)

```bash
sudo cp deploy/systemd/fasth3-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fasth3-api.service
sleep 3
curl -s http://127.0.0.1:8000/health
# expect: {"status":"ok","comfy_backend":"up","active_jobs":0}
```

The unit's `ExecStart` is `/opt/fasth3/.venv/bin/python` — the uv environment
from Step 7. It runs the API as the unprivileged `fasth3` user.

## Option B — running both services WITHOUT systemd (traditional mode)

Both services are ordinary processes; systemd only supervises them. On instances
without systemd (containers, WSL, minimal cloud images) or when you prefer plain
background processes, use the bundled launcher instead: `deploy/traditional/run.sh`.

### B1 — Install the launcher

```bash
sudo chmod +x /opt/fasth3/deploy/traditional/run.sh /opt/fasth3/deploy/traditional/fasth3-ctl
sudo install -m 755 /opt/fasth3/deploy/traditional/fasth3-ctl /usr/local/bin/fasth3-ctl
```

`fasth3-ctl` is a thin wrapper that reproduces the **same privilege separation
as the units**: ComfyUI runs as `comfy`, the API as `fasth3`, each with its own
pid/log directory.

### B2 — Start, inspect, stop

```bash
sudo fasth3-ctl start            # engine first, then the API (default: all)
sudo fasth3-ctl status           # pids + HTTP health of both services
sudo fasth3-ctl logs api         # tail -f the API log  (api|comfy|all)
sudo fasth3-ctl restart all
sudo fasth3-ctl stop             # API first, then the engine
```

Layout produced by this mode:

| Item | ComfyUI engine | API server |
|---|---|---|
| User | `comfy` | `fasth3` |
| PID file | `/run/fasth3-comfy/comfyui.pid` | `/run/fasth3-api/fasth3-api.pid` |
| Log file | `/var/log/fasth3-comfy/comfyui.log` | `/var/log/fasth3-api/fasth3-api.log` |

### B3 — Run in the foreground (interactive / dev / tmux)

`run.sh` can also run a service attached to your terminal — ideal inside `tmux`
or `screen`, and Ctrl+C stops it:

```bash
export RUN_DIR=$HOME/.fasth3/run LOG_DIR=$HOME/.fasth3/log   # unprivileged use
sudo -u comfy env COMFY_HOME=/home/comfy/ComfyUI COMFY_PY=/home/comfy/.venv/bin/python \
    /opt/fasth3/deploy/traditional/run.sh foreground
```

### B4 — Auto-start at boot without systemd

```bash
# cron
( sudo crontab -l 2>/dev/null; echo "@reboot /usr/local/bin/fasth3-ctl start all" ) | sudo crontab -
# or, with cloud-init / rc.local:
echo '/usr/local/bin/fasth3-ctl start all' | sudo tee -a /etc/rc.local
```

### B5 — run.sh reference (used by both modes)

```
./run.sh start [api|comfy|all]    background via nohup, pid file in $RUN_DIR
./run.sh stop  [api|comfy|all]    SIGTERM, then SIGKILL after $STOP_TIMEOUT
./run.sh restart [api|comfy|all]
./run.sh status [api|comfy|all]   pids + /health + /system_stats probes
./run.sh logs  [api|comfy|all]    tail -f the log(s)
./run.sh foreground               both services attached to the terminal
```

Overridable variables: `FASTH3_HOME`, `COMFY_HOME`, `API_PY`, `COMFY_PY`,
`API_HOST/API_PORT`, `COMFY_HOST/COMFY_PORT`, `ENV_FILE`, `RUN_DIR`, `LOG_DIR`,
`READY_TIMEOUT`, `STOP_TIMEOUT`. `RUN_DIR`/`LOG_DIR` automatically fall back to
`~/.fasth3/{run,log}` when the system paths aren't writable (non-root use).

### Switching between modes

```bash
# systemd -> traditional
sudo systemctl disable --now comfyui fasth3-api
sudo fasth3-ctl start all

# traditional -> systemd
sudo fasth3-ctl stop
sudo systemctl enable --now comfyui fasth3-api
```

> Never run both at once: the second starter fails to bind ports 8000/8188
> (`address already in use`) — the cache-free check for "which mode is live" is
> `ss -ltnp | grep -E ':(8000|8188)'`.

## Step 9 — TLS with nginx (SSE-safe)

```nginx
# /etc/nginx/sites-available/fasth3
server {
    listen 80;
    server_name api.example.com;
    client_max_body_size 32m;          # image uploads
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_read_timeout 3600s;      # long SSE streams
        proxy_buffering off;            # REQUIRED for SSE
    }
}
```

```bash
sudo ln -sf /etc/nginx/sites-available/fasth3 /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d api.example.com --non-interactive --agree-tos -m admin@example.com
```

## Step 10 — End-to-end verification

```bash
KEY=<the FASTH3_API_KEYS value from step 7>
API=https://api.example.com

# 1) submit a job and capture its id
JOB=$(curl -s -X POST $API/v1/videos -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"integrated_multimodal_description: [Shot 1] A slow dolly push-in on a vintage synth on a desk, warm lamp light. overall_soundscape: soft vinyl crackle and a single low synth pad.","duration_seconds":5}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "job: $JOB"

# 2) watch it progress (Ctrl+C when status is succeeded/failed/cancelled)
watch -n5 "curl -s $API/v1/videos/$JOB -H \"X-API-Key: $KEY\" | python3 -m json.tool"

# 3) download the finished MP4 via the signed URL
curl -s "$API/v1/videos/$JOB" -H "X-API-Key: $KEY" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["links"]["content"])' \
  | xargs -I{} curl -sL -o out.mp4 "{}"
ls -lh out.mp4    # expect a playable MP4 with an audio track
```

Expect a 5-second clip to finish in **a few minutes** on datacenter GPUs
(the 8-step distillation is the point of FastH3); the **first** job after boot
adds ~1–2 minutes of model loading. Watch `journalctl -u comfyui -f` during
the first run.

## Operations

| Task | systemd (Option A) | traditional (Option B) |
|---|---|---|
| API logs | `journalctl -u fasth3-api -f` | `sudo fasth3-ctl logs api` |
| Engine logs | `journalctl -u comfyui -f` | `sudo fasth3-ctl logs comfy` |
| Status | `systemctl status comfyui fasth3-api` | `sudo fasth3-ctl status` |
| Restart API (safe — running jobs auto-requeue) | `sudo systemctl restart fasth3-api` | `sudo fasth3-ctl restart api` |
| Restart engine | `sudo systemctl restart comfyui` | `sudo fasth3-ctl restart comfy` |
| Stop everything | `sudo systemctl stop fasth3-api comfyui` | `sudo fasth3-ctl stop` |
| Boot autostart | `systemctl enable …` (already done) | `@reboot` cron / rc.local (see B4) |

Common tasks (either mode):

| Task | Command |
|---|---|
| Update API code | `cd /opt/fasth3 && sudo git pull && cd server && sudo uv pip sync --python /opt/fasth3/.venv requirements.lock` then restart the API |
| Reinstall/reset the API env | `sudo rm -rf /opt/fasth3/.venv && sudo uv venv --python 3.11 /opt/fasth3/.venv && sudo uv pip sync --python /opt/fasth3/.venv /opt/fasth3/server/requirements.lock` |
| Update ComfyUI (engine) | `sudo -u comfy env UV_LINK_MODE=copy bash -c 'export PATH=$HOME/.venv/bin:$PATH; comfy update'`, then restart the engine |
| Update models | replace the safetensors, then restart the engine |
| Free disk (uv cache) | `sudo uv cache clean` |
| Backups | back up `/var/lib/fasth3` (job store + outputs) |
| Load-balancer probe | `GET /health` (200 = fully healthy, 503 = engine down) |
| Ops telemetry | `GET /metrics` (queue depth + GPU VRAM via the engine) |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/health` → 503 `comfy_backend: down` | ComfyUI not running | `systemctl status comfyui`; check its logs for missing model files |
| Job `failed`: "comfy rejected the workflow … node errors" | model missing / misnamed | compare `ComfyUI/models` layout with Step 4 exactly |
| Job `failed`: "ComfyUI backend is not reachable" | engine down at claim time | restart comfyui; the job can be re-submitted |
| Job `failed`: CUDA out of memory | another process on the GPU, or very large explicit dims | free VRAM (`nvidia-smi`), or lower `megapixels` / duration |
| `429 queue is full` / per-key limit | admission caps hit | raise `FASTH3_MAX_QUEUE_SIZE` / `FASTH3_MAX_ACTIVE_PER_KEY`, or add instances |
| signed `content` URL → 401 | TTL expired (default 24 h) | re-`GET /v1/videos/{id}` for a fresh signed link |
| First request very slow | models load on first job | expected once per comfyui start; keep a warmup job in a cron if needed |
| SSE stream silent for >15 s | keepalive comments | normal — the server sends `: keepalive` comments every 15 s |
| "ws progress unavailable … falling back to polling" in API logs | nginx WS upgrade not proxied | harmless: progress falls back to history polling |

### traditional-mode (Option B) issues

| Symptom | Cause | Fix |
|---|---|---|
| `address already in use` on start | the other mode (or a stale process) owns 8000/8188 | `ss -ltnp \| grep -E ':(8000\|8188)'`; stop the other mode or the stale pid |
| Service missing after SSH session ends | started without `nohup` (e.g. by hand in the shell) | use `fasth3-ctl start` / `run.sh start` (they `nohup`), or `run.sh foreground` inside `tmux` |
| `status` shows the wrong pid or says "not running" | stale pid file from a crash | `stop` (it cleans the pid file), then `start`; pid files live in `/run/fasth3-{api,comfy}/` |
| Nothing in `/var/log/fasth3-*` | log dir not writable by that user | `sudo install -d -o comfy -g comfy /var/log/fasth3-comfy` (same for `fasth3`) |
| `Permission denied` writing a pid file | ran `run.sh` as a different user than the service user | use `fasth3-ctl` (it switches users), or run `run.sh` as that user with matching `RUN_DIR` |
| Lost everything after reboot | no boot autostart configured | add the `@reboot` cron / `rc.local` entry from B4 |
| Unsure which mode is managing the services | both modes can be installed side by side | `systemctl status comfyui; sudo fasth3-ctl status` — only one should show running |

### uv-specific issues

| Symptom | Cause | Fix |
|---|---|---|
| `uv: command not found` | installed to `~/.local/bin` (not on PATH for this shell) | `export PATH="$HOME/.local/bin:$PATH"`, or reinstall with `UV_INSTALL_DIR=/usr/local/bin` |
| `Permission denied` on `/root/.local/share/uv/python/...` at service start | interpreter installed under root's home, service runs as `comfy`/`fasth3` | reinstall the interpreter to a shared dir: `export UV_PYTHON_INSTALL_DIR=/opt/uv/python && uv python install 3.11 && chmod -R a+rX /opt/uv`, then recreate the envs |
| `python3.11: command not found` | you expected a distro Python | uv owns the interpreters: use `uv python install 3.11` and the env's `.venv/bin/python` |
| `uv pip sync` hardlink/link errors (EXDEV, permission denied) | hardlinks across filesystems (RunPod, NFS volumes) | `export UV_LINK_MODE=copy` (the systemd unit and install script already set it) |
| `No module named pip` inside `.venv` | uv environments intentionally ship **no pip** | use `uv pip install --python /opt/fasth3/.venv <pkg>` instead of `pip` |
| `No space left on device` during installs | uv package cache grew | `sudo uv cache clean` |
| `comfy install` rejects `--fast-deps` | older comfy-cli build | rerun without the flag (the install script already falls back automatically) |
| `uv venv` recreates a broken env | interrupted install | `rm -rf /opt/fasth3/.venv && uv venv --python 3.11 /opt/fasth3/.venv && uv pip sync --python /opt/fasth3/.venv requirements.lock` |
| Dependency versions differ between box and image | someone bypassed the lockfile | always install with `uv pip sync <lockfile>` — never `uv pip install -r requirements.txt` in production |

## Security checklist

- [ ] `FASTH3_API_KEYS` set (never deploy with it empty)
- [ ] `FASTH3_URL_SIGNING_SECRET` set to 32+ random bytes
- [ ] Ports 8000 and 8188 bound to `127.0.0.1` only — verify from an external host
- [ ] SSH restricted (key auth, IP allowlist)
- [ ] `/etc/fasth3/fasth3.env` owned by `fasth3:fasth3`, mode 600
- [ ] nginx + certbot TLS active; HTTP redirected
- [ ] CORS origins in `server/app/main.py` restricted to your frontend domain
- [ ] `/var/lib/fasth3` in your backup schedule
- [ ] uv environments owned by the service users (`chown -R fasth3:fasth3 /opt/fasth3/.venv`, `comfy` likewise)

