# Quick Start — Easy Mode (Noob Friendly)

Get **FastH3 video generation with audio** running on your GPU instance in two
terminals. No systemd, no Docker, no advanced setup.

> **Rented a Hyper.ai instance?** You log in as `root` in `/hyperai/home`, and
> `~` means `/root` — not where you land. That one detail breaks most generic
> guides. Follow this page for the two-terminal workflow, then use
> [`docs/hyperai-deployment.md`](hyperai-deployment.md) for the automated
> install, autostart across instance restarts, and public-IP exposure.

**What you end up doing, every day:**

| Terminal | Command you run | What it is |
|---|---|---|
| **1** | start the AI engine | ComfyUI, headless (no browser needed) |
| **2** | start the API server | the thing you send video requests to |

That's the whole thing. Both terminals must stay **open** while you work — closing
a terminal stops that server.

**What you need**

- A cloud GPU instance (24 GB VRAM or more) with the **NVIDIA driver already working** (`nvidia-smi` prints your GPU)
- Ubuntu 22.04 or 24.04
- About **250 GB free disk** (the models are big)
- Internet connection (to download the models)

> There are two paths below. **Path A** is one command that sets everything up
> for you. **Path B** is the same thing done by hand, step by step. Pick one —
> you can always start the servers the two-terminal way from either path.

---

## Path A — One command does everything

This assumes the project folder is already on the server (Path B Step 1 shows
how). Run it from inside the project folder (`$PROJ`):

```bash
sudo deploy/bare-metal/install.sh my-server.example.com my-secret-key-123 --mode=traditional
```

Pick any name for your server (your real domain if you have one, otherwise the
instance IP) and any password-like string as your API key. On a Hyper.ai
instance there is usually no domain, so pass the **public IP** — that also skips
TLS; `docs/hyperai-deployment.md` has the exact command and how to add HTTPS
afterwards.

That single command installs the engine, the API server, all four AI models,
and a web server, then starts everything in the background.

Check it's alive:

```bash
sudo fasth3-ctl status
```

You should see `running` for **ComfyUI** and **API** (health shows `OK` once the
models have finished loading — the very first start takes a few minutes).

Everyday commands:

```bash
sudo fasth3-ctl start      # start both servers in the background
sudo fasth3-ctl stop       # stop both
sudo fasth3-ctl restart    # restart both
sudo fasth3-ctl status     # are they running?
sudo fasth3-ctl logs api   # watch the API log
```

> Don't have a domain? Use the instance's IP address as the name — TLS/HTTPS is
> skipped and everything still works over plain HTTP for testing.

If you prefer **systemd** (the professional way, auto-restarts on crash, starts
at boot) run the installer without `--mode=traditional` instead. Both ways work;
only one should be running at a time.

**Want the two-terminal experience instead of background services?** Run the
installer with no services started, then use Path B's Terminal 1 / Terminal 2:

```bash
sudo deploy/bare-metal/install.sh my-server.example.com my-secret-key-123 --mode=none
```


---

## Path B — Do it yourself, step by step

Everything below runs as whatever user you log in with. On most cloud VMs that
is `ubuntu` (so add `sudo` where it says so); **on a Hyper.ai instance you are
already `root`**, so `sudo` is optional and `~` is `/root`.

### Step 1 — Get the project files onto the instance

```bash
# On Hyper.ai, land the project in /hyperai/home (your shell's working dir).
cd /hyperai/home            # on other providers: cd ~
git clone https://github.com/osamaj6543/fast-h3-comfy-server.git
export PROJ=/hyperai/home/fast-h3-comfy-server   # other providers: ~/fast-h3-comfy-server
```

`$PROJ` is used by every later step instead of a hardcoded `~` path, because on
some providers those are two different directories.

No git access? Copy the project folder from your computer instead:

```bash
# run this on YOUR computer, not on the server
# Hyper.ai logs in as root — note root@ and the /hyperai/home/ destination:
scp -r ./fast-h3-comfy-server root@<YOUR_SERVER_IP>:/hyperai/home/
```

### Step 2 — Install uv (the Python manager we use)

```bash
export UV_LINK_MODE=copy     # harmless locally, required if /hyperai/home is a network mount
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env      # makes the `uv` command work right now
uv --version                 # should print a version number
```

### Step 3 — Install the AI engine (ComfyUI, headless)

```bash
cd /hyperai/home              # other providers: cd ~
uv venv --python 3.11 .venv                     # create the uv environment
source ~/.venv/bin/activate                     # activate it
uv pip install comfy-cli                        # ComfyUI's official installer
comfy install --nvidia --version 0.36.0 --fast-deps
comfy which                                     # prints where ComfyUI landed
```

`comfy which` prints your **home** directory plus `/ComfyUI` — e.g.
`/home/ubuntu/ComfyUI`, or **`/root/ComfyUI`** when you are logged in as root on
a Hyper.ai instance (where `~` is `/root`, *not* `/hyperai/home`). **Use
whatever path it prints wherever this guide says `~/ComfyUI`.**

> If the install command complains about `--fast-deps`, just run it again
> without that flag: `comfy install --nvidia --version 0.36.0`

### Step 4 — Download the 4 model files (exact names and folders)

Copy-paste this whole block. It creates the folders and downloads each file to
exactly the right place.

```bash
M=~/ComfyUI/models        # Hyper.ai/root: /root/ComfyUI/models — or whatever `comfy which` said
mkdir -p $M/diffusion_models $M/text_encoders $M/vae

# 1. the video/audio model itself (biggest file — takes a while)
curl -L -C - -o $M/diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors \
  https://huggingface.co/FastVideo/FastVideo-FastH3-Comfy/resolve/main/diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors

# 2. the text reader (understands your prompts)
curl -L -C - -o $M/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors \
  https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors

# 3. the picture decoder
curl -L -C - -o $M/vae/minimax_h3_video_vae_int8_convrot.safetensors \
  https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_int8_convrot.safetensors

# 4. the sound decoder (this is what gives you audio)
curl -L -C - -o $M/vae/minimax_h3_audio_vae_fp32.safetensors \
  https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors
```

`-C -` resumes a download that was interrupted — keep it on a slow or metered
rented link, these four files total around 60 GB.

Check they all arrived:

```bash
ls -lh $M/diffusion_models $M/text_encoders $M/vae
```

You should see exactly these four files, in these folders:

```
ComfyUI/models/
├── diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors
├── text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
└── vae/
    ├── minimax_h3_video_vae_int8_convrot.safetensors
    └── minimax_h3_audio_vae_fp32.safetensors
```

> If a download is interrupted, resume it by adding `-C -` to the same command:
> `curl -L -C - -o <the same file path> <the same URL>`
>
> **Why the exact names matter:** the API server looks these names up exactly.
> A renamed or relocated file = "model not found" errors later.

### Step 5 — Set up the API server (one time)

Make sure you're in a clean shell (if you activated an environment in Step 3,
type `deactivate` first).

```bash
cd $PROJ/server
uv venv --python 3.11 .venv                 # separate uv environment for the API
source .venv/bin/activate                   # activate it
uv pip sync requirements.lock               # installs the exact tested versions
```

That's it — no config file needed for local use. (Optional: to require a
password later, put `FASTH3_API_KEYS=my-secret-key-123` in a file called `.env`
in this folder.)

---

## The two terminals

> New terminal? Re-run the `export PROJ=…` line from Step 1 first — exports live
> in one shell, so `$PROJ` is empty until you set it again. (Or paste the full
> path instead; it is short.)

### Terminal 1 — start the engine (leave it running)

Open your first terminal and run:

```bash
cd ~/ComfyUI                # or the path `comfy which` printed
source ~/.venv/bin/activate
python main.py --listen 127.0.0.1 --port 8188 --fast
```

Wait until you see `To see the GUI go to: http://127.0.0.1:8188` — the first
start also loads the models, which takes a couple of minutes. **Leave this
terminal open.** Nothing else happens in it.

### Terminal 2 — start the API server (in a new terminal)

Open a **second** terminal (or a second SSH session) and run:

```bash
cd $PROJ/server
source .venv/bin/activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Wait for `Application startup complete`. **Leave this open too.**

Check both are healthy (from any terminal):

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"ok","comfy_backend":"up","active_jobs":0}
```

`"comfy_backend":"up"` means both halves are talking to each other. 🎉

> Rebooting or closing a terminal? Just run the two commands again.

---

## Make your first video

Same as before, from a third terminal (or Terminal 2 is fine for `curl`):

```bash
# 1) ask for a 2-second video (short = fast)
curl -s -X POST http://127.0.0.1:8000/v1/videos \
  -H "Content-Type: application/json" \
  -d '{"prompt": "integrated_multimodal_description: [Shot 1] A red paper boat drifts across a rain puddle on a city street at dusk, neon reflections shimmering. overall_soundscape: light rain and distant traffic. non_diegetic_music: a soft piano melody.", "duration_seconds": 2, "seed": 1}'
```

You get back something like `{"id":"a1b2c3d4","status":"queued", ...}`.

```bash
# 2) check on it (replace the id) — repeat until status is "succeeded"
curl -s http://127.0.0.1:8000/v1/videos/a1b2c3d4

# 3) when it succeeded, grab the "content" link from that output and download it
curl -L -o myvideo.mp4 "PASTE_THE_CONTENT_LINK_HERE"
```

Expect the first video to be slow (models are warming up) and later ones much
faster. A 2-second clip is the quickest way to test.

Your videos are also saved on disk automatically:

```bash
ls -lh $PROJ/server/data/outputs/
```

**Writing prompts:** describe the scene, then the shots and camera moves, and
always describe the sound (that's what makes the audio match). More examples:
`docs/api-usage.md`.

---

## When something looks wrong

| What you see | What it means | Fix |
|---|---|---|
| `curl` says **connection refused** on 8000 | Terminal 2 isn't running (or crashed) | look at Terminal 2's output and restart the command |
| `/health` shows `"comfy_backend":"down"` | Terminal 1 isn't running yet | wait for "To see the GUI go to…", or restart Terminal 1 |
| **Model not found** / node error in Terminal 1 | a model file is in the wrong folder or has the wrong name | re-check Step 4's folder listing exactly |
| `Address already in use` | that server is already running | use the terminal that's already running it, or stop it with `Ctrl+C` |
| First video takes ages | models loading for the first time | normal; subsequent videos are much faster |
| Out of memory | clip too big for the GPU | use `"megapixels": 0.2` and a shorter `"duration_seconds"` |

---

## Next steps (only when you want them)

- **Make it a real public API** (HTTPS, API keys, auto-restart, run without a terminal): `docs/bare-metal-deployment.md`
- **Rented a Hyper.ai instance?** Provider-specific paths, `root` vs `~`, public-IP exposure, autostart across instance restarts: `docs/hyperai-deployment.md`
- **All the request options and sample prompts** (resolutions, durations, image-to-video): `docs/api-usage.md`
- **Keep both servers running after you close the terminal:**
  ```bash
  nohup ~/.venv/bin/python main.py --listen 127.0.0.1 --port 8188 --fast >~/comfyui.log 2>&1 &
  ```
  …or simply use `sudo fasth3-ctl start` from Path A.

