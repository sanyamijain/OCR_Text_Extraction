from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString, Tag

MATH_MARKER = re.compile(r"\[\[MATH:(\d+)\]\]")
QUESTION_BOUNDARY = re.compile(r"(?<![\w.])(\d{1,3})\.\s+")
OPTION_BOUNDARY = re.compile(r"\(([A-Da-d])\)\s*")
ROMAN = ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x")


@dataclass
class LogicalLine:
    text: str
    maths: list[str]
    kind: str = "text"


def _clean_spacing(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def _marked_fragment(node: Tag | BeautifulSoup) -> tuple[str, list[str]]:
    clone = BeautifulSoup(str(node), "html.parser")
    maths: list[str] = []
    for math in clone.find_all("math"):
        index = len(maths)
        maths.append(math.get_text(" ", strip=True))
        math.replace_with(NavigableString(f"[[MATH:{index}]]"))
    for br in clone.find_all("br"):
        br.replace_with(NavigableString("\n"))
    return _clean_spacing(clone.get_text(" ", strip=False)), maths


def _remap_markers(text: str, source_maths: list[str], destination: list[str]) -> str:
    def replace(match: re.Match) -> str:
        destination.append(source_maths[int(match.group(1))])
        return f"[[MATH:{len(destination) - 1}]]"

    return MATH_MARKER.sub(replace, text)


def _split_options(value: str) -> list[str]:
    matches = list(OPTION_BOUNDARY.finditer(value))
    if not matches:
        return [value.strip()] if value.strip() else []
    prefix = value[: matches[0].start()].strip()
    lines = [prefix] if prefix else []
    expected = matches[0].group(1).upper()
    accepted: list[re.Match] = []
    for match in matches:
        letter = match.group(1).upper()
        if letter != expected:
            continue
        accepted.append(match)
        if letter == "D":
            break
        expected = chr(ord(expected) + 1)
    for index, match in enumerate(accepted):
        end = accepted[index + 1].start() if index + 1 < len(accepted) else len(value)
        body = value[match.end() : end].strip()
        lines.append(f"({match.group(1).upper()}) {body}".rstrip())
    return lines


def arrange_text(value: str) -> list[str]:
    """Split combined OCR text into questions and vertically arranged options."""
    value = _clean_spacing(value)
    if not value:
        return []
    output: list[str] = []
    for physical_line in value.splitlines():
        line = physical_line.strip()
        if not line:
            continue
        matches = list(QUESTION_BOUNDARY.finditer(line))
        if not matches:
            output.extend(_split_options(line))
            continue
        prefix = line[: matches[0].start()].strip()
        if prefix:
            output.extend(_split_options(prefix))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            body = line[match.end() : end].strip()
            parts = _split_options(body)
            if parts:
                output.append(f"{match.group(1)}. {parts[0]}".rstrip())
                output.extend(parts[1:])
            else:
                output.append(f"{match.group(1)}.")
    return output


def html_logical_lines(html: str, label: str = "") -> list[LogicalLine]:
    """Preserve HTML list structure and math boundaries for Word rendering."""
    soup = BeautifulSoup(html or "", "html.parser")
    lines: list[LogicalLine] = []

    def append_arranged(text: str, maths: list[str], kind: str = "text") -> None:
        for arranged in arrange_text(text):
            selected: list[str] = []
            remapped = _remap_markers(arranged, maths, selected)
            lines.append(LogicalLine(remapped, selected, kind))

    def visit_list(ordered: Tag) -> None:
        list_type = (ordered.get("type") or "").lower()
        items = ordered.find_all("li", recursive=False)
        for index, item in enumerate(items, 1):
            nested = item.find(["ol", "ul"], recursive=False)
            clone = BeautifulSoup(str(item), "html.parser").find("li")
            for child in clone.find_all(["ol", "ul"], recursive=False):
                child.decompose()
            text, maths = _marked_fragment(clone)
            if list_type == "i":
                prefix = ROMAN[index - 1] if index <= len(ROMAN) else str(index)
                append_arranged(f"{prefix}) {text}", maths, "list")
            else:
                append_arranged(text, maths, "list")
            if nested:
                visit_list(nested)

    top_nodes = [child for child in soup.contents if not (isinstance(child, NavigableString) and not child.strip())]
    if any(isinstance(node, Tag) and node.name in {"ol", "ul"} for node in top_nodes):
        for node in top_nodes:
            if isinstance(node, Tag) and node.name in {"ol", "ul"}:
                visit_list(node)
            elif isinstance(node, Tag):
                text, maths = _marked_fragment(node)
                append_arranged(text, maths, "equation" if label == "Equation" else "text")
        return lines

    paragraph_nodes = soup.find_all(["p", "h1", "h2", "h3", "h4"], recursive=False)
    nodes = paragraph_nodes or [soup]
    for node in nodes:
        text, maths = _marked_fragment(node)
        append_arranged(text, maths, "equation" if label == "Equation" else "text")
    return lines
