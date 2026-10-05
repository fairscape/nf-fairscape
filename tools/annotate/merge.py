#!/usr/bin/env python3
"""
Assemble the annotator's annotations.json from the pieces the scouts wrote:

    merge.py --inventory inventory.json --scouts 'scout_*.json' [--dataset dataset.json]
             [--by "nf-fairscape annotator"] -o annotations.json

* each scout file holds one software-scout object (or a list of them, for aliases)
* dataset.json holds the dataset scout's {root, root_evidence, root_review, compute}
* existing `fairscape.metadata` from the inventory is the base; the dataset scout's
  metadata merges on top of it; existing `ext fairscape` values fill any ext key a scout
  left out
* every inventoried process with no scout result is listed under `skipped`, so the
  validator can flag it
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
from pathlib import Path

EXT_KEYS = ["softwareName", "softwareVersion", "softwareAuthor", "softwareDescription",
            "softwareUrl", "softwareFormat", "softwareKeywords"]


def load_scouts(patterns: list[str]) -> list[dict]:
    out = []
    for pat in patterns:
        for f in sorted(glob.glob(pat)):
            try:
                d = json.loads(Path(f).read_text())
            except Exception as e:  # noqa: BLE001
                print(f"WARN {f}: not JSON ({e}); skipped")
                continue
            items = d if isinstance(d, list) else [d]
            for it in items:
                if isinstance(it, dict) and it.get("process"):
                    it.setdefault("_file", f)
                    out.append(it)
                else:
                    print(f"WARN {f}: entry without 'process'; skipped")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inventory", required=True)
    ap.add_argument("--scouts", nargs="+", required=True, help="glob(s) of software-scout JSON files")
    ap.add_argument("--dataset", help="dataset-scout JSON")
    ap.add_argument("--by", default="nf-fairscape annotator (fairscape-annotate skill)")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args(argv)

    inv = json.loads(Path(a.inventory).read_text())
    inv_procs = {p["name"]: p for p in inv.get("processes", [])}
    scouts = load_scouts(a.scouts)

    processes, seen = [], set()
    for s in scouts:
        name = s["process"]
        if name in seen:
            print(f"WARN duplicate scout result for {name}; keeping the first")
            continue
        seen.add(name)
        ext = {k: v for k, v in (s.get("ext") or {}).items() if k in EXT_KEYS and v not in (None, "", [])}
        dropped = [k for k in (s.get("ext") or {}) if k not in EXT_KEYS]
        if dropped:
            print(f"WARN {name}: dropped unknown ext key(s) {dropped}")
        existing = (inv_procs.get(name) or {}).get("existing_ext_fairscape") or {}
        for k in EXT_KEYS:
            if k not in ext and existing.get(k) not in (None, "", []) and isinstance(existing.get(k), (str, list)):
                # keep the human's value where the scout said nothing -- unless the scout
                # deliberately dropped it (a local path in softwareUrl, say), which it says in diff
                if any(k in d and "omitted" in d for d in (s.get("diff") or [])):
                    continue
                ext[k] = existing[k]
        entry = {
            "process": name,
            "selector": s.get("selector") or name,
            "ext": ext,
            "evidence": s.get("evidence") or [],
            "review": s.get("review") or [],
            "confidence": s.get("confidence") or "medium",
        }
        if s.get("diff"):
            entry["diff"] = s["diff"]
        if name not in inv_procs:
            print(f"WARN scout result for '{name}', which is not in the inventory")
        processes.append(entry)
    order = list(inv_procs)
    processes.sort(key=lambda p: order.index(p["process"]) if p["process"] in order else 999)
    skipped = [n for n in inv_procs if n not in seen]

    cfg = inv.get("config") or {}
    fs = cfg.get("fairscape") or {}
    root = {}
    for k in ("author", "organization", "description", "keywords", "license"):
        if isinstance(fs.get(k), (str, list)) and fs.get(k):
            root[k] = fs[k]
    existing_meta = fs.get("metadata") if isinstance(fs.get("metadata"), dict) else {}
    existing_meta = {k: v for k, v in existing_meta.items() if not (isinstance(v, dict) and ("$expr" in v or "$unparsed" in v))}
    root["metadata"] = dict(existing_meta)
    root_evidence, root_review, compute = [], [], None
    if a.dataset:
        ds = json.loads(Path(a.dataset).read_text())
        droot = ds.get("root") or {}
        for k in ("author", "organization", "description", "keywords", "license"):
            if droot.get(k):
                root[k] = droot[k]
        root["metadata"].update({k: v for k, v in (droot.get("metadata") or {}).items() if v not in (None, "", [])})
        root_evidence = ds.get("root_evidence") or []
        root_review = ds.get("root_review") or []
        compute = ds.get("compute")
    for n in skipped:
        root_review.append(f"process {n}: no scout result; it keeps the plugin's process-derived Software entity")

    wf = inv.get("workflow") or {}
    manifest = cfg.get("manifest") or {}
    doc = {
        "workflow": {"dir": wf.get("dir"), "main": os.path.basename(wf.get("main") or "main.nf"),
                     "name": manifest.get("name") if isinstance(manifest.get("name"), str) else os.path.basename(wf.get("dir") or "")},
        "generated": {"by": a.by, "date": dt.date.today().isoformat()},
        "plugin_enabled": any("nf-fairscape" in str(p) for p in cfg.get("plugins") or []),
        "processes": processes,
        "skipped": skipped,
        "root": root,
        "root_evidence": root_evidence,
        "root_review": root_review,
        "compute": compute or (inv.get("machine") or {}),
    }
    Path(a.output).write_text(json.dumps(doc, indent=2, default=str))
    print(f"wrote {a.output}: {len(processes)} processes annotated, {len(skipped)} skipped, "
          f"{len(root['metadata'])} root metadata keys, plugin_enabled={doc['plugin_enabled']}")


if __name__ == "__main__":
    main()
