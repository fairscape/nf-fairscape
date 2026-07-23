# reverse-list example

Minimal pipeline: builds a three-item list, reverses it with `tac`, and publishes
`reversed.txt`. The plugin writes the EVI RO-Crate next to the published output.

```bash
# from the repo root, install the plugin once
make install

# run the example
cd examples/reverse-list
nextflow run . -plugins nf-fairscape@0.1.0

# view the output and the crate
cat results/reversed.txt
python3 -m json.tool results/ro-crate-metadata.json | less
```

The crate contains one run-level Computation, one `REVERSE` task Computation, the
`list.txt` input Dataset, and the published `reversed.txt` Dataset with a
`generatedBy` edge back to the task.

The `REVERSE` process carries an `ext fairscape: [...]` annotation describing the
actual tool it runs, so the Software entity in the crate is `tac` (GNU coreutils
8.32, with author, description, and URL) instead of the process-derived default.
See `docs/FAIRSCAPE.md` for the full list of `software*` keys.

Build the provenance graph for the published output (JSON + interactive HTML):

```bash
fairscape-cli build evidence-graph results <ark of reversed.txt from the crate>
```

Optionally validate against the `fairscape_models` schema:

```bash
PYTHONPATH=/path/to/fairscape_models python3 ../../nf-fairscape-test/validate_crate.py results/ro-crate-metadata.json
```
