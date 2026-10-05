#!/usr/bin/env python3
"""
Validate an annotations JSON document before it is rendered, and optionally
the rendered config after.

    validate.py annotations.json [--inventory inventory.json]
                [--config fairscape-annotations.config --workflow DIR]

Checks (errors fail, warnings print):
  * every ext key is one nf-fairscape understands; values are non-empty strings
    (softwareKeywords a list of strings); softwareUrl looks like a URL;
    softwareDescription is >= 10 chars (the plugin's own threshold)
  * no placeholder text (TODO, TBD, FIXME, lorem, placeholder, <...>) in any value;
    'unknown' / 'N/A' are warnings
  * every annotated process exists in the inventory, and every inventoried process
    is either annotated or listed under `skipped`
  * root metadata does not touch the plugin-managed keys; unknown non-rai keys are
    warned about (they expand under schema.org and resolve to nothing)
  * softwareVersion has an evidence entry (warning)
  * --config: `nextflow config -flat` parses the rendered file on top of the
    workflow and shows each withName ext.fairscape
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

KNOWN_EXT_KEYS = ["softwareName", "softwareVersion", "softwareAuthor",
                  "softwareDescription", "softwareUrl", "softwareFormat", "softwareKeywords"]
PROTECTED_ROOT_KEYS = ["@id", "@type", "conformsTo", "hasPart"]
ROOT_KEYS = {
    "name", "description", "keywords", "version", "identifier", "url", "about", "language",
    "creativeWorkStatus", "datePublished", "dateCreated", "dateModified", "correction", "isPartOf",
    "author", "publisher", "principalInvestigator", "funder", "contactEmail", "citation", "associatedPublication",
    "license", "conditionsOfAccess", "copyrightNotice", "usageInfo", "prohibitedUses",
    "ethicalReview", "humanSubjectResearch", "humanSubjectExemption", "irb", "irbProtocolId",
    "dataGovernanceCommittee", "deidentified", "fdaRegulated", "confidentialityLevel",
    "contentSize", "hasSummaryStatistics", "additionalProperty", "completeness",
}
RAI_KEYS = {
    "rai:dataCollection", "rai:dataCollectionType", "rai:dataCollectionRawData", "rai:dataCollectionMissingData",
    "rai:dataCollectionTimeframe", "rai:dataBiases", "rai:dataLimitations", "rai:dataUseCases",
    "rai:dataSocialImpact", "rai:personalSensitiveInformation", "rai:dataPreprocessingProtocol",
    "rai:dataManipulationProtocol", "rai:dataImputationProtocol", "rai:dataAnnotationProtocol",
    "rai:dataAnnotationPlatform", "rai:dataAnnotationAnalysis", "rai:annotationsPerItem",
    "rai:machineAnnotationTools", "rai:dataReleaseMaintenancePlan",
}
# angle-bracket placeholders only when they read as a template slot (<YOUR NAME>, <insert doi>),
# not a documented file-name convention like <fold>_node_attributes.tsv
PLACEHOLDER_ERR = re.compile(r"\b(TODO|TBD|FIXME|XXX|lorem ipsum|placeholder|INSERT [A-Z]+|CHANGEME)\b|<(?:your|insert|add|enter|fill)\b[^>]*>|(?-i:<[A-Z][A-Z _-]{2,}>)", re.I)
PLACEHOLDER_WARN = re.compile(r"\b(unknown|n/?a|not (?:available|known)|unspecified)\b", re.I)
URL_RE = re.compile(r"^(https?://|git@|ftp://|file://|s3://)")


def walk_strings(value, path=""):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from walk_strings(v, f"{path}[{i}]")
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from walk_strings(v, f"{path}.{k}" if path else k)


def validate(doc: dict, inventory: dict | None) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    procs = doc.get("processes") or []
    names = [p.get("process") for p in procs]
    if len(set(names)) != len(names):
        errors.append(f"duplicate process entries: {sorted(n for n in names if names.count(n) > 1)}")

    for p in procs:
        name = p.get("process") or "?"
        ext = p.get("ext")
        if ext is None:
            continue
        if not isinstance(ext, dict):
            errors.append(f"{name}: ext must be a map")
            continue
        for k, v in ext.items():
            if k not in KNOWN_EXT_KEYS:
                errors.append(f"{name}: unknown ext key '{k}' (allowed: {KNOWN_EXT_KEYS})")
                continue
            if k == "softwareKeywords":
                if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
                    errors.append(f"{name}: softwareKeywords must be a list of non-empty strings")
                continue
            if not isinstance(v, str) or not v.strip():
                errors.append(f"{name}: {k} must be a non-empty string")
                continue
            if k == "softwareUrl" and not URL_RE.match(v):
                errors.append(f"{name}: softwareUrl is not a URL: {v!r}")
            if k == "softwareDescription" and len(v.strip()) < 10:
                errors.append(f"{name}: softwareDescription under 10 chars is replaced by the plugin's fallback")
            if k == "softwareVersion" and v.strip().lower() in ("latest", "unknown", "", "none", "null"):
                errors.append(f"{name}: softwareVersion {v!r} is not a version; omit the key instead")
        if "softwareVersion" in ext:
            fields = {e.get("field") for e in (p.get("evidence") or [])}
            if "softwareVersion" not in fields:
                warnings.append(f"{name}: softwareVersion has no evidence entry")
        for path, s in walk_strings(ext, f"{name}.ext"):
            if PLACEHOLDER_ERR.search(s):
                errors.append(f"{path}: placeholder text: {s[:80]!r}")
            elif PLACEHOLDER_WARN.search(s):
                warnings.append(f"{path}: contains 'unknown'/'N/A' -- state a fact or omit the key: {s[:80]!r}")

    if inventory:
        inv_names = {p["name"] for p in inventory.get("processes", [])}
        skipped = set(doc.get("skipped") or [])
        for n in names:
            if n not in inv_names:
                errors.append(f"annotated process '{n}' is not in the inventory ({sorted(inv_names)})")
        for n in sorted(inv_names - set(names) - skipped):
            warnings.append(f"inventoried process '{n}' has no annotation and is not listed under 'skipped'")

    root = doc.get("root") or {}
    for k in ("author", "organization", "description", "license"):
        if k in root and root[k] is not None and (not isinstance(root[k], str) or not root[k].strip()):
            errors.append(f"root.{k} must be a non-empty string")
    if "keywords" in root and root["keywords"] is not None and not (isinstance(root["keywords"], list) and all(isinstance(x, str) for x in root["keywords"])):
        errors.append("root.keywords must be a list of strings")
    if root.get("license") and not URL_RE.match(root["license"]):
        warnings.append(f"root.license should be an SPDX URI, got {root['license']!r}")
    meta = root.get("metadata") or {}
    if not isinstance(meta, dict):
        errors.append("root.metadata must be a map")
        meta = {}
    for k, v in meta.items():
        if k in PROTECTED_ROOT_KEYS:
            errors.append(f"root.metadata.{k} is managed by the plugin and will be refused")
        elif k.startswith("rai:"):
            if k not in RAI_KEYS:
                warnings.append(f"root.metadata.{k} is not a Croissant RAI 1.0 property")
        elif k not in ROOT_KEYS:
            warnings.append(f"root.metadata.{k} is not a FAIRSCAPE profile field; it will expand under schema.org and resolve to nothing. Prefer additionalProperty")
        if v is None or v == "" or v == []:
            errors.append(f"root.metadata.{k} is empty; omit it instead")
    if "additionalProperty" in meta:
        ap = meta["additionalProperty"]
        if not isinstance(ap, list) or not all(isinstance(x, dict) and x.get("name") and "value" in x for x in ap):
            errors.append("root.metadata.additionalProperty must be a list of {'@type':'PropertyValue', name, value} maps")
    for path, s in walk_strings(root, "root"):
        if PLACEHOLDER_ERR.search(s):
            errors.append(f"{path}: placeholder text: {s[:80]!r}")
        elif PLACEHOLDER_WARN.search(s) and not path.endswith(("humanSubjectResearch", "humanSubjectExemption")):
            warnings.append(f"{path}: contains 'unknown'/'N/A': {s[:80]!r}")
    return errors, warnings


def check_config(config: Path, workflow: Path, doc: dict) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    nf = shutil.which("nextflow")
    if not nf:
        warnings.append("nextflow not on PATH; skipped the config parse check")
        return errors, warnings
    env = dict(os.environ, NXF_ANSI_LOG="false")
    r = subprocess.run([nf, "-q", "-c", str(config), "config", "-flat", str(workflow)],
                       capture_output=True, text=True, timeout=300, env=env)
    if r.returncode != 0:
        # the workflow's own config may not parse here (an nf-core main.nf copied out without
        # its conf/ directory); parse the rendered file on its own before blaming it
        import tempfile
        with tempfile.TemporaryDirectory() as empty:
            alone = subprocess.run([nf, "-q", "-c", str(config), "config", "-flat", empty],
                                   capture_output=True, text=True, timeout=300, env=env)
        if alone.returncode != 0:
            errors.append("nextflow config failed on the rendered file:\n" + (alone.stderr or alone.stdout).strip()[-1500:])
            return errors, warnings
        warnings.append(f"the workflow's own config in {workflow} does not parse here "
                        f"({(r.stderr or r.stdout).strip().splitlines()[-1][:120] if (r.stderr or r.stdout).strip() else 'no message'}); "
                        "the rendered file was parse-checked on its own instead")
        r = alone
    flat = r.stdout
    for p in doc.get("processes") or []:
        if not p.get("ext"):
            continue
        sel = p.get("selector") or p["process"]
        if f"withName:{sel}" not in flat.replace("'", "").replace('"', ""):
            errors.append(f"withName:{sel} ext.fairscape not visible in `nextflow config -flat` output")
    if (doc.get("root") or {}).get("metadata") and "fairscape.metadata" not in flat:
        errors.append("fairscape.metadata not visible in `nextflow config -flat` output")
    return errors, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("annotations")
    ap.add_argument("--inventory")
    ap.add_argument("--config", help="rendered config to parse-check with nextflow")
    ap.add_argument("--workflow", help="workflow dir for the parse check")
    a = ap.parse_args(argv)
    doc = json.loads(Path(a.annotations).read_text())
    inv = json.loads(Path(a.inventory).read_text()) if a.inventory else None
    errors, warnings = validate(doc, inv)
    if a.config:
        e2, w2 = check_config(Path(a.config), Path(a.workflow or Path(doc.get("workflow", {}).get("dir", "."))), doc)
        errors += e2
        warnings += w2
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    n = len([p for p in doc.get("processes") or [] if p.get("ext")])
    print(f"{'FAIL' if errors else 'OK'}: {n} process annotations, {len((doc.get('root') or {}).get('metadata') or {})} root metadata keys, "
          f"{len(errors)} errors, {len(warnings)} warnings")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
