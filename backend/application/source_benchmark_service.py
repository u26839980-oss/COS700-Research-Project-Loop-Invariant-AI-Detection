"""Source cohort workflows. Checker evidence never becomes prompt input."""
from __future__ import annotations

import hashlib
import json
from backend.domain.source_traces import trace_evidence_digest


class SourceBenchmarkService:
    def __init__(self, *, repository, code_checker_factory, native_checker, prepare_source, parse_candidates, parse_native_contracts, trace_sampler):
        self.repository = repository
        self.code_checker_factory = code_checker_factory
        self.native_checker = native_checker
        self.prepare_source = prepare_source
        self.parse_candidates = parse_candidates
        self.parse_native_contracts = parse_native_contracts
        self.trace_sampler = trace_sampler

    def sample_traces(self, task_id, options):
        task = self.repository.task(task_id)
        source = self.repository.read(task_id)
        template = self.repository.read(task_id, "verification_conditions") if task["collection"] == "Code2Inv" else None
        folder = self.repository.reserve()
        record = self.trace_sampler.sample(task=task, source=source, template=template, options=options, output=folder)
        record["log_path"] = self.repository.save(folder, record)
        return record

    def inspect(self, task_id):
        task = self.repository.task(task_id)
        source = self.repository.read(task_id)
        prepared, loops = self.prepare_source(source, task["collection"])
        return dict(task=task, source=source, prepared=prepared, loops=loops)

    @staticmethod
    def conservative_context_requirement(prompt, output_tokens):
        """Byte-based preflight, not a claim about a particular model's tokenizer."""
        return len(prompt.encode("utf-8")) + output_tokens + 1024

    def prompt(self, task_id, addition="", *, input_mode="syntax-only", trace_record=None):
        task = self.repository.task(task_id)
        if input_mode not in {"syntax-only", "trace-only", "syntax-plus-trace"}:
            raise ValueError("Unknown input mode")
        if input_mode != "syntax-only":
            if not trace_record or trace_record.get("task_id") != task_id or trace_record.get("status") != "sampled_all_loops":
                raise ValueError("Sample this task with coverage of every loop before trace-based generation.")
            expected = next(asset["sha256"] for asset in task["assets"] if asset["path"] == task["primary_input"])
            digest = trace_evidence_digest(trace_record["evidence"])
            if expected != trace_record["source_sha256"] or digest != trace_record["evidence_sha256"]:
                raise ValueError("Trace source/evidence integrity mismatch")
        # Exclude names, URLs, official verdicts, certificates and all source comments.
        contract = ("Return one Python-style scalar invariant per line between CANDIDATES and END. "
                    "Use and, or, not. No explanations.") if task["collection"] == "Code2Inv" else (
                    "Return only a JSON object mapping each loop index to its invariant in C Boolean syntax. "
                    "Use &&, ||, !. Quantifiers: forall(k, predicate), exists(k, predicate), with int-bound k. "
                    "Supply contracts for all loops, including nested loops. No explanations.")
        semantics = ("Interpret scalars as mathematical integers, uninitialized locals as symbolic inputs, assume as path restriction. "
                     if task["collection"] == "Code2Inv" else
                     "Use native ILP32 C arithmetic, including unsigned wraparound. __VERIFIER_assert checks its condition; "
                     "reach_error denotes assertion failure; assume_abort_if_not and __VERIFIER_assume restrict paths. ")
        instructions = "Infer inductive loop invariants, sufficient if possible to establish the program assertions. "
        if input_mode == "trace-only":
            instructions = "Infer candidate loop invariants from observations only. The program, guard and goal are intentionally withheld. "
        evidence_sections = ""
        if input_mode != "trace-only":
            info = self.inspect(task_id)
            evidence_sections += "\nPROGRAM:\n" + info["prepared"] + "\nLOOPS:\n" + json.dumps(info["loops"])
        if input_mode != "syntax-only":
            evidence_sections += ("\nObserved states are bounded, non-exhaustive evidence, not proof. Do not infer termination from the end of a sample. Missing cells are unknown. Nonconsecutive visits are not single-step transitions.\nTRACE:\n"
                                  + json.dumps(trace_record["evidence"], sort_keys=True, separators=(",", ":")))
        return (instructions + semantics +
                "Nondeterministic calls choose fresh values at each call. Do not assume a fixed sampled input.\n"
                + contract + evidence_sections + ("\nAdditional instructions:\n" + addition.strip() if addition.strip() else ""))

    def generate(self, task_id, model, adapter, *, addition="", unwind=20, timeout=30,
                 input_mode="syntax-only", trace_record=None, **settings):
        """Explicit user-triggered generation. Never called by page load or sampling."""
        if not model.strip() or model.startswith("gemini/"):
            raise ValueError("This cohort currently requires a local model identifier.")
        prompt = self.prompt(task_id, addition, input_mode=input_mode, trace_record=trace_record)
        required = self.conservative_context_requirement(prompt, settings.get("max_output_tokens", 2048))
        if settings.get("context_length", 4096) < required:
            raise ValueError(f"Conservative prompt budget requires context >= {required}; increase context or reduce observations/inputs. No model called.")
        output = adapter.generate(model, prompt, **settings)
        generation = dict(model=model, prompt=prompt, raw_output=output, input_mode=input_mode, model_calls=1,
                          metadata=dict(adapter.last_generation_metadata), settings=settings,
                          prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
        generation["prompt_utf8_bytes"] = len(prompt.encode("utf-8"))
        if input_mode != "syntax-only":
            generation.update(trace_log_path=trace_record.get("log_path", ""),
                trace_evidence_sha256=trace_record["evidence_sha256"], trace_options=trace_record["options"])
        # Preserve the actual response even if a checker crashes or is interrupted.
        generation_path = self.repository.save(self.repository.reserve(),
            dict(task_id=task_id, status="generated_unchecked", generation=generation, model_calls=1))
        generation["generation_log_path"] = generation_path
        try:
            if self.repository.task(task_id)["collection"] == "Code2Inv":
                candidates = self.parse_candidates(output)[:8]
                if not candidates:
                    raise ValueError("No supported invariant candidates parsed.")
            else:
                candidates = self.parse_native_contracts(output)
        except (ValueError, TypeError) as exc:
            record = dict(task_id=task_id, status="invalid_model_output", model_calls=1,
                          generation=generation, diagnostic=str(exc))
            record["log_path"] = self.repository.save(self.repository.reserve(), record)
            return record
        try:
            return self.check(task_id, candidates, unwind=unwind, timeout=timeout, origin="LLM", generation=generation)
        except (ValueError, TypeError) as exc:
            record = dict(task_id=task_id, status="candidate_admission_failure", model_calls=1,
                          generation=generation, diagnostic=str(exc))
            record["log_path"] = self.repository.save(self.repository.reserve(), record)
            return record

    def check(self, task_id, candidates, *, unwind=20, timeout=30, origin="manual", generation=None):
        info = self.inspect(task_id)
        task = info["task"]
        folder = self.repository.reserve()
        record = dict(task_id=task_id, collection=task["collection"], origin=origin, model_calls=0,
                      source_sha256=hashlib.sha256(info["source"].encode()).hexdigest(),
                      source_assets=task["assets"], semantics=task["semantics"])
        if generation:
            record.update(generation=generation, model_calls=generation["model_calls"])
        match task["collection"]:
            case "Code2Inv":
                checker = self.code_checker_factory(self.repository.read(task_id, "verification_conditions"), timeout * 1000)
                record["verifications"] = [checker.verify(candidate).to_record() for candidate in candidates]
                record["status"] = "checked_original_SMT_Int_VCs"
            case "SV-COMP":
                if not isinstance(candidates, dict):
                    raise ValueError("Native C candidates must map loop indices to C predicates.")
                record["native_verification"] = self.native_checker.run(info["source"], folder,
                    contracts=candidates, unwind=unwind, timeout=timeout)
                record["status"] = record["native_verification"]["status"]
            case _:
                raise ValueError("Unknown source collection")
        record["log_path"] = self.repository.save(folder, record)
        return record
