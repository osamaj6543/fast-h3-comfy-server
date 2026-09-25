#!/usr/bin/env bash
# Bare-metal cloud GPU instance bootstrap (Ubuntu 22.04/24.04 + NVIDIA driver).
# Run as root. Installs: uv, ComfyUI (headless, uv-managed env + comfy-cli),
# the FastH3 models, the FastH3 API server, and an nginx TLS reverse proxy.
#
# Usage:
#   ./install.sh <fqdn> <api-key> [--mode=systemd|traditional|none]
#
#   <fqdn>     e.g. api.example.com (used for the nginx TLS cert)
#   <api-key>  the single API key to seed FASTH3_API_KEYS with
#
#   --mode=systemd      (default) install + enable systemd units for both
#                       ComfyUI and the API server, privilege-separated
#   --mode=traditional  no systemd: install deploy/traditional/{run.sh,fasth3-ctl}
#                       and start both services as background processes
#                       (nohup + pid files). Useful on instances without
#                       systemd (containers, WSL, minimal images).
#   --mode=none         install everything but start nothing (you drive it)
#
#   Aliases: --traditional == --mode=traditional, --no-systemd == same,
#            --no-services == --mode=none
#
# Everything Python is managed by uv: uv installs its own CPython 3.11, so no
# apt python3.11 / virtualenv tooling is required.
set -euo pipefail

FQDN="${1:?usage: install.sh <fqdn> <api-key> [--mode=systemd|traditional|none]}"
API_KEY="${2:?usage: install.sh <fqdn> <api-key> [--mode=systemd|traditional|none]}"
shift 2

MODE=systemd
for arg in "$@"; do
    case "$arg" in
        --mode=*) MODE="${arg#*=}" ;;
        --traditional|--no-systemd) MODE=traditional ;;
        --no-services) MODE=none ;;
        --systemd) MODE=systemd ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done
case "$MODE" in
    systemd|traditional|none) ;;
    *) echo "invalid --mode: $MODE (use systemd|traditional|none)" >&2; exit 2 ;;
esac

FASTH3_HOME=/opt/fasth3
DATA_DIR=/var/lib/fasth3
PYTHON_VERSION=3.11
TRADITIONAL="$FASTH3_HOME/deploy/traditional/run.sh"

# uv: install system-wide, copy (never hardlink) packages — the safe mode on
# network filesystems (RunPod, NFS-backed volumes) — and keep the managed Python
# in a SHARED location. The service users (comfy, fasth3) must be able to read
# the interpreter; the default (~/.local/share/uv/python) would land in root's
# home and break their environments at runtime.
export UV_INSTALL_DIR=/usr/local/bin
export UV_LINK_MODE=copy
export UV_PYTHON_INSTALL_DIR=/opt/uv/python

echo "==> [1/7] system packages"
apt-get update -y
apt-get install -y git curl nginx certbot python3-certbot-nginx

echo "==> [2/7] verify NVIDIA GPU"
nvidia-smi || { echo "FATAL: install the NVIDIA driver first (nvidia-smi must work)"; exit 1; }

echo "==> [3/7] uv + Python ${PYTHON_VERSION}"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
uv --version
# uv downloads and manages its own CPython — no distro Python needed.
mkdir -p "$UV_PYTHON_INSTALL_DIR"
uv python install "${PYTHON_VERSION}"
chmod -R a+rX /opt/uv    # readable by the comfy/fasth3 service users

echo "==> [4/7] ComfyUI engine in a uv environment (user: comfy)"
id -u comfy &>/dev/null || useradd -m -s /bin/bash comfy
sudo -u comfy env UV_LINK_MODE=copy UV_PYTHON_INSTALL_DIR="$UV_PYTHON_INSTALL_DIR" bash -s <<EOS
set -euo pipefail
# Reuse the shared interpreter installed in step [3/7] (no per-user download).
uv venv --python ${PYTHON_VERSION} "\$HOME/.venv"          # ComfyUI runtime env
uv pip install --python "\$HOME/.venv" comfy-cli           # official CLI
export VIRTUAL_ENV="\$HOME/.venv"
export PATH="\$HOME/.venv/bin:\$PATH"
cd "\$HOME"
# Install ComfyUI into the uv environment above. --fast-deps routes dependency
# resolution through uv (comfy-cli ships/uses uv for this); fall back to plain
# pip resolution if the installed comfy-cli build does not accept the flag.
comfy install --nvidia --version 0.36.0 --fast-deps \
  || comfy install --nvidia --version 0.36.0
comfy which           # confirm where ComfyUI was installed
EOS

echo "==> [5/7] FastH3 models"
MODELS=/home/comfy/ComfyUI/models
dl() { [ -f "$2" ] || curl -L --retry 5 -o "$2" "$1"; }
mkdir -p "$MODELS/diffusion_models" "$MODELS/text_encoders" "$MODELS/vae"
dl https://huggingface.co/FastVideo/FastVideo-FastH3-Comfy/resolve/main/diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors "$MODELS/diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors"
dl https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors "$MODELS/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
dl https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_int8_convrot.safetensors "$MODELS/vae/minimax_h3_video_vae_int8_convrot.safetensors"
dl https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors "$MODELS/vae/minimax_h3_audio_vae_fp32.safetensors"
chown -R comfy:comfy /home/comfy/ComfyUI


echo "==> [6/7] FastH3 API server (uv environment from the hashed lockfile)"
id -u fasth3 &>/dev/null || useradd -r -s /usr/sbin/nologin fasth3
mkdir -p "$DATA_DIR"
git clone https://github.com/osamaj6543/fast-h3-comfy-server.git "$FASTH3_HOME" 2>/dev/null || true
cd "$FASTH3_HOME/server"
uv venv --python "${PYTHON_VERSION}" "$FASTH3_HOME/.venv"
uv pip sync --python "$FASTH3_HOME/.venv" requirements.lock
# On-box dev/test env instead: uv pip sync --python "$FASTH3_HOME/.venv" requirements-dev.lock

mkdir -p /etc/fasth3
cat > /etc/fasth3/fasth3.env <<EOF
FASTH3_COMFY_URL=http://127.0.0.1:8188
FASTH3_API_KEYS=$API_KEY
FASTH3_DATA_DIR=$DATA_DIR
FASTH3_LOG_LEVEL=INFO
EOF
chown -R fasth3:fasth3 "$FASTH3_HOME" "$DATA_DIR" /etc/fasth3
chmod 600 /etc/fasth3/fasth3.env
chmod +x "$TRADITIONAL" "$FASTH3_HOME/deploy/traditional/fasth3-ctl"
install -m 755 "$FASTH3_HOME/deploy/traditional/fasth3-ctl" /usr/local/bin/fasth3-ctl

if [ "$MODE" = "systemd" ]; then
    install -m 644 "$FASTH3_HOME/deploy/systemd/comfyui.service" /etc/systemd/system/
    install -m 644 "$FASTH3_HOME/deploy/systemd/fasth3-api.service" /etc/systemd/system/
fi

echo "==> [7/7] nginx TLS reverse proxy"
cat > /etc/nginx/sites-available/fasth3 <<EOF
server {
    listen 80;
    server_name $FQDN;
    client_max_body_size 32m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "";
        proxy_read_timeout 3600s;   # long-running SSE
        proxy_buffering off;        # required for SSE
    }
}
EOF
ln -sf /etc/nginx/sites-available/fasth3 /etc/nginx/sites-enabled/fasth3
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
certbot --nginx -d "$FQDN" --non-interactive --agree-tos -m "admin@$FQDN" || true

echo "==> [7/7] starting the stack ($MODE mode)"
case "$MODE" in
    systemd)
        systemctl daemon-reload
        systemctl enable --now comfyui.service fasth3-api.service
        sleep 5
        systemctl --no-pager --lines=0 status comfyui.service fasth3-api.service || true
        echo "Manage with: systemctl {status,restart,stop} comfyui fasth3-api"
        ;;
    traditional)
        # Same privilege separation as the units: comfy runs the engine,
        # fasth3 runs the API, each with its own pid/log directory.
        install -d -o comfy -g comfy /run/fasth3-comfy /var/log/fasth3-comfy
        install -d -o fasth3 -g fasth3 /run/fasth3-api /var/log/fasth3-api
        echo "--- starting ComfyUI (user comfy) ---"
        sudo -u comfy env RUN_DIR=/run/fasth3-comfy LOG_DIR=/var/log/fasth3-comfy \
            READY_TIMEOUT=300 "$TRADITIONAL" start comfy || true
        echo "--- starting API server (user fasth3) ---"
        sudo -u fasth3 env RUN_DIR=/run/fasth3-api LOG_DIR=/var/log/fasth3-api \
            READY_TIMEOUT=120 "$TRADITIONAL" start api || true
        echo "Manage with: sudo fasth3-ctl {start,stop,restart,status,logs} [api|comfy|all]"
        echo "Logs:        /var/log/fasth3-comfy/comfyui.log  /var/log/fasth3-api/fasth3-api.log"
        ;;
    none)
        echo "Services were NOT started (--mode=none)."
        if command -v systemctl >/dev/null 2>&1; then
            echo "Systemd:      sudo systemctl enable --now comfyui fasth3-api"
        fi
        echo "Traditional:  sudo fasth3-ctl start all"
        ;;
esac
sleep 2
curl -s http://127.0.0.1:8000/health || true
echo
COMFY_PY=$(/home/comfy/.venv/bin/python -V 2>&1 || true)
API_PY=$("$FASTH3_HOME/.venv/bin/python" -V 2>&1 || true)
echo "Done. Service is live at https://$FQDN (API key: the one you provided)."
echo "uv environments:  comfy=$COMFY_PY  api=$API_PY"
