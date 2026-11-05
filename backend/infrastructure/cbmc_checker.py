"""Native ILP32 C checking through CBMC; bounded passes are never invariant proofs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from backend.infrastructure.svcomp_contracts import prepare_source
from backend.infrastructure.process_tree import ProcessTree


def default_tool_directory():
    configured = os.environ.get("CBMC_BIN")
    if configured:
        return Path(configured).resolve()
    local = Path(__file__).resolve().parents[3] / "tools/cbmc-6.11.0/portable/cbmc/bin"
    located = shutil.which("cbmc")
    return Path(located).parent if located else local


class CBMCChecker:
    def __init__(self, tool_directory=None):
        self.directory = Path(tool_directory) if tool_directory else default_tool_directory()
        self.suffix = ".exe" if os.name == "nt" else ""

    def tool(self, name):
        return self.directory / (name + self.suffix)

    def available(self):
        return self.tool("cbmc").is_file()

    def run(self, source, output, *, contracts=None, unwind=20, timeout=30):
        if not 1 <= unwind <= 1000000 or not 1 <= timeout <= 3600:
            raise ValueError("Invalid resource limits")
        output = Path(output).resolve()
        output.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()
        environment = os.environ.copy()
        record = dict(checker="CBMC", contract="native_C_ILP32_reachability_v1", model_calls=0,
                      unwind=unwind, timeout_seconds=timeout, contracts=contracts or {},
                      source_sha256=hashlib.sha256(source.encode()).hexdigest(), commands=[],
                      status="checker_unavailable", accepted=False, invariant_proved=False)
        def invoke(name, args):
            command = [str(self.tool(name)), *map(str, args)]
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            record["commands"].append(command)
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=output, env=environment,
                    start_new_session=os.name != "nt") as process, ProcessTree(process) as tree:
                try:
                    stdout, stderr = process.communicate(timeout=remaining)
                except subprocess.TimeoutExpired:
                    tree.kill()
                    stdout, stderr = process.communicate()
                    (output / f"command_{len(record['commands'])}.stdout.txt").write_text(stdout, encoding="utf-8")
                    (output / f"command_{len(record['commands'])}.stderr.txt").write_text(stderr, encoding="utf-8")
                    raise
                result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            index = len(record["commands"])
            (output / f"command_{index}.stdout.txt").write_text(result.stdout, encoding="utf-8")
            (output / f"command_{index}.stderr.txt").write_text(result.stderr, encoding="utf-8")
            return result
        try:
            if not self.available():
                return self._save(output, record, started)
            version = invoke("cbmc", ["--version"])
            record["version"] = version.stdout.strip()
            record["executable_sha256"] = hashlib.sha256(self.tool("cbmc").read_bytes()).hexdigest()
            prepared, loops = prepare_source(source, contracts)
            record["loops"] = loops
            generated = output / "instrumented.i"
            generated.write_text(prepared, encoding="utf-8")
            record["instrumented_sha256"] = hashlib.sha256(generated.read_bytes()).hexdigest()
            target = generated
            solver_args = []
            if "__CPROVER_forall" in prepared or "__CPROVER_exists" in prepared:
                configured = os.environ.get("Z3_EXECUTABLE") or shutil.which("z3")
                local_z3 = Path(__file__).resolve().parents[3] / "tools/z3-5.3.1/z3-5.3.1-x64-win/bin/z3.exe"
                executable = Path(configured) if configured else local_z3
                if not executable.is_file():
                    record.update(status="quantifier_solver_unavailable")
                    return self._save(output, record, started)
                record["quantifier_solver_sha256"] = hashlib.sha256(executable.read_bytes()).hexdigest()
                environment["PATH"] = str(executable.parent) + os.pathsep + environment.get("PATH", "")
                solver_args = ["--smt2", "--z3"]
            if contracts and any(value.strip() for value in contracts.values()):
                compiler = "goto-cl" if os.name == "nt" else "goto-cc"
                binary, instrumented = output / "compiled.goto", output / "contracts.goto"
                args = ["--i386-linux", generated, "/Fe" + str(binary)] if os.name == "nt" else ["--i386-linux", "-o", binary, generated]
                compiled = invoke(compiler, args)
                if compiled.returncode or not binary.is_file():
                    record.update(status="instrumentation_error", diagnostic=compiled.stdout + compiled.stderr)
                    return self._save(output, record, started)
                transformed = invoke("goto-instrument", ["--apply-loop-contracts", binary, instrumented])
                if transformed.returncode or not instrumented.is_file():
                    record.update(status="instrumentation_error", diagnostic=transformed.stdout + transformed.stderr)
                    return self._save(output, record, started)
                target = instrumented
            result = invoke("cbmc", [target, "--gcc", "--i386-linux", "--no-library", "--unwind", unwind,
                                      "--unwinding-assertions", "--json-ui", "--trace", "--retain-trivial-checks", *solver_args])
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError:
                record.update(status="checker_error", diagnostic=result.stdout + result.stderr)
                return self._save(output, record, started)
            active_contracts = sum(bool(value.strip()) for value in (contracts or {}).values())
            record.update(classify_output(payload, result.returncode, active_contracts))
            record["raw_output"] = payload
        except subprocess.TimeoutExpired:
            record.update(status="timeout", diagnostic="Checker resource budget exhausted; no proof claimed.")
        except OSError as exc:
            record.update(status="checker_error", diagnostic=str(exc))
        return self._save(output, record, started)

    @staticmethod
    def _save(output, record, started):
        record["duration_seconds"] = time.monotonic() - started
        (output / "result.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        return record


def classify_output(payload, returncode, has_contracts):
    errors = [item for item in payload if item.get("messageType") == "ERROR"]
    properties = [prop for item in payload for prop in item.get("result", [])]
    failed = [prop for prop in properties if prop.get("status") == "FAILURE"]
    status = "checker_error"
    if errors or returncode not in (0, 10) or not properties:
        return dict(status=status, accepted=False, invariant_proved=False, properties=properties, errors=errors)
    unwind_failed = [prop for prop in failed if prop.get("sourceLocation", {}).get("propertyClass") == "unwind"
                     or "unwind" in prop.get("property", "")]
    other_failed = [prop for prop in failed if prop not in unwind_failed]
    invariant_failed = [prop for prop in other_failed if "loop invariant" in prop.get("description", "").lower()]
    goal_failed = [prop for prop in other_failed if "reach_error" in prop.get("property", "")]
    if other_failed:
        if invariant_failed:
            status = "contract_refuted"
        elif goal_failed:
            status = "insufficient_for_goal" if has_contracts else "safety_counterexample"
        else:
            status = "native_safety_obligation_failed" if has_contracts else "native_safety_counterexample"
    elif unwind_failed:
        status = "incomplete_unwinding"
    elif returncode == 0 and all(prop.get("status") == "SUCCESS" for prop in properties):
        entry = sum("loop invariant before entry" in prop.get("description", "").lower() for prop in properties)
        preservation = sum("loop invariant is preserved" in prop.get("description", "").lower() for prop in properties)
        if has_contracts and (entry < int(has_contracts) or preservation < int(has_contracts)):
            status = "missing_contract_obligations"
        else:
            status = "contracts_and_safety_proved" if has_contracts else "safety_proved_complete_unwinding"
    entry_checks = [prop for prop in properties if "loop invariant before entry" in prop.get("description", "").lower()]
    preservation_checks = [prop for prop in properties if "loop invariant is preserved" in prop.get("description", "").lower()]
    # A failed goal does not invalidate independently proved induction obligations.
    # Incomplete unwinding, frame/safety failures and unknowns still block this claim.
    induction_proved = bool(has_contracts and len(entry_checks) >= int(has_contracts)
        and len(preservation_checks) >= int(has_contracts)
        and all(prop.get("status") == "SUCCESS" for prop in properties if "reach_error" not in prop.get("property", "")))
    return dict(status=status, accepted=status in {"contracts_and_safety_proved", "safety_proved_complete_unwinding"},
                invariant_proved=induction_proved, establishment_checks=entry_checks,
                preservation_checks=preservation_checks, properties=properties)
