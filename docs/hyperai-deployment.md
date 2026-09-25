# Deployment on a Hyper.ai GPU Instance

The provider-agnostic guide lives in
[`docs/bare-metal-deployment.md`](bare-metal-deployment.md). This page is the
Hyper.ai flavour of it: it assumes **you are already logged in as `root`** with
the shell sitting in `/hyperai/home`, which is what a rented Hyper.ai instance
gives you:

```
root@re0nxwofx1zz-0cz0achmtoc4-main:/hyperai/home#
```

New here? [docs/quickstart.md](quickstart.md) is the gentler on-ramp; come back
here when you want it to survive a terminal close and an instance restart.

## The three things that trip people up

Hyper.ai instances are Ubuntu boxes with a preinstalled NVIDIA driver, but they
differ from a generic cloud VM in ways that matter here:

| | Generic Ubuntu VM | Your Hyper.ai instance |
|---|---|---|
| Default user | `ubuntu` (needs `sudo`) | **`root`** — no `sudo` required (harmless if you type it) |
| Where you land | `/home/ubuntu` | **`/hyperai/home`** |
| What `~` means | `/home/ubuntu` | **`/root`** — *not* `/hyperai/home` |
| NVIDIA driver | you install it | **already installed** — never run `ubuntu-drivers` |
| Public DNS name | often you have a domain | usually none: you have an IP and the ports you expose |

> **The `~` trap.** Guides that say "clone into `~`, then `cd
> ~/fast-h3-comfy-server`" silently scatter your project, the ComfyUI engine and
> your Python environments across two different trees (`/root` and
> `/hyperai/home`), and leave you debugging `command not found`. This guide pins
> every path to one variable instead.

```bash
# Set this once per shell session; reuse it from your shell history later:
export PROJ=/hyperai/home/fast-h3-comfy-server
```

## Step 0 — Check what the instance gives you

Run this first. It takes ten seconds and tells you which path below to take.

```bash
nvidia-smi                                   # driver + GPU must work, or the installer stops
df -h / /hyperai/home                       # models need ~60 GB free; outputs grow from there
ps -p 1 -o comm=                            # "systemd" -> Path A default works
                                            # anything else (e.g. "bash") -> use --mode=traditional
ss -ltnp | grep -E ':(8000|8188)' || echo "ports 8000/8188 are free"
```

Notes on the output:

- **`nvidia-smi` failing** is the only genuine blocker. The Hyper.ai image ships
  a driver, so do *not* run `sudo ubuntu-drivers install` or `apt install
  nvidia-driver-*`: that can replace a working driver with one that needs a
  reboot you cannot perform from inside a container-style instance. Pick a
  different image in the Hyper.ai console instead.
- **Free space.** The four model files total roughly 60 GB. Check the right
  filesystem before starting a multi-hour download.
- **No `systemd`?** Some images run a container-style init. Pass
  `--mode=traditional` everywhere below and use `fasth3-ctl` instead of
  `systemctl`.

---

## Path A — One command (recommended)

Get the repo onto the instance first (see [Step 1](#step-1--get-the-repo-onto-the-instance)),
then run the bundled installer from inside it:

```bash
cd $PROJ
sudo deploy/bare-metal/install.sh <YOUR_INSTANCE_IP> your-secret-key-123 --mode=traditional
```

Two arguments, and they are the two things that differ from a normal VM:

1. **`<YOUR_INSTANCE_IP>`** — a Hyper.ai instance has no inbound DNS name
   unless you attach one, so pass the public IP. That makes the installer
   **skip TLS** (certbot cannot issue a certificate for a bare IP) and serve
   plain `http://`, which is fine for a first run — see
   [Exposing it](#exposing-the-api-on-your-public-ip). If you later point a real
   domain at the IP, run certbot by hand and you are back on HTTPS.
2. **`your-secret-key-123`** — this becomes `FASTH3_API_KEYS`, the `X-API-Key`
   your frontend sends. Use something long and random; you can rotate it later in
   `/etc/fasth3/fasth3.env`.

Omit `--mode=traditional` if Step 0 showed real `systemd` and you want the
privilege-separated units (auto-restart on crash, autostart at boot).

Check it came up:

```bash
sudo fasth3-ctl status      # or: systemctl status comfyui fasth3-api
curl -s http://127.0.0.1:8000/health
# {"status":"ok","comfy_backend":"up","active_jobs":0}
```

`"comfy_backend":"up"` means the engine and the API are talking. The **first**
start loads ~60 GB of models and takes several minutes, so `comfy_backend` stays
`down` until it finishes. Watch it with `sudo fasth3-ctl logs comfy`.

Day-to-day:

```bash
sudo fasth3-ctl start | stop | restart | status
sudo fasth3-ctl logs api        # or: logs comfy / logs all
```

### Surviving an instance restart

Rented instances get recycled — a stop/start, a maintenance event or an expiry
can hand you a fresh boot with an empty `/run` and no services. Set up autostart
**once**:

```bash
( sudo crontab -l 2>/dev/null; echo "@reboot /usr/local/bin/fasth3-ctl start all" ) | sudo crontab -
```

Confirm it later with `sudo crontab -l`. If Step 0 showed real `systemd`, the
installer's `systemctl enable` already covers this and you can skip the cron
line. `/hyperai/home` survives a restart, but the system paths the services use
for pid files do not — which is exactly why the `@reboot` entry is needed.

---

## Path B — Two terminals, no installer

Useful when you want to watch every file land, or when reusing an environment
you already built. Remember you are **root**: drop the `sudo` and point `$PROJ`
at `/hyperai/home`.

### Step 1 — Get the repo onto the instance

```bash
cd /hyperai/home
git clone https://github.com/osamaj6543/fast-h3-comfy-server.git
export PROJ=/hyperai/home/fast-h3-comfy-server
```

No git access? Copy it from your machine instead — note `root@` and the
`/hyperai/home/` destination, **not** `~/`:

```bash
# run this on YOUR computer, not on the instance
scp -r ./fast-h3-comfy-server root@<YOUR_INSTANCE_IP>:/hyperai/home/
```

### Step 2 — uv (manages all Python on this box)

```bash
export UV_LINK_MODE=copy          # /hyperai/home may be a network mount; hardlinks fail there
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
uv --version
```

### Step 3 — ComfyUI engine, headless

```bash
cd /hyperai/home
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install comfy-cli
comfy install --nvidia --version 0.36.0 --fast-deps || comfy install --nvidia --version 0.36.0
comfy which
```

As root this prints **`/root/ComfyUI`**, not `/hyperai/home/ComfyUI` — expected,
and fine. Wherever this guide says `~/ComfyUI`, read `/root/ComfyUI` on a
Hyper.ai instance. To keep the engine beside the project on `/hyperai/home`
(useful when that is your large, persistent volume), create the venv there
instead and use whatever path `comfy which` prints.

### Step 4 — The 4 model files

```bash
M=/root/ComfyUI/models        # or $(comfy which)/models if you installed elsewhere
mkdir -p $M/diffusion_models $M/text_encoders $M/vae

curl -L -C - -o $M/diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors \
  https://huggingface.co/FastVideo/FastVideo-FastH3-Comfy/resolve/main/diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors
curl -L -C - -o $M/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors \
  https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
curl -L -C - -o $M/vae/minimax_h3_video_vae_int8_convrot.safetensors \
  https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_int8_convrot.safetensors
curl -L -C - -o $M/vae/minimax_h3_audio_vae_fp32.safetensors \
  https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors

ls -lh $M/diffusion_models $M/text_encoders $M/vae   # all four present?
```

`-C -` resumes an interrupted download — worth keeping for a 60 GB transfer over
a rented link. Exact filenames matter: the API looks them up verbatim.

> Prefer the models on `/hyperai/home` (bigger / persistent volume)? Download
> there and symlink before the first start:
> `ln -s /hyperai/home/ComfyUI/models /root/ComfyUI/models`.

### Step 5 — API server

```bash
cd $PROJ/server
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip sync requirements.lock
```

Keep state on the large volume instead of inside the repo checkout:

```bash
export FASTH3_DATA_DIR=/hyperai/home/fasth3-data   # job store + uploads + MP4s
mkdir -p $FASTH3_DATA_DIR
```

### Terminal 1 — the engine

```bash
cd /root/ComfyUI && source /root/.venv/bin/activate
python main.py --listen 127.0.0.1 --port 8188 --fast
```

### Terminal 2 — the API

```bash
cd $PROJ/server && source .venv/bin/activate
FASTH3_DATA_DIR=/hyperai/home/fasth3-data \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Third terminal — smoke test:

```bash
curl -s http://127.0.0.1:8000/health   # {"status":"ok","comfy_backend":"up",...}
curl -s -X POST http://127.0.0.1:8000/v1/videos -H "Content-Type: application/json" \
  -d '{"prompt": "integrated_multimodal_description: [Shot 1] A red paper boat drifts across a rain puddle at dusk. overall_soundscape: light rain. non_diegetic_music: soft piano.", "duration_seconds": 2, "seed": 1}'
```

More prompts and request options: [docs/api-usage.md](api-usage.md).

---

## Exposing the API on your public IP

Everything above is `127.0.0.1` on purpose. To reach it from outside:

1. **Expose only port 8000** (the API) in the Hyper.ai instance's port settings.
   Never expose 8188 — that is the ComfyUI engine and it has no auth at all.
2. **Terminate TLS in front of it.** nginx is the tidy way; since a bare IP
   cannot get a certificate, use a self-signed one or attach a real domain.
   Skipping TLS entirely is acceptable only while testing, and only because
   every request is authenticated with `X-API-Key`.

```bash
# 1) self-signed cert, when you have no domain (clients need -k or CA pinning)
apt-get install -y nginx openssl
mkdir -p /etc/nginx/ssl
openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout /etc/nginx/ssl/fasth3.key -out /etc/nginx/ssl/fasth3.crt \
  -subj "/CN=<YOUR_INSTANCE_IP>"

# 2) proxy config — proxy_buffering off is mandatory, it is what makes the
#    SSE progress stream work. Same block as docs/bare-metal-deployment.md step 9.
cat > /etc/nginx/sites-available/fasth3 <<'EOF'
server {
    listen 443 ssl;
    ssl_certificate     /etc/nginx/ssl/fasth3.crt;
    ssl_certificate_key /etc/nginx/ssl/fasth3.key;
    client_max_body_size 32m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "";
        proxy_read_timeout 3600s;   # long-running SSE
        proxy_buffering off;        # REQUIRED for SSE
    }
}
EOF
ln -sf /etc/nginx/sites-available/fasth3 /etc/nginx/sites-enabled/fasth3
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx     # use `service nginx restart` with no systemd
```

> With a real domain, skip the self-signed step and run
> `certbot --nginx -d api.example.com --non-interactive --agree-tos -m you@example.com`
> instead.

Then, from your own machine:

```bash
curl -s https://<YOUR_INSTANCE_IP>/health -H "X-API-Key: your-secret-key-123"
```

Hyper.ai terminates and forwards the inbound port itself, so a hairpin request
from inside the instance to its own public IP can hang even when everything is
healthy — test from your laptop, not from the box.

---

## Hyper.ai-specific troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `nvidia-smi` fails or `command not found` | image picked without a driver | switch image in the Hyper.ai console; do **not** `apt install nvidia-driver-*` |
| `uv` / `python` works in one terminal, not the next | `~/.local/bin` not on `PATH` in a fresh shell | `source ~/.local/bin/env`, or install with `export UV_INSTALL_DIR=/usr/local/bin` first |
| `uv pip sync` fails with `EXDEV` / `permission denied` | `/hyperai/home` is a network mount and uv hardlinks by default | `export UV_LINK_MODE=copy` |
| `curl: (7) connection refused` on 8000 | API not started, or died with the terminal | use `fasth3-ctl` / `systemd`, never a bare `&` |
| Services gone after a restart or instance recycle | no autostart configured | add the `@reboot` cron line from [Path A](#surviving-an-instance-restart) |
| `address already in use` on 8000/8188 | installer run twice, or both modes installed | `ss -ltnp \| grep -E ':(8000\|8188)'`, then `fasth3-ctl stop` before restarting |
| `comfy_backend: down` for the first minutes | ~60 GB of models still loading from disk | wait, and watch `fasth3-ctl logs comfy` |
| `No space left on device` | the small root filesystem filled up | `df -h`; move the data dir or the models to `/hyperai/home` |
| Request to your own public IP hangs from inside the box | hairpin routing through the provider's port forward | test from your own machine |
| Public IP unreachable at all | port not exposed in the instance's port settings | add the API port to the instance configuration |
| `git clone` fails | no outbound git access | `scp` the folder to `root@<IP>:/hyperai/home/` instead |
| ComfyUI says "model not found" | models landed in a different `models/` dir than the engine is reading | confirm `comfy which` and the exact filenames |

The provider-agnostic troubleshooting tables (TLS, queue limits, OOM, signed
URLs) are in
[docs/bare-metal-deployment.md](bare-metal-deployment.md#troubleshooting).

## Security checklist for a rented instance

- [ ] `FASTH3_API_KEYS` set to a long random value — never leave it empty
- [ ] `FASTH3_URL_SIGNING_SECRET` set to 32+ random bytes
- [ ] Ports 8000 and 8188 bound to `127.0.0.1`; only 8000 (or 443) exposed
- [ ] TLS terminated (or self-signed) rather than shipping API keys in cleartext
- [ ] SSH: key auth, and a non-default port if the instance allows it
- [ ] `/etc/fasth3/fasth3.env` is mode 600, owned by `fasth3`
- [ ] CORS origins in `server/app/main.py` narrowed to your frontend domain
- [ ] Your data dir (`/var/lib/fasth3`, or `FASTH3_DATA_DIR`) in a backup plan

## Next steps

- Every request option, resolutions, image-to-video, sample prompts:
  [docs/api-usage.md](api-usage.md)
- The model, the 8-step schedule, the memory budget:
  [docs/minimax-h3-fastvideo.md](minimax-h3-fastvideo.md)
- The full provider-agnostic deployment guide (systemd units, TLS with a real
  domain, what `install.sh` does internally):
  [docs/bare-metal-deployment.md](bare-metal-deployment.md)




