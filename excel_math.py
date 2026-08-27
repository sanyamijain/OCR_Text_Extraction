from __future__ import annotations

import re

COMMANDS = {
    r"\times": "×", r"\div": "÷", r"\neq": "≠", r"\leq": "≤",
    r"\geq": "≥", r"\pm": "±", r"\infty": "∞", r"\alpha": "α",
    r"\beta": "β", r"\gamma": "γ", r"\theta": "θ", r"\pi": "π",
    r"\Omega": "Ω", r"\omega": "ω", r"\Delta": "Δ",
    r"\rightarrow": "→", r"\Rightarrow": "⇒", r"\rightarrrow": "→",
    r"\leftrightarrow": "↔", r"\cdot": "·",
    r"\angle": "∠", r"\perp": "⟂", r"\triangle": "△",
    r"\dots": "…", r"\ldots": "…", r"\quad": " ",
    r"\sin": "sin", r"\cos": "cos", r"\tan": "tan",
    r"\cot": "cot", r"\sec": "sec", r"\csc": "csc",
}
SUPERSCRIPT = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
SUBSCRIPT = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")


def _simple_fraction(match: re.Match) -> str:
    numerator, denominator = match.group(1), match.group(2)
    if re.search(r"[+\-=]", numerator):
        numerator = f"({numerator})"
    if re.search(r"[+\-=]", denominator):
        denominator = f"({denominator})"
    return f"{numerator}⁄{denominator}"


def _overline(match: re.Match) -> str:
    return "".join(character + "\u0305" for character in match.group(1))


def _safe_terminology(text: str) -> str:
    if "J/C" in text or "J / C" in text:
        text = text.replace("जूल/कूलोम", "जूल/कूलॉम")
        text = text.replace("जूल / कूलोम", "जूल / कूलॉम")
    return text


def readable_math(value: str) -> str:
    """Readable Unicode for Word/Excel; never modifies preserved OCR JSON."""
    text = value or ""
    text = re.sub(r"\\xrightarrow\{\\Delta\}", "─Δ→", text)
    text = re.sub(r"\\overset\{\\Delta\}\{\\rightarrow\}", "─Δ→", text)
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\\text\{([^{}]*)\}", r"\1", text)
        text = re.sub(r"\\mathrm\{([^{}]*)\}", r"\1", text)
        text = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", _simple_fraction, text)
        text = re.sub(r"\\sqrt\[([^\]]+)\]\{([^{}]+)\}", r"\2√(\1)", text)
        text = re.sub(r"\\sqrt\{([^{}]+)\}", r"√(\1)", text)
        text = re.sub(r"\\overline\{([^{}]+)\}", _overline, text)
    text = re.sub(r"\^\{?\\circ\}?", "°", text)
    text = text.replace(r"\circ", "°")
    for command, replacement in COMMANDS.items():
        text = text.replace(command, replacement)
    text = re.sub(r"\^\{([0-9+\-=()n]+)\}", lambda m: m.group(1).translate(SUPERSCRIPT), text)
    text = re.sub(r"\^([0-9n])", lambda m: m.group(1).translate(SUPERSCRIPT), text)
    text = re.sub(r"_\{([0-9+\-=()]+)\}", lambda m: m.group(1).translate(SUBSCRIPT), text)
    text = re.sub(r"_([0-9])", lambda m: m.group(1).translate(SUBSCRIPT), text)
    text = re.sub(r"\\(?:left|right)(?=[()\[\]{}|])", "", text)
    text = text.replace(r"\,", " ").replace(r"\;", " ").replace(r"\!", "")
    text = text.replace("{", "").replace("}", "")
    # Preserve the word of an unrecognised alphabetic command but never expose
    # raw LaTeX syntax in a user-facing spreadsheet or Word fallback.
    text = re.sub(r"\\([A-Za-z]+)", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    return _safe_terminology(text).strip()
