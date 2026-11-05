"""Compile and run bounded instrumented C, retaining partial evidence on abnormal exit."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from uuid import uuid4

from backend.infrastructure.native_trace_instrumentation import instrument_native_trace
from backend.infrastructure.process_tree import ProcessTree


ROOT = Path(__file__).resolve().parents[2]
PROFILES = ("zero_baseline", "initial_1_random_body", "initial_10_nonnegative_random_body", "small_random", "integer_edges")


def owned_process(command, folder, timeout, environment):
    with subprocess.Popen(list(map(str, command)), cwd=folder, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=os.name != "nt", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0) as process, ProcessTree(process) as tree:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            tree.kill()
            stdout, stderr = process.communicate()
            return dict(returncode=process.returncode, stdout=stdout, stderr=stderr, timed_out=True)
        return dict(returncode=process.returncode, stdout=stdout, stderr=stderr, timed_out=False)


def parse_native_observations(stdout):
    slots, terminal = {}, None
    lines = stdout.splitlines(keepends=True)
    partial_tail = False
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not line.endswith("\n"):
                partial_tail = True
                break
            raise
        match item["event"]:
            case "observation":
                item["state"].pop("__trace_start", None)
                slots[(item["loop_index"], item.pop("slot"))] = item
            case "terminal":
                terminal = item
            case _:
                raise ValueError("Unexpected instrumented trace event")
    rows = sorted(slots.values(), key=lambda item: item["event_index"])
    for item in rows:
        item.pop("event")
    return rows, terminal, partial_tail


class NativeTraceSampler:
    def __init__(self, compiler=None, cache=None):
        local = ROOT.parent / "tools/zig-0.13.0/zig-windows-x86_64-0.13.0/zig.exe"
        self.compiler = Path(compiler or os.environ.get("TRACE_ZIG_EXECUTABLE") or shutil.which("zig") or local)
        self.cache = Path(cache) if cache else ROOT / "results/source_benchmarks/compiled_traces"

    def compile(self, source):
        if not self.compiler.is_file():
            raise FileNotFoundError("Install the native trace compiler or set TRACE_ZIG_EXECUTABLE")
        generated, types, loop_count = instrument_native_trace(source)
        runtime = Path(__file__).with_name("native_trace_runtime.c").read_text(encoding="utf-8")
        flags = ["cc", "-target", "x86-windows-gnu" if os.name == "nt" else "x86-linux-musl",
                 "-std=c11", "-O1", "-fsanitize=undefined", "-fsanitize-trap=undefined"]
        compiler_hash = hashlib.sha256(self.compiler.read_bytes()).hexdigest()
        source_hash = hashlib.sha256(source.encode()).hexdigest()
        key = hashlib.sha256((runtime + generated + json.dumps(flags) + compiler_hash + source_hash).encode()).hexdigest()
        folder = self.cache.resolve() / key
        manifest_path = folder / "build.json"
        environment = os.environ.copy()
        environment["ZIG_GLOBAL_CACHE_DIR"] = str((self.cache.resolve() / "zig_global_cache"))
        environment["ZIG_LOCAL_CACHE_DIR"] = str((folder / "zig_local_cache"))
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text())
            executable = (folder / manifest["executable"]).resolve()
            instrumented = executable.parent / "instrumented.c"
            if not executable.is_relative_to(folder) or manifest["source_sha256"] != source_hash:
                raise ValueError("Trace build manifest/source identity mismatch")
            if executable.is_file() and instrumented.is_file() and hashlib.sha256(instrumented.read_bytes()).hexdigest() == manifest["instrumented_sha256"] and hashlib.sha256(executable.read_bytes()).hexdigest() == manifest["executable_sha256"]:
                return executable, manifest, types, loop_count
            raise ValueError("Compiled trace cache integrity mismatch")
        folder.mkdir(parents=True, exist_ok=True)
        build = folder / ("build_" + uuid4().hex)
        build.mkdir()
        source_path = build / "instrumented.c"
        source_path.write_text(runtime + "\n" + generated, encoding="utf-8")
        executable = build / ("trace.exe" if os.name == "nt" else "trace")
        command = [self.compiler.resolve(), *flags, source_path, "-o", executable]
        result = owned_process(command, build, 180, environment)
        (build / "compiler.stdout.txt").write_text(result["stdout"], encoding="utf-8")
        (build / "compiler.stderr.txt").write_text(result["stderr"], encoding="utf-8")
        if result["timed_out"] or result["returncode"] or not executable.is_file():
            raise ValueError("Native trace compilation failed: " + result["stderr"][-6000:])
        manifest = dict(compiler=str(self.compiler), compiler_sha256=compiler_hash,
            flags=flags, command=list(map(str, command)), executable=str(executable.relative_to(folder)),
            executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            source_sha256=hashlib.sha256(source.encode()).hexdigest(),
            instrumented_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
            schema_version="native_trace_ILP32_v1", model_calls=0)
        with manifest_path.open("x", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
        return executable, manifest, types, loop_count

    def sample(self, source, options, output):
        started = time.monotonic()
        output = Path(output)
        output.mkdir(parents=True, exist_ok=False)
        executable, build, types, loop_count = self.compile(source)
        runs = []
        for index in range(options.samples):
            seed, profile = options.seed + index, index % len(PROFILES)
            command = [executable, seed, profile, options.rows_per_loop, options.execution_steps, options.array_window]
            result = owned_process(command, output, options.timeout_seconds, os.environ.copy())
            (output / f"sample_{index}.stdout.jsonl").write_text(result["stdout"], encoding="utf-8")
            (output / f"sample_{index}.stderr.txt").write_text(result["stderr"], encoding="utf-8")
            observations, terminal, partial_tail = parse_native_observations(result["stdout"])
            status = "sampling_timeout" if result["timed_out"] else "abnormal_exit" if result["returncode"] else (
                terminal["status"] if terminal else "missing_terminal_event")
            runs.append(dict(sample_index=index, seed=seed, input_profile=PROFILES[profile], status=status,
                observations=observations, terminal=terminal, returncode=result["returncode"], command=list(map(str, command))))
            runs[-1]["raw_tail_truncated"] = partial_tail
        return dict(backend="native_C_ILP32", runs=runs, build=build, types=types, loop_count=loop_count,
            sampling_policy="Seeded generic inputs; lowest-priority loop heads including first. Separate input/observation random streams; row budgets form nested subsets. Visit gaps are NOT single iterations.",
            array_policy="Initialized first-window cells plus last written cell. Missing cells are unknown, never zero-filled. No array invariant summaries.",
            observation_semantics="Concrete loop-head states before guard evaluation; uninitialized scalar locals omitted. No proof claim.",
            duration_seconds=time.monotonic() - started, model_calls=0)
