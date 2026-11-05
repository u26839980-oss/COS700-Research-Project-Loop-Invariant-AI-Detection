"""Shared candidate extraction with explicit scalar and textbook syntax profiles."""
from __future__ import annotations

import ast
import io
import json
import re
import tokenize
from collections.abc import Callable

from backend.infrastructure.loop_parser import LoopParseError, parse_expression
from backend.domain.candidate_contract import SCALAR_CANDIDATE_LIMIT, TEXTBOOK_CANDIDATE_LIMIT


def _require_native_contract_object(contracts) -> dict[str, str]:
    if not isinstance(contracts, dict) or not contracts or not all(
            isinstance(key, str) and isinstance(value, str) and value.strip()
            for key, value in contracts.items()):
        raise ValueError("Expected a nonempty JSON object of loop-index / C-predicate pairs.")
    return contracts


def extract_strict_native_contracts(raw_output: str) -> dict[str, str]:
    """Original source-pilot admission policy: direct JSON decoding, without fences."""
    return _require_native_contract_object(json.loads(raw_output))


def extract_native_contracts(raw_output: str) -> dict[str, str]:
    """Accept a JSON contract object, optionally enclosed by one complete fence."""
    source = raw_output.strip()
    if source.startswith("```"):
        block = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```[ \t]*", source,
                             flags=re.DOTALL | re.IGNORECASE)
        if block is None:
            raise ValueError("Expected one complete JSON code fence without surrounding prose.")
        source = block.group(1)

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate loop-index key in JSON contract object.")
            result[key] = value
        return result

    return _require_native_contract_object(json.loads(source, object_pairs_hook=unique_pairs))


def _is_boolean(node: ast.AST, allow_quantifiers: bool) -> bool:
    match node:
        case ast.Compare():
            return True
        case ast.BoolOp(values=values):
            return all(_is_boolean(value, allow_quantifiers) for value in values)
        case ast.UnaryOp(op=ast.Not(), operand=value):
            return _is_boolean(value, allow_quantifiers)
        case ast.IfExp(test=test, body=yes, orelse=no):
            return allow_quantifiers and all(_is_boolean(value, allow_quantifiers) for value in (test, yes, no))
        case ast.Call(func=ast.Name(id=name)):
            return allow_quantifiers and name in {"all", "any"}
        case ast.Constant(value=bool()):
            return True
        case _:
            return False


def _scalar_line(line: str) -> str | None:
    line = line.strip()
    if line.startswith("```") or line in {"CANDIDATES", "END"}:
        return None
    line = re.sub(r"^\d+[.)]\s+", "", line)
    line = re.sub(r"^(?:invariant|candidate|answer)\s*:\s*", "", line, flags=re.IGNORECASE)
    return line.strip("`").replace("&&", " and ").replace("||", " or ")


def _textbook_line(line: str) -> str | None:
    line = re.sub(r"^[-*]\s*", "", line.strip())
    line = re.sub(r"^\d+[.)]\s*", "", line)
    line = line.replace("`", "").strip(" ,")
    if not line or line.upper() in {"CANDIDATES", "END"} or line.lower() in {"python", "py"}:
        return None
    line = re.sub(r"^(?:invariant|candidate|answer|inv)\s*:\s*", "", line, flags=re.IGNORECASE)
    return line[5:].strip() if line.lower().startswith("inv =") else line


def _extract(raw_output: str, *, parse: Callable[[str], ast.Expression],
             normalize_line: Callable[[str], str | None],
             allow_quantifiers: bool = False, marker_flags: int = re.MULTILINE | re.DOTALL,
             candidate_limit: int = SCALAR_CANDIDATE_LIMIT) -> list[str]:
    if raw_output.startswith("MODEL_ERROR:"):
        return []
    block = re.search(r"^[ \t]*CANDIDATES[ \t]*:?[ \t]*\r?\n(.*?)(?:^[ \t]*END[ \t]*:?[ \t]*$|\Z)",
                      raw_output, flags=marker_flags)
    if block is None:
        block = re.search(r"^[ \t]*CANDIDATES(?:[ \t]*:[ \t]*|[ \t]+)(\S.*?)[ \t]+END[ \t]*:?[ \t]*$",
                          raw_output, flags=marker_flags & ~re.DOTALL)
    source = block.group(1) if block else raw_output
    lines = [line for raw_line in source.splitlines()
             if (line := normalize_line(raw_line)) is not None]
    candidates: list[str] = []
    seen: set[str] = set()
    pending = []

    def collect() -> None:
        expression = tokenize.untokenize(pending).strip()
        pending.clear()
        try:
            body = parse(expression).body
        except (LoopParseError, SyntaxError):
            return
        if _is_boolean(body, allow_quantifiers):
            candidate = ast.unparse(body)
            if candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)

    # Logical newlines preserve parenthesized formulas and never split Boolean operators.
    try:
        for token in tokenize.generate_tokens(io.StringIO("\n".join(lines)).readline):
            if token.type in (tokenize.NEWLINE, tokenize.ENDMARKER) or (token.type == tokenize.OP and token.string == ";"):
                collect()
            elif token.type not in (tokenize.INDENT, tokenize.DEDENT, tokenize.COMMENT):
                pending.append((token.type, token.string))
    except (tokenize.TokenError, IndentationError):
        pass
    return candidates[:candidate_limit]


def extract_scalar_candidates(raw_output: str) -> list[str]:
    return _extract(raw_output, parse=parse_expression, normalize_line=_scalar_line)


def extract_book_candidates(raw_output: str) -> list[str]:
    return _extract(raw_output.replace("```", "").strip(),
                    parse=lambda expression: ast.parse(expression, mode="eval"),
                    normalize_line=_textbook_line, allow_quantifiers=True, candidate_limit=TEXTBOOK_CANDIDATE_LIMIT,
                    marker_flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
