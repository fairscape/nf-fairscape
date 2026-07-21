# FAIRSCAPE EVI RO-Crate format

`nf-fairscape` writes one `ro-crate-metadata.json` per run. The `@context` is the
FAIRSCAPE default context: [schema.org](https://schema.org/) as `@vocab`, plus the
[EVI](https://w3id.org/EVI#) and [W3C PROV-O](http://www.w3.org/ns/prov#) namespaces and
`@id`-typed EVI terms (`usedSoftware`, `usedDataset`, `generated`, `generatedBy`).

## Graph structure

| Node | `@type` | Source in Nextflow |
| ---- | ------- | ------------------ |
| `ro-crate-metadata.json` descriptor | `CreativeWork` | `conformsTo` [RO-Crate 1.2](https://w3id.org/ro/crate/1.2), `about` → root |
| Root crate | `["Dataset", "EVI#ROCrate"]` | `conformsTo` [FAIRSCAPE profile 0.1](https://w3id.org/fairscape/profile/0.1); name/description/version from workflow manifest, `hasPart` lists every node below |
| Run Computation | `["prov:Activity", "EVI#Computation"]` | The whole run: `command` = launch command line, `parameter` = folded params, `usedSoftware` = workflow script + Nextflow engine, `usedDataset` = external workflow inputs, `generated` = published/declared outputs. Extra keys: `startTime`, `endTime`, `nextflowVersion`, `identifier` (session id) |
| Task Computations | `["prov:Activity", "EVI#Computation"]` | One per successful task: `command` = task script, `usedSoftware` = its process Software, `usedDataset` = task input files, `generated` = task output files, `isPartOf` → run Computation. Extra keys: `containerImage`, `identifier` (task hash) |
| Workflow Software | `["prov:Entity", "EVI#Software"]` | The main workflow script (`contentUrl` = repository or normalized path); referenced only by the run Computation |
| Nextflow Software | `["prov:Entity", "EVI#Software"]` | The engine itself, versioned; referenced only by the run Computation |
| Process Software | `["prov:Entity", "EVI#Software"]` | One per process (e.g. `REVERSE`): `description` = the process body source as written in the workflow (unresolved variables — the template; the resolved command lives on each Computation), `contentUrl` = the script/module file defining it, `isPartOf` → workflow Software. Each task Computation's `usedSoftware` points here |
| Datasets | `["prov:Entity", "EVI#Dataset"]` | One per unique file (workflow inputs, task outputs, published files): `format` = MIME type or extension, `generatedBy` → producing Computation (inverse of `generated`), `contentUrl` = crate-relative path for published files, normalized path/URL otherwise, `contentSize` in bytes |

Dataflow between steps emerges from shared Dataset identifiers: a task consuming another
task's output references the same `EVI:Dataset` ARK. Prospective-workflow entities
(WRROC's `FormalParameter`, `HowToStep`, `ControlAction`, …) have no EVI equivalent and
are intentionally not modeled.

## ARK minting

`ark:{naan}/{prefix}-{slug(name)}-{sha1(sourceId)[0:7]}`, where the hashed source id is:

| Entity | Prefix | Hashed source id |
| ------ | ------ | ---------------- |
| Root crate | `rocrate` | session id |
| Run Computation | `computation` | session id + `#run` |
| Task Computation | `computation` | task hash |
| Workflow Software | `software` | normalized script path + version |
| Process Software | `software` | defining script path + `#` + process name |
| Nextflow Software | `software` | `nextflow-<version>` |
| Dataset | `dataset` | normalized file path |

Identifiers are deterministic: `-resume` reproduces the same ARKs for unchanged tasks and
files. A published file and its work-directory source share a single Dataset ARK.

## Validation

The acceptance test validates emitted crates with the
[`fairscape_models`](https://github.com/fairscape/fairscape-models) pydantic schema
(`ROCrateV1_2.model_validate`) plus a referential-integrity check that every `ark:` reference
resolves within the graph. See `nf-fairscape-test/validate_crate.py` and `make verify`.
