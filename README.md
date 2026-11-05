# LoOpS

Local loop-invariant hypothesis generation with source-specific formal checking.
The application uses Streamlit, Python, local Ollama models, Z3 and CBMC.

## Included

- Editable programs, preconditions and postconditions.
- Syntax-only, trace-only and syntax-plus-trace input modes.
- Five attributed textbook adaptations and three numerical templates.
- A manifest for 30 Code2Inv and 12 SV-COMP tasks, with pinned revisions and hashes.
- Experiment runners, parser regression tests and an isolated container setup.

This repository contains runnable software, not the historical experiment archive.
It excludes model weights, raw research runs, reports, book scans and credentials.
See [reproduction scope](docs/EXPERIMENTS.md) and [source provenance](docs/PROVENANCE.md).

## Local setup

Use Python 3.14. Download the benchmark assets while connected to the internet:

```text
python scripts/fetch_benchmarks.py
```

The downloader verifies the 186 unique task and provenance files against the
manifest. Downloaded upstream files are ignored by Git and retain their upstream
notices. Read the recorded licensing information before using or redistributing them.

On Windows, from this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:LOOPS_LOCAL_ONLY = "1"
$env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"
.\.venv\Scripts\python -m streamlit run app.py --server.address=127.0.0.1 --browser.gatherUsageStats=false
```

On Linux/macOS, use `.venv/bin/python` and `export` for environment variables.
The local application opens at http://127.0.0.1:8501. It does not start an
experiment automatically.

Install Ollama separately and explicitly acquire the models you want to evaluate.
Record their full digests, not only their tags. The historical model identifiers
and digests are in `docs/model_artifacts.json`; availability of those exact
artifacts on another machine is not guaranteed. Setup does not download weights
or make generation calls.

Local SV-COMP checking requires CBMC and its GOTO tools. Set `CBMC_BIN` to their
directory. Native trace sampling also requires Zig; set `TRACE_ZIG_EXECUTABLE`
to its executable. Quantified native contracts require a Z3 command-line
executable, configured with `Z3_EXECUTABLE`. The Docker setup installs these tools.

## Verify without model calls

```text
python scripts/fetch_benchmarks.py --verify
python scripts/verify_installation.py
python -m unittest discover -s tests
```

The installation check validates all source hashes, inspects every source task,
checks a correct and an incorrect integer predicate, and exercises all three
source prompt modes. Add `--native` to check a native contract and compile a
native trace with the installed external tools. Tests use local fixtures or
test adapters; they do not evaluate actual models.

## Containers and offline operation

After fetching the benchmark assets:

```text
docker compose -f deploy/portable/compose.yaml build app
docker compose -f deploy/portable/compose.yaml pull ollama gateway
```

Acquire weights during an explicit connected setup phase, for example:

```text
docker compose -f deploy/portable/compose.yaml -f deploy/portable/compose.setup.yaml up -d ollama
docker compose -f deploy/portable/compose.yaml -f deploy/portable/compose.setup.yaml exec ollama ollama pull qwen2.5-coder:1.5b
docker compose -f deploy/portable/compose.yaml -f deploy/portable/compose.setup.yaml down
docker compose -f deploy/portable/compose.yaml up -d --wait
docker compose -f deploy/portable/compose.yaml exec app python deploy/portable/smoke_check.py
```

Do not add `-v` to `down` unless you intend to delete the stored models and new
results. Normal startup places the app and Ollama on an internal network and
exposes only a loopback gateway at http://127.0.0.1:8501. The setup override
temporarily enables network access; remove it for offline runtime. A copied
Ollama cache can instead be mounted read-only using `compose.models.yaml` and
`LOOPS_MODEL_DIR`. For an NVIDIA GPU, add `compose.gpu.yaml` after configuring
the [host prerequisites](https://docs.ollama.com/docker).

The container targets Linux AMD64. Direct Python dependencies and external checker
archives are version-pinned, but base images, system packages and transitive
dependencies are not a complete historical environment lock. Record built image
digests and tool versions. The default container Ollama version is not asserted
to be the Windows runtime used in the historical pilot. See
[Docker network isolation](https://docs.docker.com/reference/compose-file/networks/#internal).

## Experiments and saved data

Run instructions and the distinction between numeric bounds and trace budgets
are in [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md). Experiments require explicit
commands or a Generate action in the interface. New data are written below
`results/`, or into the Docker results volume. Treat saved prompts, source hashes,
raw responses, model digests and checker diagnostics as research evidence.

The interface's native response parser accepts raw JSON or one enclosing JSON
code fence; it does not repair predicates. The source batch runner separately
preserves the original direct-JSON policy by default and records an explicit
corrected-policy option. Do not relabel corrected-parser results as original scores.

## Limits and attribution

Formal success means initiation, preservation and target sufficiency under the
declared checker semantics. It does not prove termination, industrial readiness,
training-data independence or usefulness beyond a baseline. A trace match alone
is not a proof. These are compact research benchmarks, not representative
commercial applications. Do not expose this prototype as an unauthenticated
public service or treat it as a sandbox for arbitrary uploaded code.

Source attribution and benchmark adaptations are documented in
[docs/PROVENANCE.md](docs/PROVENANCE.md). No project-wide software licence has
been selected in this folder; the author must decide the publication terms.
