---
name: fairscape-annotate
description: Point at a Nextflow workflow and produce its nf-fairscape annotations automatically -- one `ext.fairscape` software record per process (name, version, author, URL, description, keywords, all taken from the installed software and the workflow source) and the run-level `fairscape.metadata` (Croissant RAI, ethics, attribution, the compute it ran on). Writes a `-c` config plus an evidence report. Use when a user wants a pipeline annotated, audited against its existing annotations, or its AI-Ready score raised without typing metadata by hand.
argument-hint: <workflow-dir|main.nf> [--config extra.config] [--crate ro-crate-metadata.json] [--results DIR] [--remote RAW_URL_BASE] [--audit] [--out fairscape-annotations.config]
---

# fairscape-annotate

You are the annotator for nf-fairscape. The human used to open every process, look up what
tool it runs, find its version, and type an `ext fairscape:` block; then write the RAI and
ethics statements for the run. You do that instead, from evidence, and leave the human a
short list of the things only they can know.

The deliverable is two files next to the workflow:

- `fairscape-annotations.config` -- layers onto the run with `-c`; nothing in the pipeline
  is modified
- `.fairscape-annotate/report.md` -- every value with the evidence it came from, and the
  REVIEW list

Every key you emit is one the plugin understands (see `docs/CONFIGURATION.md`, sections
"ext fairscape" and "fairscape.metadata"). Nothing else survives into the crate, so do not
invent keys.

## Ground rules

1. **Evidence or omit.** A field with no evidence is left out and goes on the REVIEW list.
   Never write "unknown", "TODO", "N/A", or a guess dressed as a fact. The validator rejects
   placeholders.
2. **Versions come from the environment the pipeline runs in**, never from the internet:
   the interpreter named in the config, the container tag, the conda spec, a `versions.yml`
   from a previous run, the crate of a previous run. The internet (repo page, PyPI, bio.tools)
   is only for URL, author, description, license, keywords.
3. **Read-only.** Probes are version/metadata queries with timeouts (`tools/annotate/probe_env.py`).
   Never run the pipeline, never install anything, never modify the workflow.
4. **Keep what the human wrote unless the evidence contradicts it.** An existing
   `ext fairscape:` or `fairscape.metadata` value is carried forward; a stale version is
   replaced and the difference reported. In `--audit` mode nothing is written except the
   report.
5. **Say what you could not settle**, once per item, in the REVIEW list and as a `// REVIEW:`
   comment in the config. Typical: principalInvestigator, funder, identifier (DOI), IRB
   details, a script with no author line.

## Where the tools are

`NF_FAIRSCAPE_ROOT` is the nf-fairscape checkout: the directory that contains this skill's
`.claude/` (use `git rev-parse --show-toplevel` from inside it, or `$NF_FAIRSCAPE_HOME` when
set). All helpers are under `$NF_FAIRSCAPE_ROOT/tools/annotate/`:

| Helper | Role |
| ------ | ---- |
| `inventory.py` | static parse of the workflow: processes, script bodies, containers, conda specs, params, manifest, existing annotations, README, machine snapshot; optional previous crate |
| `probe_env.py` | read-only probes: `interpreter`, `package`, `exe`, `container`, `script`, `conda-env`, `versions-yml`, `machine` |
| `validate.py` | contract check of the annotations JSON; `--config` parse-checks the rendered file with `nextflow config` |
| `render_config.py` | annotations JSON -> Groovy config + Markdown report |

If Nextflow needs `JAVA_HOME`, set it for the helper calls (the inventory falls back to a
static parse when `nextflow config` fails, so this is a quality improvement, not a blocker).

## Procedure

### 1. Inventory

```bash
WF=<workflow dir>          # or the directory of the main.nf given
OUT=$WF/.fairscape-annotate; mkdir -p $OUT
python3 $NF_FAIRSCAPE_ROOT/tools/annotate/inventory.py $WF \
    [-c extra.config] [--crate previous/ro-crate-metadata.json] [--remote RAW_URL_BASE] \
    -o $OUT/inventory.json
```

`--remote` is for a pipeline whose modules are not on disk (a copied-out nf-core `main.nf`):
give the raw-content base of the release, e.g. `https://raw.githubusercontent.com/nf-core/demo/1.2.0`.
If `--results DIR` was given, also run `probe_env.py versions-yml DIR` and keep the output
for the scouts.

Read the inventory with a short Python summary, not by dumping the file: process names,
file, executables, python modules, container candidates, conda spec, existing annotation
(and its source), interpreter hints, whether `nf-fairscape` is in `config.plugins`,
existing `fairscape.metadata` keys, machine snapshot.

### 2. Software units

Group processes that run the same software into one unit (same package, executable or
container). A unit is usually one process; the cellmaps pipelines are one package per
process, nf-core aliases (`FASTQC_RAW`, `FASTQC_TRIM`) are one unit with two selectors.

### 3. Fan out software scouts

Launch one `fairscape-software-scout` subagent per unit, **in parallel** (one message,
many Agent calls; batches of about eight if there are many units). Each gets, verbatim:

- the process record(s) from the inventory (as JSON: name, file, remote_file, line,
  directives incl. container_candidates and conda_environment, script, executables,
  python_modules, existing_ext_fairscape, and the matching `crate_software` entry if any)
- the interpreter hints and the machine snapshot's conda env list
- the `versions.yml` output for that process, if any
- the absolute path of `probe_env.py`
- the instruction to return exactly the JSON object described in its agent definition

Do not do the scouting yourself in the main context; the point of the fan-out is that each
tool gets a full investigation without flooding this conversation.

### 4. Dataset scout

Launch one `fairscape-dataset-scout` with: the inventory's `config` (params, manifest,
fairscape block including existing metadata), the README text, the workflow header comment
(first 60 lines of main.nf), the list of processes with each scout's `softwareName` and
`softwareDescription`, the machine snapshot, and any `--crate` root entity. It returns the
`root` object (shortcuts + `metadata`), `root_evidence`, `root_review`, and `compute`.

Run it after the software scouts return, so it can describe the pipeline in terms of the
tools actually identified.

### 5. Merge, validate, render

Save each software scout's JSON as `$OUT/scout_<PROCESS>.json` and the dataset scout's as
`$OUT/dataset.json`, then let the merge helper assemble the annotations document:

```bash
python3 $NF_FAIRSCAPE_ROOT/tools/annotate/merge.py --inventory $OUT/inventory.json \
    --scouts "$OUT/scout_*.json" --dataset $OUT/dataset.json -o $OUT/annotations.json
```

It keeps a human-written `ext` value wherever a scout said nothing (unless the scout's
`diff` says it omitted the key on purpose), uses the existing `fairscape.metadata` as the
base for the root and merges the dataset scout on top, lists every inventoried process
without a scout result under `skipped`, and records `plugin_enabled` (whether `nf-fairscape`
is already in the workflow's `plugins`). The contract it writes is documented in
`render_config.py`; per process, `diff` carries the field-level differences from the
existing annotation (`softwareVersion: '1.5.0' (existing) -> '1.6.0' (installed in /path/env)`).

Then:

```bash
python3 $NF_FAIRSCAPE_ROOT/tools/annotate/validate.py $OUT/annotations.json --inventory $OUT/inventory.json
python3 $NF_FAIRSCAPE_ROOT/tools/annotate/render_config.py $OUT/annotations.json \
    -o $WF/fairscape-annotations.config --report $OUT/report.md [--standalone]
python3 $NF_FAIRSCAPE_ROOT/tools/annotate/validate.py $OUT/annotations.json \
    --config $WF/fairscape-annotations.config --workflow $WF
```

`--standalone` when `plugin_enabled` is false in the merged document: it adds the plugin id
and `fairscape.file`/`overwrite`. Fix validator errors in the JSON and re-run; do
not hand-edit the rendered config.

In `--audit` mode stop after the report: write `$OUT/annotations.json` and `$OUT/report.md`
only, and summarise the diffs.

### 6. Report

Finish with, in this order:

1. one line: where the config and report are, and the `nextflow run ... -c` command
2. a table: process, softwareName, softwareVersion, confidence, and the version change if
   it differed from an existing annotation
3. the REVIEW list, verbatim from the JSON, grouped process / run-level
4. the compute that was documented (host, CPU, memory, GPU, engines) in one line, with the
   caveat that it describes the machine inventoried, not necessarily the one that will run

Do not paste the config into the chat; the human opens the file.

## Notes that save time

- `nextflow config -flat` resolves `params.python`-style interpreters and includeConfig
  chains; the inventory uses it when Nextflow is on the PATH.
- The `withName:` selector matches the simple process name, so `FASTQC` covers
  `NFCORE_DEMO:DEMO:FASTQC`; use a regex selector (`'FASTQC.*'`) for aliases only when the
  aliases run the same version.
- `softwareUrl` replaces the module-file `contentUrl`; only set it to the tool's home, never
  to a local path.
- `softwareFormat` is a media type. Python package -> `application/x-python`, JVM tool ->
  `application/java-archive`, compiled binary -> `application/x-executable`, shell/coreutils
  step -> `application/x-sh`, R -> `text/x-r`. Omit if unsure.
- A process that is pure orchestration (staging a file with `cp`, a `cat` of two inputs) is
  still software: name it for what it does, version it with the pipeline's `manifest.version`,
  format `application/x-sh`, and say so in `softwareDescription`.
