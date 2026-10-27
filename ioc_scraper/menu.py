from __future__ import annotations

from collections.abc import Callable


def _read(input_fn: Callable[..., str], prompt: str) -> str:
    try:
        return input_fn(prompt).strip()
    except EOFError:
        return ""


def _source_flags(raw: str) -> list[str]:
    tokens = raw.split()
    if not tokens:
        return []
    ids: list[str] = []
    for token in tokens:
        int(token)
        ids.append(token)
    return ["--source", *ids]


def prompt_menu(
    input_fn: Callable[..., str] = input,
    output_fn: Callable[..., None] = print,
) -> list[str] | None:
    output_fn("Z/HOUND")
    output_fn("Human review is only in the dashboard. This menu does not audit IoCs.")
    output_fn("1) Collect IoCs")
    output_fn("2) Validate sources")
    output_fn("3) List sources")
    output_fn("4) Start dashboard")
    output_fn("0) Quit")
    while True:
        choice = _read(input_fn, "Choice: ")
        if choice in {"", "0"}:
            return None
        if choice == "1":
            lookback = _read(input_fn, "Lookback [3m]: ") or "3m"
            try:
                source_flags = _source_flags(_read(input_fn, "Source ids (blank = all enabled): "))
            except ValueError:
                output_fn("Source ids must be integers.")
                continue
            return ["collect", "--lookback", lookback, *source_flags]
        if choice == "2":
            try:
                source_flags = _source_flags(_read(input_fn, "Source ids (blank = all enabled): "))
            except ValueError:
                output_fn("Source ids must be integers.")
                continue
            return ["validate-sources", *source_flags]
        if choice == "3":
            return ["list-sources"]
        if choice == "4":
            return ["dashboard"]
        output_fn("Choose 0, 1, 2, 3, or 4.")
