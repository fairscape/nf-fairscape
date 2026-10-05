# letters-chain-slow example

The [letters-chain](../letters-chain) pipeline with a `sleep` at the top of each
of its three steps, so a run takes about 40 seconds instead of one. It exists to
be *watched*: pick "A Nextflow pipeline" under **Run a workflow live** in
RO-Crate Studio, point at this folder, and the log and the status bar move step
by step before the crate loads.

1. `MAKE_LIST` — sleeps, then writes the first *n* letters to `letters.txt`
2. `REVERSE` — sleeps, then reverses them into `reversed.txt`
3. `SPLIT_HALVES` — sleeps, then splits that into `first_half.txt` and `second_half.txt`

```bash
nextflow run . -plugins nf-fairscape@0.2.0                # 12 s per step
nextflow run . -plugins nf-fairscape@0.2.0 --pause 20     # slower
nextflow run . -plugins nf-fairscape@0.2.0 --pause 0      # same crate, no waiting
```

`--pause` is a scalar input like `--n`, so it shows up in the run Computation's
`parameter` list rather than as a Dataset. The crate is otherwise identical in
shape to the letters-chain one.
