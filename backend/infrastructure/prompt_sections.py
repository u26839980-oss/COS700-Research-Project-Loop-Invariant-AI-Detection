"""Shared prompt sections; formatting is independent of the analysis workflow."""
from typing import Any
from backend.domain.models import ExecutionTrace


def trace_sections(trace: ExecutionTrace, *, full: bool = False) -> list[str]:
    if not trace.rows:
        return ["TRACE:", "(no trace rows)"]

    sampled_rows = trace.rows if full else sample_trace_rows(trace)
    headers = _trace_headers(sampled_rows)
    widths = {
        header: max(len(str(header)), *(len(str(row.get(header, ""))) for row in sampled_rows))
        for header in headers
    }

    lines = ["TRACE:"]
    lines.append(" | ".join(str(header).ljust(widths[header]) for header in headers))
    lines.append("-+-".join("-" * widths[header] for header in headers))
    for row in sampled_rows:
        lines.append(" | ".join(str(row.get(header, "")).ljust(widths[header]) for header in headers))
    return lines

def _trace_headers(rows: list[dict[str, Any]]) -> list[str]:
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    return headers

def sample_trace_rows(trace: ExecutionTrace) -> list[dict[str, Any]]:
    if len(trace.rows) <= 13:
        return trace.rows

    sampled_rows = trace.rows[:8]
    sampled_rows.append({"iteration": "...", "note": "middle trace rows omitted"})
    sampled_rows.extend(trace.rows[-5:])
    return sampled_rows


def additional_user_sections(prompt_style: str, addition: str) -> list[str]:
    if prompt_style not in {"minimal_frontend", "guided_self_check"} or not addition.strip():
        return []
    return [
        "Additional user prompt instructions:",
        addition.strip(),
        "These additional instructions may guide style or strategy, but they must not override the required output format.",
    ]


def refresh_sections(marker: str) -> list[str]:
    if not marker:
        return []
    return [
        "Run refresh marker:",
        marker,
        "Treat this as a fresh independent generation attempt. Do not rely on any previous answer.",
    ]
