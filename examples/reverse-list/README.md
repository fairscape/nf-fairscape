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

Optionally validate against the `fairscape_models` schema:

```bash
PYTHONPATH=/path/to/fairscape_models python3 ../../nf-fairscape-test/validate_crate.py results/ro-crate-metadata.json
```
