"""Bounded, value-free diagnostics for our declared JSON tool contracts."""

from itertools import islice
from typing import Any

from jsonschema import Draft202012Validator

MAX_PROBLEMS = 20


def shape_problems(validator: Draft202012Validator, value: Any) -> list[str]:
    problems = []
    for error in islice(validator.iter_errors(value), MAX_PROBLEMS):
        path = ""
        for part in error.absolute_path:
            path += f"[{part}]" if isinstance(part, int) else ("." if path else "") + str(part)
        path = path or "arguments"
        if error.validator == "type":
            message = f"must be {error.validator_value}"
        elif error.validator == "required":
            message = error.message  # names a trusted schema field, never its input value
        elif error.validator == "additionalProperties":
            message = "unknown key (only declared fields are allowed)"
        elif error.validator == "enum":
            message = "must be one of " + ", ".join(map(str, error.validator_value))
        else:
            message = "must match a declared alternative"
        problems.append(f"{path}: {message}"[:240])
    return problems
