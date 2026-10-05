#!/usr/bin/env python3
"""
Render an annotations JSON document (the annotator's output contract) as a
Nextflow config that layers onto any pipeline with `-c`:

    process { withName: 'X' { ext.fairscape = [...] } }   one per annotated process
    fairscape { ...; metadata = [...] }                    root shortcuts + metadata

Only fields the document states are emitted. Anything listed under `review`
becomes a `// REVIEW:` comment so a human sees what the agent could not settle.

    render_config.py annotations.json -o fairscape-annotations.config [--standalone]
                     [--plugin-version 0.1.0] [--report annotations.md]

--standalone adds the plugins block and `file`/`overwrite`, for a pipeline that
has no fairscape block of its own.

Annotations document:

{
  "workflow": {"dir": "...", "main": "...", "name": "..."},
  "generated": {"by": "...", "date": "..."},
  "processes": [
    {"process": "IMAGE_DOWNLOAD", "selector": "IMAGE_DOWNLOAD",
     "ext": {"softwareName": "...", "softwareVersion": "...", ...},
     "evidence": [{"field": "softwareVersion", "source": "...", "value": "..."}],
     "review": ["..."], "confidence": "high"}
  ],
  "root": {"author": "...", "organization": "...", "license": "...", "keywords": [...],
           "description": "...", "metadata": {"rai:dataBiases": "...", ...}},
  "root_evidence": [...], "root_review": ["..."]
}
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

EXT_ORDER = ["softwareName", "softwareVersion", "softwareAuthor", "softwareDescription",
             "softwareUrl", "softwareFormat", "softwareKeywords"]
ROOT_SHORTCUTS = ["author", "organization", "description", "keywords", "license"]


def gstr(s: str) -> str:
    s = str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "")
    return f"'{s}'"


def gkey(k: str) -> str:
    return k if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k) else gstr(k)


def gval(v, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return gstr(v)
    if isinstance(v, list):
        if all(isinstance(x, (str, int, float, bool)) for x in v) and sum(len(str(x)) for x in v) < 80:
            return "[" + ", ".join(gval(x) for x in v) + "]"
        items = ",\n".join(pad + "    " + gval(x, indent + 4) for x in v)
        return "[\n" + items + "\n" + pad + "]"
    if isinstance(v, dict):
        if not v:
            return "[:]"
        if all(isinstance(x, (str, int, float, bool, type(None))) for x in v.values()) and sum(len(str(x)) for x in v.values()) < 100:
            return "[" + ", ".join(f"{gkey(k)}: {gval(x)}" for k, x in v.items()) + "]"
        width = max(len(gkey(k)) for k in v)
        items = ",\n".join(f"{pad}    {gkey(k).ljust(width)}: {gval(x, indent + 4)}" for k, x in v.items())
        return "[\n" + items + "\n" + pad + "]"
    return gstr(str(v))


def comment_block(lines: list[str], indent: int, prefix: str = "REVIEW: ") -> list[str]:
    pad = " " * indent
    out = []
    for line in lines:
        text = f"{prefix}{line}"
        # wrap at ~100 columns
        while len(text) > 100:
            cut = text.rfind(" ", 0, 100)
            cut = cut if cut > 40 else 100
            out.append(f"{pad}// {text[:cut]}")
            text = "    " + text[cut:].lstrip()
        out.append(f"{pad}// {text}")
    return out


def render(doc: dict, standalone: bool = False, plugin_version: str = "0.1.0") -> str:
    wf = doc.get("workflow", {})
    gen = doc.get("generated", {})
    lines = [
        "/*",
        f" * nf-fairscape annotations for {wf.get('name') or wf.get('main') or 'this workflow'}",
        f" * Generated {gen.get('date') or dt.date.today().isoformat()} by {gen.get('by') or 'the nf-fairscape annotator'}.",
        " *",
        " * Layer onto the run with `-c`:",
        f" *     nextflow run {wf.get('main', 'main.nf')} -c {doc.get('output_name', 'fairscape-annotations.config')}",
        " *",
        " * Every value below was derived from the workflow source, the installed software and the",
        " * machine it was inventoried on; the evidence is in the report next to this file. Lines",
        " * marked REVIEW are things the annotator could not establish -- fill them in or delete them.",
        " */",
        "",
    ]
    if standalone:
        lines += [
            "plugins {",
            f"    id 'nf-fairscape@{plugin_version}'",
            "}",
            "",
        ]

    procs = [p for p in doc.get("processes", []) if p.get("ext")]
    if procs:
        lines.append("process {")
        for i, p in enumerate(procs):
            sel = p.get("selector") or p["process"]
            if i:
                lines.append("")
            conf = p.get("confidence")
            if conf and conf != "high":
                lines.append(f"    // confidence: {conf}")
            lines += comment_block(p.get("review") or [], 4)
            lines.append(f"    withName: {gstr(sel)} {{")
            ext = {k: p["ext"][k] for k in EXT_ORDER if k in p["ext"] and p["ext"][k] not in (None, "", [])}
            lines.append("        ext.fairscape = " + gval(ext, 8))
            lines.append("    }")
        lines.append("}")
        lines.append("")

    root = doc.get("root") or {}
    has_root = any(root.get(k) for k in ROOT_SHORTCUTS) or root.get("metadata") or standalone or doc.get("root_review")
    if has_root:
        lines.append("fairscape {")
        if standalone:
            lines.append('    file      = "${params.outdir}/ro-crate-metadata.json"')
            lines.append("    overwrite = true")
            lines.append("")
        width = max([len(k) for k in ROOT_SHORTCUTS if root.get(k)] + [1])
        for k in ROOT_SHORTCUTS:
            if root.get(k):
                lines.append(f"    {k.ljust(width)} = {gval(root[k], 4)}")
        meta = root.get("metadata") or {}
        meta = {k: v for k, v in meta.items() if v not in (None, "", [])}
        if root.get("metadata") is not None or doc.get("root_review"):
            lines.append("")
            lines += comment_block(doc.get("root_review") or [], 4)
            lines.append("    metadata = " + gval(meta, 4))
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def report(doc: dict) -> str:
    wf = doc.get("workflow", {})
    out = [f"# Annotation report: {wf.get('name') or wf.get('main')}", ""]
    gen = doc.get("generated", {})
    out.append(f"Generated {gen.get('date', '')} by {gen.get('by', 'the nf-fairscape annotator')}.")
    out.append("")
    if doc.get("compute"):
        out.append("## Compute inventoried")
        out.append("")
        out.append("```json")
        out.append(json.dumps(doc["compute"], indent=2, default=str))
        out.append("```")
        out.append("")
    out.append("## Processes")
    out.append("")
    for p in doc.get("processes", []):
        out.append(f"### {p['process']}  (confidence: {p.get('confidence', '?')})")
        out.append("")
        for k in EXT_ORDER:
            if k in (p.get("ext") or {}):
                out.append(f"- **{k}**: {p['ext'][k]}")
        if p.get("evidence"):
            out.append("")
            out.append("Evidence:")
            out.append("")
            for e in p["evidence"]:
                out.append(f"- `{e.get('field')}` <- {e.get('source')}" + (f" = `{e.get('value')}`" if e.get("value") not in (None, "") else ""))
        if p.get("review"):
            out.append("")
            out.append("Needs review:")
            out.append("")
            for r in p["review"]:
                out.append(f"- {r}")
        if p.get("diff"):
            out.append("")
            out.append("Differs from the existing annotation:")
            out.append("")
            for d in p["diff"]:
                out.append(f"- {d}")
        out.append("")
    out.append("## Run-level metadata")
    out.append("")
    root = doc.get("root") or {}
    for k in ROOT_SHORTCUTS:
        if root.get(k):
            out.append(f"- **{k}**: {root[k]}")
    for k, v in (root.get("metadata") or {}).items():
        out.append(f"- **{k}**: {v if not isinstance(v, (list, dict)) else json.dumps(v)}")
    if doc.get("root_evidence"):
        out.append("")
        out.append("Evidence:")
        out.append("")
        for e in doc["root_evidence"]:
            out.append(f"- `{e.get('field')}` <- {e.get('source')}")
    if doc.get("root_review"):
        out.append("")
        out.append("Needs review:")
        out.append("")
        for r in doc["root_review"]:
            out.append(f"- {r}")
    out.append("")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("annotations")
    ap.add_argument("-o", "--output", default="fairscape-annotations.config")
    ap.add_argument("--report", help="also write a Markdown evidence report here")
    ap.add_argument("--standalone", action="store_true")
    ap.add_argument("--plugin-version", default="0.1.0")
    a = ap.parse_args(argv)
    doc = json.loads(Path(a.annotations).read_text())
    doc.setdefault("output_name", Path(a.output).name)
    Path(a.output).write_text(render(doc, a.standalone, a.plugin_version))
    n = len([p for p in doc.get("processes", []) if p.get("ext")])
    m = len((doc.get("root") or {}).get("metadata") or {})
    print(f"wrote {a.output}: {n} process annotations, {m} root metadata keys")
    if a.report:
        Path(a.report).write_text(report(doc))
        print(f"wrote {a.report}")


if __name__ == "__main__":
    main()
