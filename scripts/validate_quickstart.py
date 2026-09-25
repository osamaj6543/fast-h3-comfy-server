"""Validate docs/quickstart.md: JSON samples parse, referenced paths/routes exist."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
doc = (ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")
problems = []

# 1) the curl -d JSON body must parse
for m in re.finditer(r"-d '(\{.*?\})'", doc, re.S):
    try:
        payload = json.loads(m.group(1))
        print(f"OK  JSON body parses (prompt {len(payload['prompt'])} chars, "
              f"duration={payload['duration_seconds']})")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"invalid JSON sample: {exc}")

# 2) every repo path referenced must exist
paths = set(re.findall(r"(?:\./)?(docs/[\w.-]+\.md|deploy/[\w./-]+)", doc))
for p in sorted(paths):
    exists = (ROOT / p).exists()
    print(("OK  " if exists else "MISSING ") + p)
    if not exists:
        problems.append(f"referenced path does not exist: {p}")

# 3) documented API routes must exist in the app
sys.path.insert(0, str(ROOT / "server"))  # app package lives under server/
from app.main import create_app  # noqa: E402
from app.config import Settings  # noqa: E402

app = create_app(Settings(data_dir="./_qs_tmp", _env_file=None))
routes = {r.path for r in app.routes if hasattr(r, "path")}
for route in sorted(set(re.findall(r"http://127\.0\.0\.1:8000(/[\w{}/-]*)", doc))):
    base = re.sub(r"\{[^}]+\}|[0-9a-f]{8}", "{job_id}", route)
    base = base.replace("PASTE_THE_CONTENT_LINK_HERE", "")
    ok = base in routes or route.split("/")[1:2] == [""]
    print(f"{'OK  ' if ok else 'CHECK'} {route} -> matched {base!r}")
import shutil  # noqa: E402

shutil.rmtree("./_qs_tmp", ignore_errors=True)

# 4) markdown fences balanced
fences = doc.count("```")
print(f"\nfences: {fences} ({'balanced' if fences % 2 == 0 else 'UNBALANCED'})")
if fences % 2:
    problems.append("unbalanced code fences")

# 5) model filenames in the doc must match what the API templates expect
templates = (ROOT / "server/app/engine/workflows/fasth3_t2v.api.json").read_text(encoding="utf-8")
for name in [
    "fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors",
    "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "minimax_h3_video_vae_int8_convrot.safetensors",
    "minimax_h3_audio_vae_fp32.safetensors",
]:
    in_doc = name in doc
    in_template = name in templates
    print(f"{'OK  ' if in_doc and in_template else 'MISMATCH'} {name} "
          f"(doc={in_doc}, template={in_template})")
    if not (in_doc and in_template):
        problems.append(f"model name mismatch: {name}")

print()
if problems:
    print("PROBLEMS:")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("QUICKSTART DOC VALIDATED")
