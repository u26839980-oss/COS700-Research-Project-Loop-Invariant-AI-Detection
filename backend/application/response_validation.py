"""Keep echoed task specifications out of model hypothesis verification."""


def candidate_response(output: str, prompt: str) -> tuple[str, bool]:
    prefix = prompt.strip()[:200]
    if len(prefix) < 200:
        return output, False
    position = output.find(prefix)
    if position < 0:
        return output, False
    return output[:position].strip(), True
