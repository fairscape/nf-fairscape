# letters-chain example

A three-step pipeline showing a full provenance chain with saved intermediates:

1. `MAKE_LIST` — takes a number (`--n`, default 8) and writes the first *n*
   letters of the alphabet to `letters.txt`
2. `REVERSE` — reverses the list into `reversed.txt`
3. `SPLIT_HALVES` — divides the reversed list into `first_half.txt` and
   `second_half.txt`

Every step publishes its output to `results/`, so each intermediate file becomes
an `EVI:Dataset` in the crate and the Computations chain together through them:

```
                    MAKE_LIST ──generated──> letters.txt
letters.txt  ──used──> REVERSE ──generated──> reversed.txt
reversed.txt ──used──> SPLIT_HALVES ──generated──> first_half.txt, second_half.txt
```

Each Computation also carries `isPartOf` → the run-level Computation, and each
Dataset carries the inverse `generatedBy` edge back to its producer.

```bash
nextflow run . -plugins nf-fairscape@0.1.0            # default: 8 letters
nextflow run . -plugins nf-fairscape@0.1.0 --n 12     # or pick a length

python3 -m json.tool results/ro-crate-metadata.json | less
```

Note that the input number itself is a scalar (`val`) input, so it does not appear
as a Dataset — it shows up in the run Computation's `parameter` list (`n: 8`) and
in the `MAKE_LIST` command (`head -n 8`).

Print the chain from the crate:

```bash
python3 - <<'EOF'
import json
g = json.load(open('results/ro-crate-metadata.json'))['@graph']
name = {n['@id']: n.get('name','?') for n in g}
for n in g:
    if 'Computation' in str(n['@type']):
        used = ', '.join(name[r['@id']] for r in n.get('usedDataset', []))
        gen  = ', '.join(name[r['@id']] for r in n.get('generated', []))
        print(f"{n['name']:35} used: [{used}]  generated: [{gen}]")
EOF
```
