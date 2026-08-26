from __future__ import annotations

import re

COMMANDS = {
    r"\times": "×",
    r"\div": "÷",
    r"\neq": "≠",
    r"\leq": "≤",
    r"\geq": "≥",
    r"\pm": "±",
    r"\infty": "∞",
    r"\alpha": "α",
    r"\beta": "β",
    r"\gamma": "γ",
    r"\theta": "θ",
    r"\pi": "π",
}
SUPERSCRIPT = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
SUBSCRIPT = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")


def _simple_fraction(match: re.Match) -> str:
    numerator, denominator = match.group(1), match.group(2)
    return f"{numerator}⁄{denominator}"


def _overline(match: re.Match) -> str:
    return "".join(character + "\u0305" for character in match.group(1))


def readable_math(value: str) -> str:
    """Make Surya LaTeX readable in Excel without changing the raw OCR JSON."""
    text = value or ""
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", _simple_fraction, text)
        text = re.sub(r"\\sqrt\[([^\]]+)\]\{([^{}]+)\}", r"\2√(\1)", text)
        text = re.sub(r"\\sqrt\{([^{}]+)\}", r"√(\1)", text)
        text = re.sub(r"\\overline\{([^{}]+)\}", _overline, text)
    for command, replacement in COMMANDS.items():
        text = text.replace(command, replacement)
    text = re.sub(r"\^\{([0-9+\-=()n]+)\}", lambda m: m.group(1).translate(SUPERSCRIPT), text)
    text = re.sub(r"\^([0-9n])", lambda m: m.group(1).translate(SUPERSCRIPT), text)
    text = re.sub(r"_\{([0-9+\-=()]+)\}", lambda m: m.group(1).translate(SUBSCRIPT), text)
    text = re.sub(r"_([0-9])", lambda m: m.group(1).translate(SUBSCRIPT), text)
    return text.replace("{", "").replace("}", "")
