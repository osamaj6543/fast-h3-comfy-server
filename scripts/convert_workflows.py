"""Flatten ComfyUI save-format workflows (with subgraphs) into /prompt API format.

The official FastVideo FastH3 template workflows wrap the whole pipeline in a
subgraph ("First Last Frame to Video (FastVideo FastH3)"). ComfyUI's /prompt
API endpoint only accepts flat API-format JSON, so this script flattens the
subgraph into plain nodes and emits parameterizable API-format templates.

Usage:
    python scripts/convert_workflows.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "comfy-workflows"
DST = ROOT / "server" / "app" / "engine" / "workflows"

WORKFLOWS = {
    "fasth3_t2v": SRC / "video_fastvideo_fasth3_t2v.json",
    "fasth3_i2v": SRC / "video_fastvideo_fasth3_i2v.json",
}

SG_INPUT_ID = -10   # virtual origin node id for subgraph inputs
SG_OUTPUT_ID = -20  # virtual target node id for subgraph outputs


def link_map(links: list) -> dict:
    """Normalize links to dicts; save-format top-level links are positional
    arrays [id, origin_id, origin_slot, target_id, target_slot, type] while
    subgraph links are already dicts."""
    out = {}
    for l in links:
        if isinstance(l, dict):
            d = l
        else:
            d = {
                "id": l[0],
                "origin_id": l[1],
                "origin_slot": l[2],
                "target_id": l[3],
                "target_slot": l[4],
                "type": l[5],
            }
        out[int(d["id"])] = d
    return out


def flatten(workflow: dict):
    """Flatten a save-format workflow containing a single subgraph instance.

    Returns (api_workflow, report) where report maps semantic roles to node
    keys so the server can parameterize the template without hard-coding ids.
    """
    subgraphs = workflow.get("definitions", {}).get("subgraphs", [])
    if len(subgraphs) != 1:
        raise ValueError("expected exactly one subgraph definition")
    sg = subgraphs[0]

    # The single node whose type is the subgraph id is the instance.
    instance = None
    for n in workflow["nodes"]:
        if n["type"] == sg["id"]:
            instance = n
    if instance is None:
        raise ValueError("no subgraph instance found")

    top_links = link_map(workflow["links"])
    sg_links = link_map(sg["links"])

    api = {}
    report = {}

    # ------------------------------------------------------------------
    # Resolve the value feeding each subgraph input slot. A slot is fed
    # either by a top-level link into the instance input, or by one of the
    # instance's own widget values.
    # ------------------------------------------------------------------
    inst_named_widgets = instance.get("widgets_values_named") or {}
    inst_inputs = {i["name"]: i for i in instance.get("inputs", [])}

    slot_values = {}
    for idx, sgin in enumerate(sg.get("inputs", [])):
        name = sgin["name"]
        inp = inst_inputs.get(name)
        if inp is not None and inp.get("link") is not None:
            link = top_links[int(inp["link"])]
            origin_id = int(link["origin_id"])
            if origin_id == int(instance["id"]):
                raise ValueError(f"unexpected self-loop on subgraph input {name}")
            slot_values[idx] = [f"n{origin_id}", int(link["origin_slot"])]
        elif name in inst_named_widgets:
            slot_values[idx] = inst_named_widgets[name]
        else:
            slot_values[idx] = None  # disconnected (e.g. first/last_frame in t2v)

    # ------------------------------------------------------------------
    # Emit the subgraph interior nodes.
    # ------------------------------------------------------------------
    for node in sg["nodes"]:
        key = f"s{node['id']}"
        entry = {"class_type": node["type"], "inputs": {}}
        inputs = entry["inputs"]
        for inp in node.get("inputs", []):
            if inp.get("link") is None:
                continue  # widget-only input; value comes from widgets below
            link = sg_links[int(inp["link"])]
            origin_id = int(link["origin_id"])
            origin_slot = int(link["origin_slot"])
            if origin_id == SG_INPUT_ID:
                value = slot_values[origin_slot]
                if value is None:
                    continue  # disconnected subgraph input -> omit input
                inputs[inp["name"]] = value
            elif origin_id == SG_OUTPUT_ID:
                raise ValueError("unexpected subgraph-output link as origin")
            else:
                inputs[inp["name"]] = [f"s{origin_id}", origin_slot]
        named = node.get("widgets_values_named")
        if named:
            for wname, wval in named.items():
                if wname not in inputs:
                    inputs[wname] = wval
        elif node.get("widgets_values"):
            raise ValueError(
                f"node {node['id']} ({node['type']}) has widgets_values but lacks "
                "widgets_values_named"
            )
        api[key] = entry

    # ------------------------------------------------------------------
    # Emit the top-level nodes (skip notes and the subgraph instance).
    # ------------------------------------------------------------------
    for node in workflow["nodes"]:
        if node["type"] == "MarkdownNote" or node["type"] == sg["id"]:
            continue
        key = f"n{node['id']}"
        entry = {"class_type": node["type"], "inputs": {}}
        inputs = entry["inputs"]
        for inp in node.get("inputs", []):
            if inp.get("link") is None:
                continue
            link = top_links[int(inp["link"])]
            origin_id = int(link["origin_id"])
            origin_slot = int(link["origin_slot"])
            if origin_id == int(instance["id"]):
                # Reroute through the subgraph output the link came from.
                out = sg["outputs"][origin_slot]
                out_link = sg_links[int(out["linkIds"][0])]
                inputs[inp["name"]] = [
                    f"s{int(out_link['origin_id'])}",
                    int(out_link["origin_slot"]),
                ]
            else:
                inputs[inp["name"]] = [f"n{origin_id}", origin_slot]
        named = node.get("widgets_values_named")
        if named:
            for wname, wval in named.items():
                if wname not in inputs:
                    inputs[wname] = wval
        elif node.get("widgets_values"):
            raise ValueError(
                f"top-level node {node['id']} ({node['type']}) lacks "
                "widgets_values_named"
            )
        api[key] = entry

    # ------------------------------------------------------------------
    # Semantic roles the server needs to parameterize. Identified by node
    # class so this survives template re-generation with different ids.
    # ------------------------------------------------------------------
    def find(ctype, unique=True):
        keys = [k for k, v in api.items() if v["class_type"] == ctype]
        if unique and len(keys) != 1:
            raise ValueError(f"expected one {ctype}, found {keys}")
        return keys

    report["sampler_noise"] = find("RandomNoise")[0]
    report["h3_i2v"] = find("MiniMaxH3ImageToVideo")[0]
    report["duration_float"] = find("PrimitiveFloat")[0]
    report["unet_loader"] = find("UNETLoader")[0]
    report["clip_loader"] = find("CLIPLoader")[0]
    report["save_video"] = find("SaveVideo")[0]
    report["scheduler"] = find("BasicScheduler")[0]
    report["math_expression"] = find("ComfyMathExpression")[0]
    rs = [k for k, v in api.items() if v["class_type"] == "ResolutionSelector"]
    report["resolution_selector"] = rs[0] if rs else None
    li = [k for k, v in api.items() if v["class_type"] == "LoadImage"]
    report["load_image"] = li[0] if li else None
    return api, report


def main():
    DST.mkdir(parents=True, exist_ok=True)
    for name, path in WORKFLOWS.items():
        workflow = json.loads(path.read_text(encoding="utf-8"))
        api, report = flatten(workflow)
        out = DST / f"{name}.api.json"
        out.write_text(json.dumps(api, indent=2), encoding="utf-8")
        print(f"{name}: {len(api)} nodes -> {out}")
        print(f"  roles: {json.dumps(report)}")
        meta_out = DST / f"{name}.meta.json"
        meta_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        # Sanity checks
        sched = next(v for v in api.values() if v["class_type"] == "BasicScheduler")
        assert sched["inputs"]["steps"] == 8, "FastH3 must run exactly 8 steps"
        keys = set(api)
        for v in api.values():
            for val in v["inputs"].values():
                if isinstance(val, list):
                    assert val[0] in keys, f"dangling link {val}"
    print("all templates valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
