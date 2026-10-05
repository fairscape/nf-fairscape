#!/usr/bin/env python3
"""
Inventory a Nextflow workflow for the nf-fairscape annotator.

Static analysis only -- nothing is executed. Walks main.nf and every locally
included module, and reads nextflow.config (plus any -c overrides), to produce
one JSON document describing:

  * every process: file, directives (container, conda, label, tag, publishDir),
    the script/shell body, the executables and Python modules it appears to
    call, and any `ext fairscape:` annotation it already carries
  * params, manifest, and the existing `fairscape { }` block (metadata keys)
  * interpreter hints (params that look like `conda run -n X python`)
  * the README next to the workflow, if any
  * optionally, the Software entities of an existing crate, keyed by process
  * a snapshot of the machine (CPU, memory, GPUs, engines, Nextflow/Java)

Usage:
    inventory.py <workflow-dir | main.nf> [-c extra.config ...]
                 [--crate ro-crate-metadata.json] [--remote RAW_URL_BASE]
                 [--no-nextflow] [-o inventory.json]

`--remote` resolves includes that are not on disk (a copied-out nf-core main.nf)
against a raw-content URL base, e.g.
    --remote https://raw.githubusercontent.com/nf-core/demo/1.2.0
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

DIRECTIVE_SECTION_HEADS = ("input:", "output:", "script:", "shell:", "exec:", "when:", "stub:", "topic:")
KNOWN_EXT_KEYS = ["softwareName", "softwareVersion", "softwareAuthor",
                  "softwareDescription", "softwareUrl", "softwareFormat", "softwareKeywords"]


# --------------------------------------------------------------------------- lexing
def strip_comments(text: str) -> str:
    """Blank out // and /* */ comments while preserving string contents and offsets."""
    out = []
    i, n = 0, len(text)
    state = None  # None | "'" | '"' | "'''" | '"""'
    while i < n:
        c = text[i]
        if state is None:
            if text.startswith("/*", i):
                j = text.find("*/", i + 2)
                j = n if j < 0 else j + 2
                out.append(re.sub(r"[^\n]", " ", text[i:j]))
                i = j
                continue
            if text.startswith("//", i):
                j = text.find("\n", i)
                j = n if j < 0 else j
                out.append(" " * (j - i))
                i = j
                continue
            for q in ('"""', "'''", '"', "'"):
                if text.startswith(q, i):
                    state = q
                    out.append(q)
                    i += len(q)
                    break
            else:
                out.append(c)
                i += 1
        else:
            if c == "\\" and i + 1 < n:
                out.append(text[i:i + 2])
                i += 2
                continue
            if text.startswith(state, i):
                out.append(state)
                i += len(state)
                state = None
                continue
            out.append(c)
            i += 1
    return "".join(out)


def match_brace(clean: str, open_idx: int) -> int:
    """Index of the } matching the { at open_idx, on comment-stripped text (strings respected)."""
    depth = 0
    i, n = open_idx, len(clean)
    state = None
    while i < n:
        c = clean[i]
        if state is None:
            for q in ('"""', "'''", '"', "'"):
                if clean.startswith(q, i):
                    state = q
                    i += len(q)
                    break
            else:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        return i
                i += 1
        else:
            if c == "\\":
                i += 2
                continue
            if clean.startswith(state, i):
                i += len(state)
                state = None
                continue
            i += 1
    return n - 1


# --------------------------------------------------------------------------- groovy literals
def parse_groovy_literal(src: str):
    """Best-effort Groovy literal -> Python: strings, numbers, booleans, null, lists, maps."""
    src = src.strip()
    pos = [0]

    def skip_ws():
        while pos[0] < len(src) and src[pos[0]] in " \t\r\n,":
            pos[0] += 1

    def parse_value():
        skip_ws()
        if pos[0] >= len(src):
            return None
        c = src[pos[0]]
        if c == "[":
            return parse_collection()
        if c in "\"'":
            return parse_string()
        m = re.match(r"[-+]?\d+(\.\d+)?", src[pos[0]:])
        if m:
            pos[0] += m.end()
            return float(m.group()) if m.group(1) else int(m.group())
        m = re.match(r"[A-Za-z_][\w.]*", src[pos[0]:])
        if m:
            pos[0] += m.end()
            word = m.group()
            return {"true": True, "false": False, "null": None}.get(word, {"$expr": word})
        pos[0] += 1
        return None

    def parse_string():
        q = src[pos[0]]
        triple = src.startswith(q * 3, pos[0])
        quote = q * 3 if triple else q
        pos[0] += len(quote)
        buf = []
        while pos[0] < len(src):
            if src[pos[0]] == "\\" and pos[0] + 1 < len(src):
                nxt = src[pos[0] + 1]
                buf.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
                pos[0] += 2
                continue
            if src.startswith(quote, pos[0]):
                pos[0] += len(quote)
                return "".join(buf)
            buf.append(src[pos[0]])
            pos[0] += 1
        return "".join(buf)

    def parse_key():
        skip_ws()
        if src[pos[0]] in "\"'":
            return parse_string()
        m = re.match(r"[\w.@-]+", src[pos[0]:])
        if not m:
            raise ValueError(f"bad map key at {pos[0]}: {src[pos[0]:pos[0] + 20]!r}")
        pos[0] += m.end()
        return m.group()

    def parse_collection():
        pos[0] += 1  # [
        skip_ws()
        if src.startswith(":", pos[0]):
            pos[0] += 2
            return {}
        if src.startswith("]", pos[0]):
            pos[0] += 1
            return []
        # decide map vs list by looking for `key :` before the first comma/close at depth 0
        save = pos[0]
        is_map = False
        try:
            k = parse_key()
            skip_ws()
            if src.startswith(":", pos[0]) and k is not None:
                is_map = True
        except Exception:
            pass
        pos[0] = save
        if is_map:
            result = {}
            while pos[0] < len(src):
                skip_ws()
                if src.startswith("]", pos[0]):
                    pos[0] += 1
                    return result
                k = parse_key()
                skip_ws()
                pos[0] += 1  # :
                result[k] = parse_value()
            return result
        result = []
        while pos[0] < len(src):
            skip_ws()
            if src.startswith("]", pos[0]):
                pos[0] += 1
                return result
            result.append(parse_value())
        return result

    try:
        return parse_value()
    except Exception:
        return {"$unparsed": src}


def extract_bracket_literal(clean: str, start: int) -> tuple[str, int]:
    """From clean[start] == '[' return (literal text, end index exclusive)."""
    depth = 0
    i = start
    state = None
    while i < len(clean):
        c = clean[i]
        if state is None:
            for q in ('"""', "'''", '"', "'"):
                if clean.startswith(q, i):
                    state = q
                    i += len(q)
                    break
            else:
                if c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                    if depth == 0:
                        return clean[start:i + 1], i + 1
                i += 1
        else:
            if c == "\\":
                i += 2
                continue
            if clean.startswith(state, i):
                i += len(state)
                state = None
                continue
            i += 1
    return clean[start:], len(clean)


# --------------------------------------------------------------------------- processes
def split_sections(body: str) -> dict:
    """Split a process body into directives / input / output / script / stub ... by section heads."""
    lines = body.split("\n")
    sections = {"directives": []}
    current = "directives"
    for line in lines:
        stripped = line.strip()
        head = next((h for h in DIRECTIVE_SECTION_HEADS if stripped.startswith(h)), None)
        if head and (stripped == head or stripped.startswith(head)):
            current = head[:-1]
            sections.setdefault(current, [])
            rest = stripped[len(head):].strip()
            if rest:
                sections[current].append(rest)
            continue
        sections.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip("\n") for k, v in sections.items()}


DIRECTIVE_RE = re.compile(r"^\s*(container|conda|tag|label|publishDir|cpus|memory|time|accelerator|module|spack)\b\s*(.*)$")


IMAGE_REF_RE = re.compile(r"""['"]([A-Za-z0-9][\w.\-/:@+]*(?:[:/][\w.\-:@+]+)+)['"]""")


def parse_directives(text: str, raw_text: str) -> dict:
    d = {"labels": []}
    lines = text.split("\n")
    for idx, line in enumerate(lines):
        m = DIRECTIVE_RE.match(line)
        if not m:
            continue
        name, rest = m.group(1), m.group(2).strip()
        # a directive value may span lines (nf-core's container ternary); take text up to the
        # next directive-looking line
        block = [rest]
        for nxt in lines[idx + 1:]:
            if DIRECTIVE_RE.match(nxt) or not nxt.strip():
                break
            block.append(nxt.strip())
        full = " ".join(block)
        val = rest
        sm = re.match(r"""^(?:\(\s*)?(['"])(.*?)\1""", rest)
        if sm:
            val = sm.group(2)
        if name == "label":
            d["labels"].append(val)
        elif name == "container":
            d["container"] = full if "${" in val or not sm else val
            refs = [r for r in IMAGE_REF_RE.findall(full) if not r.startswith("${")]
            if refs:
                d["container_candidates"] = refs
        elif name in ("conda", "tag", "cpus", "memory", "time", "accelerator", "module", "spack"):
            d[name] = val
        elif name == "publishDir":
            d.setdefault("publishDir", []).append(rest)
    # ext fairscape: [...]  (multi-line) -- take it from the raw text so string contents survive
    m = re.search(r"ext\s+fairscape\s*:\s*\[", raw_text)
    if m:
        lit, _ = extract_bracket_literal(raw_text, m.end() - 1)
        d["ext_fairscape"] = parse_groovy_literal(strip_comments(lit))
    return d


EXE_SKIP = {"set", "export", "cd", "mkdir", "rm", "cp", "mv", "ln", "cat", "echo", "touch", "if", "then", "else", "fi",
            "for", "do", "done", "while", "true", "false", "test", "[", "[[", "exit", "printf", "read", "shift",
            "eval", "exec", "source", ".", "local", "return", "case", "esac", "in", "wait", "trap", "def", "ls",
            "head", "tail", "sed", "awk", "grep", "cut", "sort", "uniq", "tr", "wc", "tee", "xargs", "find", "gzip",
            "gunzip", "zcat", "tar", "unzip", "date", "basename", "dirname", "sleep", "chmod", "which", "env", "rmdir"}


def shell_lines(script: str) -> list[str]:
    """The lines inside the script's triple-quoted block(s); the whole thing when there are none."""
    blocks = re.findall(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'', script, flags=re.S)
    if not blocks:
        return script.split("\n")
    out = []
    for a, b in blocks:
        out.extend((a or b).split("\n"))
    return out


def guess_executables(script: str) -> list[str]:
    """First token of each command line in a script body, minus shell noise. Heuristic."""
    exes = []
    for raw in shell_lines(script):
        line = raw.strip()
        if not line or line.startswith(("#", "cat <<", "END_VERSIONS", "def ", "return ")):
            continue
        # drop leading env assignments and `\` continuation artefacts
        line = re.sub(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)+", "", line)
        # split pipelines / && chains
        for seg in re.split(r"\s*(?:\|\||&&|\||;)\s*", line):
            seg = seg.strip()
            if not seg:
                continue
            tok = seg.split()[0]
            tok = tok.strip("\\()")
            if not tok or tok in EXE_SKIP or tok.startswith(("$", "-", '"', "'", "<", ">", "`", "{", "}", "|")):
                continue
            if tok.endswith(":"):
                continue
            exes.append(tok)
    seen, out = set(), []
    for e in exes:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def guess_python_modules(script: str) -> list[str]:
    """Python scripts and packages a script body appears to run: `pkg/pkg/foocmd.py` -> pkg,
    `some/dir/tool.py` -> tool.py, `python -m pkg` -> pkg, plus imports in inline python."""
    mods = set()
    text = "\n".join(shell_lines(script))
    for m in re.finditer(r"([\w./$@{}-]+\.py)\b", text):
        parts = [p for p in m.group(1).split("/") if p and not p.startswith("$")]
        stem = parts[-1]
        mods.add(stem)
        # an installed package laid out as <pkg>/<pkg>/<pkg>cmd.py, the cellmaps convention
        dirs = parts[:-1]
        for i in range(len(dirs) - 1):
            if dirs[i] == dirs[i + 1] and re.match(r"^[a-z][\w]*$", dirs[i]):
                mods.add(dirs[i])
        if dirs and stem.startswith(dirs[-1]) and re.match(r"^[a-z][\w]*$", dirs[-1]):
            mods.add(dirs[-1])
    for m in re.finditer(r"python3?\s+-m\s+([\w.]+)", text):
        mods.add(m.group(1).split(".")[0])
    for m in re.finditer(r"(?m)^\s*(?:import|from)\s+([A-Za-z_][\w]*)", text):
        mods.add(m.group(1))
    return sorted(mods)


def parse_processes(path: Path, text: str) -> list[dict]:
    clean = strip_comments(text)
    procs = []
    for m in re.finditer(r"(?m)^[ \t]*process\s+([A-Za-z_][\w]*)\s*\{", clean):
        name = m.group(1)
        open_idx = m.end() - 1
        close_idx = match_brace(clean, open_idx)
        body_clean = clean[open_idx + 1:close_idx]
        body_raw = text[open_idx + 1:close_idx]
        sections = split_sections(body_clean)
        raw_sections = split_sections(body_raw)
        directives = parse_directives(sections.get("directives", ""), raw_sections.get("directives", ""))
        script = raw_sections.get("script") or raw_sections.get("shell") or raw_sections.get("exec") or ""
        line_no = text[:m.start()].count("\n") + 1
        procs.append({
            "name": name,
            "file": str(path),
            "line": line_no,
            "directives": directives,
            "input": raw_sections.get("input", ""),
            "output": raw_sections.get("output", ""),
            "script": script.strip("\n"),
            "script_kind": "script" if "script" in raw_sections else ("shell" if "shell" in raw_sections else ("exec" if "exec" in raw_sections else "none")),
            "executables": guess_executables(script),
            "python_modules": guess_python_modules(script),
            "existing_ext_fairscape": directives.pop("ext_fairscape", None),
        })
    return procs


# --------------------------------------------------------------------------- includes
INCLUDE_RE = re.compile(r"include\s*\{([^}]*)\}\s*from\s*(['\"])([^'\"]+)\2")


def resolve_include(base: Path, target: str) -> Path | None:
    p = (base.parent / target)
    for cand in (p, p.with_suffix(".nf") if p.suffix != ".nf" else p, p / "main.nf"):
        if cand.is_file():
            return cand.resolve()
    return None


def fetch_remote(remote: str, rel: str, cache: Path, suffixes=("", ".nf", "/main.nf")) -> tuple[Path, str] | None:
    """Fetch rel(+suffix) from the raw URL base into cache; returns (local path, rel with suffix)."""
    rel = rel.lstrip("./")
    for suffix in suffixes:
        target = rel + suffix
        local = cache / target
        if local.is_file():
            return local, target
        url = f"{remote.rstrip('/')}/{target}"
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                data = r.read().decode("utf-8", "replace")
        except Exception:
            continue
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(data)
        return local, target
    return None


def conda_environment(proc: dict, remote: str | None, rel_for_remote: str | None, cache: Path) -> dict | None:
    """The environment.yml a `conda "${moduleDir}/environment.yml"` directive points at, parsed loosely."""
    conda = proc["directives"].get("conda")
    if not conda or not conda.endswith((".yml", ".yaml")):
        return None
    module_dir = Path(proc["file"]).parent
    name = conda.split("/")[-1]
    local = module_dir / name
    if not local.is_file() and remote and rel_for_remote:
        got = fetch_remote(remote, os.path.join(os.path.dirname(rel_for_remote), name), cache, suffixes=("",))
        local = got[0] if got else local
    if not local.is_file():
        return {"file": conda, "missing": True}
    env = {"file": str(local), "channels": [], "dependencies": []}
    section = None
    for line in local.read_text(errors="replace").splitlines():
        s = line.strip()
        if s.startswith("channels:"):
            section = "channels"
        elif s.startswith("dependencies:"):
            section = "dependencies"
        elif s.startswith("- ") and section:
            env[section].append(s[2:].strip().strip("'\""))
        elif s and not s.startswith("#") and not s.startswith("-"):
            section = None
    return env


def walk_workflow(entry: Path, remote: str | None, cache: Path) -> tuple[list[dict], list[dict]]:
    seen: set[Path] = set()
    processes: list[dict] = []
    files: list[dict] = []
    unresolved: list[dict] = []

    def visit(path: Path, rel_for_remote: str | None):
        if path in seen:
            return
        seen.add(path)
        text = path.read_text(errors="replace")
        procs = parse_processes(path, text)
        for p in procs:
            env = conda_environment(p, remote, rel_for_remote, cache)
            if env:
                p["directives"]["conda_environment"] = env
            if rel_for_remote and remote:
                p["remote_file"] = f"{remote.rstrip('/')}/{rel_for_remote}"
        processes.extend(procs)
        files.append({"file": str(path), "processes": [p["name"] for p in procs]})
        clean = strip_comments(text)
        for inc in INCLUDE_RE.finditer(clean):
            names = [n.strip() for n in inc.group(1).replace("\n", ";").split(";") if n.strip()]
            target = inc.group(3)
            if target.startswith("plugin/"):
                continue
            child = resolve_include(path, target)
            child_rel = None
            if child is not None:
                try:
                    child_rel = os.path.relpath(child, entry.parent) if not path.is_relative_to(cache) else None
                except ValueError:
                    child_rel = None
                if child_rel and child_rel.startswith(".."):
                    child_rel = None
            if child is None and remote:
                # remote-relative: compose from where this file sits in the remote tree
                base_rel = os.path.dirname(rel_for_remote) if rel_for_remote else ""
                got = fetch_remote(remote, os.path.normpath(os.path.join(base_rel, target)), cache)
                if got:
                    child, child_rel = got
            if child is None:
                unresolved.append({"from": str(path), "target": target, "names": names})
                continue
            visit(child, child_rel)

    visit(entry.resolve(), "main.nf")
    for u in unresolved:
        files.append({"unresolved_include": u})
    return processes, files


# --------------------------------------------------------------------------- config
def parse_config_static(config_path: Path) -> dict:
    """Regex-level read of a nextflow.config: params.x = ..., params{}, manifest{}, fairscape{}, process{withName}."""
    text = config_path.read_text(errors="replace")
    clean = strip_comments(text)
    result = {"params": {}, "manifest": {}, "fairscape": {}, "process_ext": {}, "plugins": [], "includeConfig": []}

    for m in re.finditer(r"(?m)^\s*params\.([\w.]+)\s*=\s*(.+?)\s*$", clean):
        result["params"][m.group(1)] = parse_groovy_literal(m.group(2))

    def block(name):
        m = re.search(r"(?m)^\s*%s\s*\{" % re.escape(name), clean)
        if not m:
            return None
        end = match_brace(clean, m.end() - 1)
        return clean[m.end():end], text[m.end():end]

    def simple_assignments(body):
        out = {}
        for m in re.finditer(r"(?m)^\s*([\w.'\":-]+)\s*=\s*(.+?)\s*$", body):
            out[m.group(1).strip("'\"")] = parse_groovy_literal(m.group(2))
        return out

    b = block("params")
    if b:
        result["params"].update(simple_assignments(b[0]))
    b = block("manifest")
    if b:
        result["manifest"] = simple_assignments(b[0])
    b = block("fairscape")
    if b:
        fs_clean, fs_raw = b
        result["fairscape"] = simple_assignments(re.sub(r"metadata\s*=\s*\[.*", "", fs_clean, flags=re.S))
        mm = re.search(r"metadata\s*=\s*\[", fs_raw)
        if mm:
            lit, _ = extract_bracket_literal(fs_raw, mm.end() - 1)
            result["fairscape"]["metadata"] = parse_groovy_literal(strip_comments(lit))
    for m in re.finditer(r"withName\s*:\s*['\"]?([^'\"{\s]+)['\"]?\s*\{", clean):
        end = match_brace(clean, m.end() - 1)
        body = text[m.end():end]
        em = re.search(r"ext\.fairscape\s*=\s*\[", body)
        if em:
            lit, _ = extract_bracket_literal(body, em.end() - 1)
            result["process_ext"][m.group(1)] = parse_groovy_literal(strip_comments(lit))
    result["plugins"] = re.findall(r"id\s+['\"]([^'\"]+)['\"]", clean)
    result["includeConfig"] = re.findall(r"includeConfig\s+['\"]([^'\"]+)['\"]", clean)
    return result


def nextflow_flat_config(workflow_dir: Path, extra_configs: list[Path]) -> dict | None:
    nf = shutil.which("nextflow")
    if not nf:
        return None
    cmd = [nf, "-q"]
    for c in extra_configs:
        cmd += ["-c", str(c)]
    cmd += ["config", "-flat", str(workflow_dir)]
    env = dict(os.environ)
    env.setdefault("NXF_ANSI_LOG", "false")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    if r.returncode != 0:
        return {"error": (r.stderr or r.stdout).strip()[-2000:]}
    flat = {}
    for line in r.stdout.splitlines():
        if " = " not in line:
            continue
        k, v = line.split(" = ", 1)
        flat[k.strip()] = parse_groovy_literal(v.strip())
    return flat


INTERP_RE = re.compile(r"(conda\s+run\s+-n\s+\S+\s+python\S*|\S*/bin/python\S*|\bpython3?\b|micromamba\s+run\s+-n\s+\S+\s+python\S*)")


def interpreter_hints(params: dict, processes: list[dict]) -> list[dict]:
    hints = []
    for k, v in params.items():
        if isinstance(v, str) and INTERP_RE.search(v) and ("python" in v or "/bin/" in v):
            hints.append({"param": k, "value": v})
    for p in processes:
        for m in INTERP_RE.finditer(p["script"]):
            val = m.group(1)
            if val in ("python", "python3"):
                continue
            hints.append({"process": p["name"], "value": val})
    seen, out = set(), []
    for h in hints:
        key = h["value"]
        if key not in seen:
            seen.add(key)
            out.append(h)
    return out


# --------------------------------------------------------------------------- crate enrichment
def crate_software(crate_path: Path) -> dict:
    try:
        g = json.loads(crate_path.read_text())["@graph"]
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    out = {}
    for e in g:
        t = e.get("@type")
        t = t if isinstance(t, list) else [t]
        if any("Software" in str(x) for x in t):
            out[e.get("name")] = {k: e.get(k) for k in ("@id", "name", "version", "author", "contentUrl", "containerImage",
                                                        "containerDigest", "format", "keywords") if e.get(k) is not None}
    return out


# --------------------------------------------------------------------------- machine
def run(cmd: list[str], timeout=20) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr).strip() or None
    except Exception:  # noqa: BLE001
        return None


def machine_snapshot() -> dict:
    import platform
    snap = {
        "hostname": platform.node(),
        "os": platform.platform(),
        "cpu_count": os.cpu_count(),
        "cpu_model": None,
        "memory_gb": None,
        "gpus": [],
        "container_engines": {},
        "nextflow": run(["nextflow", "-v"]) if shutil.which("nextflow") else None,
        "java": None,
        "conda_envs": [],
    }
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                snap["cpu_model"] = line.split(":", 1)[1].strip()
                break
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                snap["memory_gb"] = round(int(line.split()[1]) / 1048576, 1)
                break
    except Exception:  # noqa: BLE001
        pass
    if shutil.which("nvidia-smi"):
        out = run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"])
        if out:
            snap["gpus"] = [dict(zip(("name", "memory", "driver"), [x.strip() for x in l.split(",")])) for l in out.splitlines() if l.strip()]
    for eng in ("docker", "podman", "singularity", "apptainer"):
        if shutil.which(eng):
            snap["container_engines"][eng] = run([eng, "--version"])
    java = shutil.which("java") or (os.path.join(os.environ["JAVA_HOME"], "bin", "java") if os.environ.get("JAVA_HOME") else None)
    if java and os.path.exists(java):
        snap["java"] = run([java, "-version"])
    if shutil.which("conda"):
        out = run(["conda", "env", "list"])
        if out:
            snap["conda_envs"] = [l.split()[0] for l in out.splitlines() if l and not l.startswith("#")]
    return snap


# --------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workflow", help="workflow directory or main.nf")
    ap.add_argument("-c", "--config", action="append", default=[], help="extra config file(s), like nextflow -c")
    ap.add_argument("--crate", help="an existing ro-crate-metadata.json from a previous run, for enrichment")
    ap.add_argument("--remote", help="raw URL base to fetch includes not present locally")
    ap.add_argument("--no-nextflow", action="store_true", help="skip `nextflow config -flat` resolution")
    ap.add_argument("--no-machine", action="store_true", help="skip the machine snapshot")
    ap.add_argument("-o", "--output", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)

    wf = Path(args.workflow).expanduser().resolve()
    if wf.is_dir():
        wf_dir = wf
        main_nf = None
        cfg = wf_dir / "nextflow.config"
        if cfg.is_file():
            static = parse_config_static(cfg)
            ms = static.get("manifest", {}).get("mainScript")
            if isinstance(ms, str) and (wf_dir / ms).is_file():
                main_nf = wf_dir / ms
        main_nf = main_nf or (wf_dir / "main.nf")
    else:
        main_nf, wf_dir = wf, wf.parent
    if not main_nf.is_file():
        sys.exit(f"no main script at {main_nf}")

    cache = Path(os.environ.get("NF_ANNOTATE_CACHE", str(wf_dir / ".fairscape-annotate" / "remote")))
    processes, files = walk_workflow(main_nf, args.remote, cache)

    configs = [wf_dir / "nextflow.config"] if (wf_dir / "nextflow.config").is_file() else []
    configs += [Path(c).expanduser().resolve() for c in args.config]
    static_cfg = {}
    for c in configs:
        part = parse_config_static(c)
        for k in ("params", "manifest", "fairscape", "process_ext"):
            static_cfg.setdefault(k, {}).update(part.get(k, {}))
        static_cfg.setdefault("plugins", []).extend(part.get("plugins", []))
        static_cfg.setdefault("includeConfig", []).extend(part.get("includeConfig", []))

    flat = None if args.no_nextflow else nextflow_flat_config(wf_dir, [Path(c) for c in args.config])
    if flat and "error" not in flat:
        params = {k[len("params."):]: v for k, v in flat.items() if k.startswith("params.")}
        manifest = {k[len("manifest."):]: v for k, v in flat.items() if k.startswith("manifest.")}
        fairscape = {k[len("fairscape."):]: v for k, v in flat.items() if k.startswith("fairscape.") and not k.startswith("fairscape.metadata")}
        meta = {}
        for k, v in flat.items():
            if k.startswith("fairscape.metadata."):
                meta[k[len("fairscape.metadata."):].strip("'\"")] = v
        if meta:
            fairscape["metadata"] = meta
        for k, v in flat.items():
            m = re.match(r"process\.'?withName:([^'.]+)'?\.ext\.fairscape", k)
            if m:
                static_cfg.setdefault("process_ext", {})[m.group(1)] = v
        static_cfg["params"] = {**static_cfg.get("params", {}), **params}
        static_cfg["manifest"] = {**static_cfg.get("manifest", {}), **manifest}
        static_cfg["fairscape"] = {**static_cfg.get("fairscape", {}), **fairscape}
        static_cfg["resolved_by"] = "nextflow config -flat"
    else:
        static_cfg["resolved_by"] = "static parse" + (f" (nextflow config failed: {flat['error'][:300]})" if flat else "")

    # attach config-side ext.fairscape to the processes it names
    for p in processes:
        cfg_ext = static_cfg.get("process_ext", {}).get(p["name"])
        if cfg_ext and not p.get("existing_ext_fairscape"):
            p["existing_ext_fairscape"] = cfg_ext
            p["existing_ext_source"] = "config"
        elif p.get("existing_ext_fairscape"):
            p["existing_ext_source"] = "process"

    readme = None
    for cand in ("README.md", "README.rst", "README.txt", "README"):
        if (wf_dir / cand).is_file():
            readme = (wf_dir / cand).read_text(errors="replace")[:20000]
            break

    inv = {
        "workflow": {"main": str(main_nf), "dir": str(wf_dir), "configs": [str(c) for c in configs], "files": files},
        "config": static_cfg,
        "interpreter_hints": interpreter_hints(static_cfg.get("params", {}), processes),
        "processes": processes,
        "readme": readme,
        "crate_software": crate_software(Path(args.crate)) if args.crate else None,
        "machine": None if args.no_machine else machine_snapshot(),
        "known_ext_keys": KNOWN_EXT_KEYS,
    }
    out = json.dumps(inv, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(out)
        n_annot = sum(1 for p in processes if p.get("existing_ext_fairscape"))
        print(f"{len(processes)} processes in {len([f for f in files if 'file' in f])} files "
              f"({n_annot} already annotated); config resolved by {static_cfg['resolved_by']}; -> {args.output}")
    else:
        print(out)


if __name__ == "__main__":
    main()
