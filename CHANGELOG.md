# nf-fairscape changelog

## Unreleased

- One `EVI:Software` node per process (`isPartOf` the workflow-script Software, `contentUrl` = defining script/module file). Task Computations now reference their own process via `usedSoftware`; only the run-level Computation references the workflow script and the Nextflow engine.
- Process Software `description` is the process body source as written in the workflow (the unresolved template; the resolved command remains on each Computation).

## 0.1.0

Initial release, forked from [nf-prov](https://github.com/nextflow-io/nf-prov) v1.7.0.

- New `FairscapeRenderer` emitting a FAIRSCAPE EVI RO-Crate (`ro-crate-metadata.json`) conforming to RO-Crate 1.2, the FAIRSCAPE profile 0.1, and the EVI ontology.
- New flat `fairscape` config scope replacing `prov.formats.*`; every required EVI field has a fallback so a zero-config run produces a valid crate.
- Deterministic ARK identifier minting (`ark:{naan}/{prefix}-{slug}-{sha1[0:7]}`), stable across `-resume`.
- Removed the BCO, DAG, GEXF, and WRROC renderers.
- Successful native (`exec:`) tasks are recorded as Computations (upstream nf-prov drops them on fresh runs).
- Python validation oracle (`nf-fairscape-test/validate_crate.py`) against the `fairscape_models` schema.
