# Provenance

## Research software

LoOpS is a COS700 research prototype by Kagiso Tsiane, supervised by
Professor Stefan Gruner. Responsibility for review, attribution and research
claims remains with the author.

This source release excludes machine-local credentials, model caches and
unpublished raw experimental data. Checker results depend on the specified
encoding and toolchain; independent validation of every checker is not claimed.

## Benchmark sources

`benchmarks/source_manifest.json` preserves the selected upstream paths, revisions,
SHA-256 hashes, Git blob identifiers and available licence records.

| Collection | Pinned revision | Tasks |
| --- | --- | ---: |
| [Code2Inv](https://github.com/PL-ML/code2inv) | `336a7063ae0d6313a6993dfb897766d01d5ba41a` | 30 |
| [SV-Benchmarks](https://gitlab.com/sosy-lab/benchmarking/sv-benchmarks) | `7efe28dd29576b46927b7a34e8f742bd90966a75` | 12 |

Upstream program files are not committed in this release. The explicit setup
command retrieves their pinned bytes and checks their hashes. Code2Inv's pinned
revision has no root licence file; public availability is not treated as permission
to redistribute it. SV-Benchmarks assets have their own notices and licence terms.
The imported manifest contains historical import-stage readiness notes; execution
and checker outcomes, not those old notes, determine a run's measured results.

Code2Inv reference conditions are mathematical-integer SMT obligations. Native
SV-COMP checks use the declared ILP32 C profile, including its safety requirements.
Neither source collection supplies a universal official invariant answer key for
this application. Local reference predicates and test fixtures are not official
benchmark solutions and are not inserted into primary source-generation prompts.

## Textbook adaptations

The five textbook cases are attributed Python-style adaptations of:

D. G. Kourie and B. W. Watson, *The Correctness-by-Construction Approach to
Programming*, Springer, 2012. https://doi.org/10.1007/978-3-642-27919-5

They cover Linear Search, Finding the Maximal Element, Binary Search, Pattern
Matching and Exponentiation. Their metadata records assumptions and departures
from the supplied source pages. Pattern Matching includes a disclosed final-start
boundary adjustment; Exponentiation specialises the real-base problem to integers.
Book scans and photographs are not part of this release. References to source
photographs are provenance pointers, not runtime dependencies. Review the rights
applicable to adapted material before public redistribution.

## Third-party tools

Python packages, CBMC, Z3, Zig, Ollama and model weights retain their own terms.
Model identifiers and hashes identify experimental artifacts, not redistribution
rights, verified training provenance or commercial licence clearance. No model
weights or checker executables are included in the source folder.
