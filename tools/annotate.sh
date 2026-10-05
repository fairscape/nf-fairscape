#!/usr/bin/env bash
#
# Annotate a Nextflow workflow for nf-fairscape without opening an interactive session:
# runs the `fairscape-annotate` skill headlessly with Claude Code and leaves
# <workflow>/fairscape-annotations.config and <workflow>/.fairscape-annotate/report.md.
#
#   tools/annotate.sh <workflow-dir|main.nf> [skill args...]
#
#   tools/annotate.sh examples/cellmaps --audit
#   tools/annotate.sh examples/nf-core/demo --remote https://raw.githubusercontent.com/nf-core/demo/1.2.0
#   tools/annotate.sh /path/to/pipeline --config run.config --crate results/ro-crate-metadata.json
#
# Needs the `claude` CLI (https://claude.com/claude-code) logged in. The skill and its two
# scout agents live in this repo's .claude/, so the session is started from the repo root
# and given access to the workflow directory. Probes are read-only version/metadata
# queries; the allow-list below is what they need. Set ANNOTATE_YOLO=1 to skip the
# permission checks entirely, or ANNOTATE_MODEL to pick a model.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
if [[ $# -lt 1 ]]; then
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
fi
WF=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
[[ -d $WF ]] || WF=$(dirname "$WF")
shift

# Nextflow needs a JVM for `nextflow config`; the inventory degrades to a static parse
# without one, so this is best-effort.
if [[ -z ${JAVA_HOME:-} ]] && ! command -v java > /dev/null; then
    for cand in "$CONDA_PREFIX" "$HOME/anaconda3/envs/cellmap" "$HOME/miniconda3/envs/cellmap"; do
        [[ -n $cand && -x $cand/bin/java ]] && export JAVA_HOME=$cand && break
    done
fi

ALLOW=(
    "Read" "Glob" "Grep" "Agent" "WebFetch" "WebSearch" "Write" "Edit"
    "Bash(python3:*)" "Bash(python:*)" "Bash(conda:*)" "Bash(nextflow:*)" "Bash(git:*)"
    "Bash(mkdir:*)" "Bash(ls:*)" "Bash(cat:*)" "Bash(head:*)" "Bash(which:*)"
    "Bash(docker image inspect:*)" "Bash(podman image inspect:*)"
)
PERM=(--permission-mode acceptEdits --allowedTools "${ALLOW[@]}")
[[ ${ANNOTATE_YOLO:-0} == 1 ]] && PERM=(--dangerously-skip-permissions)
MODEL=()
[[ -n ${ANNOTATE_MODEL:-} ]] && MODEL=(--model "$ANNOTATE_MODEL")

export NF_FAIRSCAPE_HOME=$ROOT
cd "$ROOT"
# the prompt goes in on stdin: the variadic --allowedTools/--add-dir options would swallow
# a trailing positional argument
printf '%s\n' "/fairscape-annotate $WF $*" | exec claude -p "${PERM[@]}" "${MODEL[@]}" --add-dir "$WF"
