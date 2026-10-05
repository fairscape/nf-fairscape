#!/usr/bin/env python3
"""
Read-only probes of the software and compute a workflow runs on, for the
nf-fairscape annotator's scout agents. Every subcommand prints one JSON object
and never modifies anything; commands it runs are version/metadata queries
with a timeout.

    probe_env.py machine
    probe_env.py interpreter "<python command>"           # e.g. "conda run -n cellmap python"
    probe_env.py package "<python command>" NAME [NAME...] # pip show + import metadata
    probe_env.py exe NAME [--via "<prefix command>"]      # which + --version
    probe_env.py container IMAGE                           # engine inspect, when an engine exists
    probe_env.py script PATH                               # header, __version__, git provenance
    probe_env.py conda-env NAME [--filter PKG...]          # conda list -n NAME
    probe_env.py versions-yml DIR                          # every versions.yml under DIR (a work/ or results/ tree)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inventory import machine_snapshot  # noqa: E402


def run(cmd: list[str], timeout=30, cwd=None) -> dict:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return {"cmd": " ".join(shlex.quote(c) for c in cmd), "rc": r.returncode,
                "stdout": r.stdout.strip(), "stderr": r.stderr.strip()[-2000:]}
    except FileNotFoundError:
        return {"cmd": " ".join(cmd), "rc": 127, "stdout": "", "stderr": "not found"}
    except subprocess.TimeoutExpired:
        return {"cmd": " ".join(cmd), "rc": 124, "stdout": "", "stderr": "timeout"}


def emit(obj):
    print(json.dumps(obj, indent=2, default=str))


# --------------------------------------------------------------------------- interpreter
def interpreter(cmd: str) -> dict:
    argv = shlex.split(cmd)
    code = ("import sys, platform, json, os; "
            "print(json.dumps(dict(executable=sys.executable, version=platform.python_version(), "
            "prefix=sys.prefix, implementation=platform.python_implementation(), "
            "conda_env=os.environ.get('CONDA_DEFAULT_ENV') or os.path.basename(sys.prefix))))")
    r = run(argv + ["-c", code], timeout=120)
    out = {"command": cmd, "ok": r["rc"] == 0}
    if r["rc"] == 0:
        line = [l for l in r["stdout"].splitlines() if l.startswith("{")]
        try:
            out.update(json.loads(line[-1]))
        except Exception:  # noqa: BLE001
            out["raw"] = r["stdout"]
    else:
        out["error"] = r["stderr"] or r["stdout"]
    return out


# --------------------------------------------------------------------------- package
def package(interp: str, names: list[str]) -> dict:
    argv = shlex.split(interp)
    code = r'''
import json, sys, importlib
from importlib import metadata as md
out = {}
for name in sys.argv[1:]:
    rec = {"name": name}
    try:
        dist = md.distribution(name)
        meta = dist.metadata
        rec.update({
            "found": True,
            "version": dist.version,
            "summary": meta.get("Summary"),
            "home_page": meta.get("Home-page"),
            "author": meta.get("Author"),
            "author_email": meta.get("Author-email"),
            "maintainer": meta.get("Maintainer"),
            "license": meta.get("License"),
            "project_urls": meta.get_all("Project-URL") or [],
            "classifiers": [c for c in (meta.get_all("Classifier") or []) if c.startswith("License")],
            "requires": (dist.requires or [])[:40],
            "location": str(dist.locate_file("")),
            "direct_url": None,
        })
        try:
            du = dist.read_text("direct_url.json")
            rec["direct_url"] = json.loads(du) if du else None
        except Exception:
            pass
        try:
            rec["top_level"] = (dist.read_text("top_level.txt") or "").split()
        except Exception:
            pass
    except md.PackageNotFoundError:
        rec["found"] = False
        try:
            mod = importlib.import_module(name)
            rec["import_version"] = getattr(mod, "__version__", None)
            rec["import_file"] = getattr(mod, "__file__", None)
        except Exception as e:
            rec["import_error"] = str(e)[:200]
    out[name] = rec
print(json.dumps(out))
'''
    r = run(argv + ["-c", code] + names, timeout=180)
    if r["rc"] != 0:
        return {"interpreter": interp, "error": r["stderr"] or r["stdout"]}
    line = [l for l in r["stdout"].splitlines() if l.startswith("{")]
    try:
        return {"interpreter": interp, "packages": json.loads(line[-1])}
    except Exception:  # noqa: BLE001
        return {"interpreter": interp, "raw": r["stdout"]}


# --------------------------------------------------------------------------- exe
def exe(name: str, via: str | None) -> dict:
    prefix = shlex.split(via) if via else []
    out = {"name": name, "via": via}
    if not prefix:
        out["path"] = shutil.which(name)
    else:
        w = run(prefix + ["which", name], timeout=60)
        out["path"] = w["stdout"].splitlines()[-1] if w["rc"] == 0 and w["stdout"] else None
    tried = []
    for flag in ("--version", "-version", "version", "-v", "-V", "--help"):
        r = run(prefix + [name, flag], timeout=30)
        text = (r["stdout"] or r["stderr"]).strip()
        tried.append({"flag": flag, "rc": r["rc"], "first_line": text.splitlines()[0][:200] if text else ""})
        if r["rc"] in (0, 1) and text and re.search(r"\d+\.\d+", text.splitlines()[0]):
            out["version_line"] = text.splitlines()[0][:300]
            out["version_flag"] = flag
            m = re.search(r"(\d+\.\d+(?:\.\d+)*(?:[-.\w]*)?)", text.splitlines()[0])
            out["version_guess"] = m.group(1) if m else None
            break
    out["tried"] = tried
    return out


# --------------------------------------------------------------------------- container
def container(image: str) -> dict:
    out = {"image": image, "available": False}
    for eng in ("docker", "podman"):
        if shutil.which(eng):
            r = run([eng, "image", "inspect", image], timeout=60)
            if r["rc"] == 0:
                try:
                    info = json.loads(r["stdout"])[0]
                except Exception:  # noqa: BLE001
                    continue
                cfg = info.get("Config") or {}
                out.update({"available": True, "engine": eng, "id": info.get("Id"),
                            "repo_digests": info.get("RepoDigests"), "labels": cfg.get("Labels"),
                            "entrypoint": cfg.get("Entrypoint"), "cmd": cfg.get("Cmd"),
                            "created": info.get("Created")})
                return out
            out["error"] = r["stderr"][-300:]
    if shutil.which("skopeo"):
        r = run(["skopeo", "inspect", f"docker://{image}"], timeout=90)
        if r["rc"] == 0:
            try:
                info = json.loads(r["stdout"])
                out.update({"available": True, "engine": "skopeo", "digest": info.get("Digest"),
                            "labels": info.get("Labels"), "created": info.get("Created")})
            except Exception:  # noqa: BLE001
                pass
    # the tag itself often carries the version (biocontainers: tool:1.2.3--hash)
    m = re.search(r":([^:/]+?)(?:--[0-9a-f]+(?:_\d+)?)?$", image)
    if m and re.search(r"\d", m.group(1)):
        out["tag_version_guess"] = m.group(1)
    return out


# --------------------------------------------------------------------------- script
def script(path: str) -> dict:
    p = Path(path).expanduser()
    out = {"path": str(p), "exists": p.is_file()}
    if not p.is_file():
        return out
    text = p.read_text(errors="replace")
    lines = text.splitlines()
    out["size"] = p.stat().st_size
    out["lines"] = len(lines)
    out["shebang"] = lines[0] if lines and lines[0].startswith("#!") else None
    out["header"] = "\n".join(lines[:40])
    m = re.search(r"""__version__\s*=\s*['"]([^'"]+)['"]""", text)
    out["dunder_version"] = m.group(1) if m else None
    m = re.search(r"""(?m)^\s*(?:#|//|\*)?\s*(?:Author|Authors|@author)\s*[:=]\s*(.+)$""", text)
    out["author_line"] = m.group(1).strip() if m else None
    m = re.search(r'(?s)^\s*(?:#![^\n]*\n)?\s*(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', text)
    out["docstring"] = m.group(1).strip()[:1500] if m else None
    out["imports"] = sorted(set(re.findall(r"(?m)^\s*(?:import|from)\s+([A-Za-z_][\w]*)", text)))[:40]
    d = p.parent
    if run(["git", "-C", str(d), "rev-parse", "--is-inside-work-tree"], timeout=10)["stdout"] == "true":
        out["git"] = {
            "remote": run(["git", "-C", str(d), "remote", "get-url", "origin"], timeout=10)["stdout"] or None,
            "last_commit": run(["git", "-C", str(d), "log", "-1", "--format=%H|%an|%aI|%s", "--", p.name], timeout=10)["stdout"] or None,
            "authors": run(["git", "-C", str(d), "log", "--format=%an", "--", p.name], timeout=10)["stdout"].splitlines()[:20],
            "root": run(["git", "-C", str(d), "rev-parse", "--show-toplevel"], timeout=10)["stdout"] or None,
        }
        out["git"]["authors"] = sorted(set(out["git"]["authors"]))
    return out


# --------------------------------------------------------------------------- conda env
def conda_env(name: str, filt: list[str]) -> dict:
    r = run(["conda", "list", "-n", name, "--json"], timeout=120)
    if r["rc"] != 0:
        return {"env": name, "error": r["stderr"] or r["stdout"]}
    try:
        pkgs = json.loads(r["stdout"])
    except Exception as e:  # noqa: BLE001
        return {"env": name, "error": str(e)}
    if filt:
        want = {f.lower() for f in filt}
        pkgs = [p for p in pkgs if p.get("name", "").lower() in want]
    return {"env": name, "count": len(pkgs), "packages": [{k: p.get(k) for k in ("name", "version", "channel", "build_string")} for p in pkgs]}


# --------------------------------------------------------------------------- versions.yml
def versions_yml(root: str) -> dict:
    found = {}
    for p in Path(root).rglob("versions.yml"):
        try:
            found[str(p)] = p.read_text(errors="replace")[:2000]
        except Exception:  # noqa: BLE001
            pass
        if len(found) >= 200:
            break
    for p in Path(root).rglob("*software*versions*.yml"):
        found[str(p)] = p.read_text(errors="replace")[:5000]
    return {"root": root, "files": found}


# --------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("machine")
    s = sub.add_parser("interpreter"); s.add_argument("command")
    s = sub.add_parser("package"); s.add_argument("interpreter"); s.add_argument("names", nargs="+")
    s = sub.add_parser("exe"); s.add_argument("name"); s.add_argument("--via")
    s = sub.add_parser("container"); s.add_argument("image")
    s = sub.add_parser("script"); s.add_argument("path")
    s = sub.add_parser("conda-env"); s.add_argument("name"); s.add_argument("--filter", nargs="*", default=[])
    s = sub.add_parser("versions-yml"); s.add_argument("dir")
    a = ap.parse_args(argv)

    if a.cmd == "machine":
        emit(machine_snapshot())
    elif a.cmd == "interpreter":
        emit(interpreter(a.command))
    elif a.cmd == "package":
        emit(package(a.interpreter, a.names))
    elif a.cmd == "exe":
        emit(exe(a.name, a.via))
    elif a.cmd == "container":
        emit(container(a.image))
    elif a.cmd == "script":
        emit(script(a.path))
    elif a.cmd == "conda-env":
        emit(conda_env(a.name, a.filter))
    elif a.cmd == "versions-yml":
        emit(versions_yml(a.dir))


if __name__ == "__main__":
    main()
