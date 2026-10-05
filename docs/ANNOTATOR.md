# The annotator

Nextflow knows what ran. The plugin records it. What nobody records unless a human types
it is *what the tool inside each process is* -- its name, version, author, home -- and what
the run is *as a dataset*: where the inputs came from, what could bias them, what the
outputs are fit for, who to credit. Those are the `ext fairscape:` block per process and the
`fairscape.metadata` map ([CONFIGURATION.md](CONFIGURATION.md#adding-your-own-metadata)),
and until now every example in this repo had them written by hand.

The annotator writes them from evidence instead. Point it at a workflow; it inventories the
processes, sends one agent per tool to find out what that tool is and which version is
actually installed where the pipeline runs, sends one agent to describe the run, and
renders a config you layer on with `-c`. What it could not establish -- the PI, the funder,
the DOI -- it leaves as a `// REVIEW:` comment for you, instead of inventing a value.

```
tools/annotate.sh examples/cellmaps
```

```
examples/cellmaps/
  fairscape-annotations.config      # -> nextflow run main.nf -c fairscape-annotations.config
  .fairscape-annotate/
    inventory.json                  # what the parser saw
    annotations.json                # what the agents concluded, with evidence
    report.md                       # the same, readable: every value and its source
```

## What it is made of

It is a [Claude Code](https://claude.com/claude-code) skill with two subagents and four
plain-Python helpers. The helpers do everything deterministic; the agents do the reading,
probing and writing.

| Piece | Where | Does |
| ----- | ----- | ---- |
| `fairscape-annotate` skill | `.claude/skills/fairscape-annotate/SKILL.md` | the procedure: inventory, fan out, merge, validate, render, report |
| `fairscape-software-scout` agent | `.claude/agents/` | one per tool: identifies it, gets the *installed* version, name, author, URL, description; compares with any existing annotation |
| `fairscape-dataset-scout` agent | `.claude/agents/` | once per workflow: the RAI, ethics, attribution and compute statements |
| `inventory.py` | `tools/annotate/` | static parse of `main.nf` + included modules + config: processes, script bodies, containers, conda specs, params, manifest, existing annotations, README, a machine snapshot |
| `probe_env.py` | `tools/annotate/` | read-only probes: interpreter, package metadata, `--version`, container inspect, script header/git, conda env, `versions.yml` |
| `merge.py` | `tools/annotate/` | assembles the scouts' JSON into one annotations document: keeps human values the scouts did not contest, merges root metadata, lists un-scouted processes |
| `validate.py` | `tools/annotate/` | the contract: only keys the plugin understands, no placeholders, every process accounted for; parse-checks the rendered config with `nextflow config` |
| `render_config.py` | `tools/annotate/` | annotations JSON -> Groovy config + Markdown report |
| `tools/annotate.sh` | `tools/` | runs the skill headlessly (`claude -p`) with a read-only permission allow-list |

Nothing here touches the plugin. The output is ordinary configuration, the same
`process { withName: ... { ext.fairscape = [...] } }` and `fairscape { metadata = [...] }`
you would write yourself, so a pipeline annotated this way needs nothing installed to run.

## Running it

Interactively, from a Claude Code session in this repo:

```
/fairscape-annotate examples/cellmaps
/fairscape-annotate examples/cellmaps --audit
/fairscape-annotate examples/nf-core/demo --remote https://raw.githubusercontent.com/nf-core/demo/1.2.0
/fairscape-annotate /path/to/pipeline --config run.config --crate results/ro-crate-metadata.json
```

Headlessly, the same arguments through `tools/annotate.sh`. It needs the `claude` CLI
logged in, and a JVM for `nextflow config` (it looks in the usual conda places; without one
the inventory falls back to a static parse of the config).

| Argument | Effect |
| -------- | ------ |
| `--config FILE` | extra config, like `nextflow -c`: resolves `params.python` and friends the way your run does |
| `--crate FILE` | a previous run's `ro-crate-metadata.json`; its Software entities (with `toolVersions` versions and container digests) seed the scouts |
| `--results DIR` | a previous run's `work/` or `results/`; every `versions.yml` under it is handed to the scouts |
| `--remote URL` | raw-content base to fetch modules that are not on disk (a copied-out nf-core `main.nf`) |
| `--audit` | write the report only; compare with the existing annotations and do not touch the config |
| `--out FILE` | where the config goes (default `<workflow>/fairscape-annotations.config`) |

## What the agents may and may not do

The scouts run version and metadata queries and read files. They do not run the pipeline,
install anything, pull images, or edit the workflow. `tools/annotate.sh` enforces that with
a tool allow-list; set `ANNOTATE_YOLO=1` to lift it on a machine you trust.

The version rule matters most: **a version is taken from the environment the pipeline runs
in** -- the interpreter named in `params.python`, the container tag, the conda spec, a
`versions.yml` -- never from PyPI or GitHub. The internet is for the URL, the author, the
description. On the cellmaps example this is what caught two stale hand-written versions:
`cellmaps_coembedding` annotated as 1.5.0 with 1.6.0 installed, and
`cellmaps_generate_hierarchy` as 0.2.5 with 0.3.0.post1.

Everything the agents conclude carries an evidence entry (the command, file or URL it came
from) in `annotations.json` and `report.md`. Everything they could not conclude is a REVIEW
line. The validator refuses `TODO`, `unknown`, `N/A`, `latest`, an ext key the plugin does
not know, a root key the plugin manages, and an inventoried process that is neither
annotated nor explicitly skipped.

## The compute

The dataset scout records the machine the inventory ran on -- host, CPU, memory, GPUs,
container engines, Nextflow and Java, the Python environment with its interpreter and the
versions of the heavy libraries the tools import -- as `additionalProperty` entries on the
root, each stamped with the inventory date and host. That is the machine the annotator ran
on, which is usually but not necessarily the one the pipeline will run on; the config's
header says so. Container provenance proper (image digests per task) stays with the plugin's
`containerProvenance` option, which records what actually executed.

## Extending

- A new `ext.fairscape` key in the plugin: add it to `KNOWN_EXT_KEYS` in both
  `FairscapeRenderer.groovy` and `tools/annotate/validate.py`, and to the table in the
  software scout.
- A new root field: `ROOT_KEYS` / `RAI_KEYS` in `validate.py`, and the table in the dataset
  scout.
- A tool the scouts keep misidentifying: the inventory's `guess_executables` and
  `guess_python_modules` are heuristics over the script body; extend them, or give the
  process an explicit `softwareName` in an existing annotation and the scout will start
  from it.
