# Experiment reproduction

## What this release can reproduce

The folder supplies the application, source selection manifest, trace samplers,
formal-checking adapters, current parsers and experiment runners. It can produce
new, inspectable runs after dependencies, upstream inputs and model artifacts are
installed. It is not the historical raw-data archive and cannot reconstruct the
report's exact tables without that separate evidence. Offline inference does not
rule out pretraining exposure to public examples.

The interface includes the native fenced-JSON parser correction. The source
batch runner defaults to the original direct-JSON admission policy; use
`--native-response-format corrected` explicitly to change it. The chosen policy
is written to the run manifest and must match when resuming. New outputs and parser-replay results
must be labelled separately from that historical score. Hardware changes and
model-runner versions can change outputs and timings even at temperature zero.

## Source benchmark pilot

The historical design has 42 programs, five model artifacts, three modes and
observation budgets 5, 10, 15, 20 and 25. Syntax-only is scheduled once per program,
not once per trace budget: `42 * 5 * (1 + 5 + 5) = 2310` scheduled treatments.

Positive comparison cohorts contain 27 Code2Inv and ten SV-COMP programs.
Three Code2Inv tasks and one SV-COMP task are unsafe controls. The css2003 task
has a specification-profile mismatch. Keep them visible but outside positive
proof denominators. Context exclusions and provider failures are not mathematical
counterexamples.

The source runner inventories all installed local models and requires a Gemma
artifact. Use a dedicated Ollama installation containing exactly the intended
model set, then compare names and full digests with `model_artifacts.json`.
It rejects non-loopback endpoints and cloud aliases. It can be expensive.
Run this batch workflow with the host-side Python installation and a host-side
Ollama server. The isolated container UI uses an internal service hostname;
that hostname is intentionally not accepted by this loopback-only batch runner.

Prepare the dataset, dependencies and native tools before invoking it. The
following command contacts model inventory endpoints and samples traces but
does not generate hypotheses:

```text
python scripts/run_source_pilot.py --output results/source_pilots/new_run --prepare-only
```

Inspect `manifest.json` and `prompt_inventory.json` before deliberately starting:

```text
python scripts/run_source_pilot_resilient.py --output results/source_pilots/new_run --smaller-models-first
```

Use the same arguments when resuming. Source/model/settings changes invalidate
the frozen run; create a new output directory instead. The execution wrappers
record context batching and persistence changes. Create `STOP_AFTER_CURRENT`
inside the run directory to stop at a saved-trial boundary. Do not run multiple
workers on one directory.

The recorded controls are temperature 0, seed 0, output cap 2048, maximum context
32768 or the model's smaller declared limit, and conservative byte-based context
preflight. Generation limits are 180 seconds for smaller models and 600 for Gemma.
Code2Inv uses a 90-second limit per solver query; native C uses 90 seconds shared
across the checker pipeline and unwind 20. Three trace sampling runs are requested;
they need not expose distinct inputs or unique states. Read actual manifests and
diagnostics rather than assuming every requested row adds information.

## Numeric loop-bound increments

These change the numerical template's program bound, rather than merely its
trace-observation limit. Plan a small sweep without making model calls:

```text
python scripts/run_research_experiment.py --suite scaling --templates transfer --bound-start 5 --bound-stop 25 --bound-step 5 --models qwen2.5-coder:1.5b --input-modes syntax-only trace-only syntax-plus-trace --repeats 1 --dry-run
```

Remove `--dry-run` only when ready to run inference. These locally defined
templates are not sourced textbook or Code2Inv problems. Keep results separate
from the 42-program source pilot.

## Textbook input sizes

The textbook runner can vary array lengths, or the exponent for Exponentiation:

```text
python scripts/run_research_experiment.py --suite examples --textbook-only --textbook-input-sizes 5 10 15 20 25 --models qwen2.5-coder:1.5b --input-modes syntax-only trace-only syntax-plus-trace --repeats 1 --dry-run
```

Changing sample size is not necessarily changing the program's symbolic bound.
Syntax-only prompts can remain identical across sizes. The supplied input
profiles are deterministic, not exhaustive. Historical context-assisted textbook
runs and current minimal/unassisted prompts are different treatments. Do not
claim the report completed a uniform textbook size sweep merely because the
runner supports one.

## Outcomes and interpretation

One successful response requires at least one candidate or admitted native
bundle satisfying the full proof contract. Multiple candidates do not count as
multiple successful trials. Record malformed output, invalid syntax, refutation,
inductive-but-insufficient results, native safety failures, checker limits,
provider errors and no-call exclusions separately.

Formal correctness and proof contribution beyond an all-True baseline are
different assessments. Do not call every proof useful or every unsuccessful
treatment a model reasoning failure. Keep raw prompts, responses and source
hashes with each run. A saved-output parser/checker replay is not a new model
experiment and must not silently replace original scores.
