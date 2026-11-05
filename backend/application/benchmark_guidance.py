def build_guidance_examples(
    selected_benchmark_name: str,
    include_prompt_guidance: bool,
) -> list[dict[str, str]]:
    # Test-catalogue solutions must never become demonstrations for another test.
    return []
