#!/usr/bin/env python3
"""Validate an nf-fairscape crate against the fairscape_models schema.

Usage: python validate_crate.py <path/to/ro-crate-metadata.json>
"""
import json
import sys

from fairscape_models.rocrate import ROCrateV1_2


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip())

    with open(sys.argv[1]) as f:
        metadata = json.load(f)

    crate = ROCrateV1_2.model_validate(metadata)

    # referential integrity: every @id reference must resolve within the graph
    graph = json.load(open(sys.argv[1]))['@graph']
    ids = {node['@id'] for node in graph}
    dangling = []
    for node in graph:
        for key in ('hasPart', 'usedDataset', 'usedSoftware', 'generated', 'generatedBy', 'isPartOf', 'about'):
            refs = node.get(key) or []
            refs = refs if isinstance(refs, list) else [refs]
            for ref in refs:
                if isinstance(ref, dict) and ref.get('@id', '').startswith('ark:') and ref['@id'] not in ids:
                    dangling.append(f"{node['@id']} .{key} -> {ref['@id']}")
    if dangling:
        sys.exit("DANGLING REFERENCES:\n  " + "\n  ".join(dangling))

    counts = {}
    for elem in crate.metadataGraph:
        counts[type(elem).__name__] = counts.get(type(elem).__name__, 0) + 1
    print(f"VALID: {sys.argv[1]}")
    for name, count in sorted(counts.items()):
        print(f"  {name}: {count}")


if __name__ == '__main__':
    main()
