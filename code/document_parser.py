from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

DEVANAGARI = re.compile(r"[\u0900-\u097f]")
QUESTION_START = re.compile(r"^\s*(\d{1,3})\s*[.)]\s*(.*)$", re.S)
OPTION_MARKER = re.compile(r"\(([A-Da-d])\)\s*")
NUMBERED_ITEM = re.compile(r"(?:^|\s)(\d{1,3})\.\s+")


def text_of(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    ordered = soup.find(["ol", "ul"])
    if not ordered:
        return soup.get_text(" ", strip=True)

    roman = ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x")

    def render_list(container) -> list[str]:
        rendered: list[str] = []
        list_type = (container.get("type") or "").lower()
        for index, item in enumerate(container.find_all("li", recursive=False), 1):
            clone = BeautifulSoup(str(item), "html.parser").find("li")
            nested = item.find(["ol", "ul"], recursive=False)
            for child in clone.find_all(["ol", "ul"], recursive=False):
                child.decompose()
            value = clone.get_text(" ", strip=True)
            if list_type == "i":
                marker = roman[index - 1] if index <= len(roman) else str(index)
                value = f"{marker}) {value}"
            rendered.append(value)
            if nested:
                rendered.extend(render_list(nested))
        return rendered

    prefix_parts = []
    for node in soup.find_all(["ol", "ul"], recursive=False):
        prefix_parts.extend(render_list(node))
    return "\n".join(prefix_parts).strip()


def has_hindi(text: str) -> bool:
    return bool(DEVANAGARI.search(text or ""))


def append_text(existing: str, value: str) -> str:
    if not existing:
        return value.strip()
    if not value:
        return existing.strip()
    separator = "\n" if "\n" in existing or "\n" in value else " "
    return f"{existing.strip()}{separator}{value.strip()}"


def split_options(text: str) -> tuple[str, dict[str, str]]:
    all_matches = list(OPTION_MARKER.finditer(text or ""))
    # Surya often emits each mathematical option as its own block. In a block
    # beginning with (B), (C), or (D), later markers are part of that option's
    # value (for example: "(D) (B) and (C) both").
    if all_matches and all_matches[0].start() == 0 and all_matches[0].group(1).upper() != "A":
        marker = all_matches[0]
        return "", {marker.group(1).upper(): (text or "")[marker.end() :].strip()}
    matches = []
    expected = "A"
    for match in OPTION_MARKER.finditer(text or ""):
        if match.group(1).upper() == expected:
            matches.append(match)
            if expected == "D":
                break
            expected = chr(ord(expected) + 1)
    if not matches:
        return (text or "").strip(), {}
    prefix = (text or "")[: matches[0].start()].strip()
    options: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        options[match.group(1).upper()] = text[match.end() : end].strip()
    return prefix, options


def section_kind(text: str) -> str | None:
    value = (text or "").lower()
    if "long answer" in value or "दीर्घ उत्तरीय" in value:
        return "Section C - Long Answer"
    if "short answer" in value or "लघु उत्तरीय" in value:
        return "Section B - Short Answer"
    if "objective type" in value or "वस्तुनिष्ठ प्रश्न" in value:
        return "Section A - MCQ"
    return None


def numbered_items(text: str) -> list[tuple[int, str]]:
    matches = [match for match in NUMBERED_ITEM.finditer(text or "") if int(match.group(1)) > 0]
    if not matches:
        return []
    items: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        items.append((int(match.group(1)), text[match.end() : end].strip()))
    return items


def descriptive_pair(body: str, section: str) -> tuple[str, str] | None:
    if "MCQ" in section:
        return None
    mark = "5" if "Long" in section else "2"
    match = re.search(rf"\s+{mark}\s+(?=[A-Z])", body)
    if not match:
        return None
    return body[: match.start()].strip(), body[match.end() :].strip()


@dataclass
class Question:
    number: int
    section: str
    page: int
    english: str = ""
    hindi: str = ""
    english_options: dict[str, str] = field(default_factory=dict)
    hindi_options: dict[str, str] = field(default_factory=dict)

    def option(self, letter: str) -> str:
        return self.english_options.get(letter) or self.hindi_options.get(letter, "")


def parse_questions(pages: list[dict]) -> list[Question]:
    questions: list[Question] = []
    current: Question | None = None
    section: str | None = None
    expecting_english = False

    def finish() -> None:
        nonlocal current, expecting_english
        if current and (
            current.hindi or current.english or current.hindi_options or current.english_options
        ):
            questions.append(current)
        current = None
        expecting_english = False

    for page in pages:
        page_number = int(page.get("source_page_index", 0)) + 1
        blocks = sorted(page.get("blocks", []), key=lambda b: b.get("reading_order", 0))
        for block in blocks:
            label = block.get("label", "")
            if label in {"PageHeader", "PageFooter", "Picture"} or block.get("skipped"):
                continue
            text = text_of(block.get("html", ""))
            if not text:
                continue
            detected = section_kind(text) if label == "SectionHeader" else None
            if detected:
                finish()
                section = detected
                continue
            if label == "SectionHeader":
                # Subject/part headings belong to document structure, never to
                # the preceding question body.
                continue
            if section is None:
                continue
            if text.lower().lstrip().startswith(("direction", "instruction")):
                continue
            if re.fullmatch(r"\[?\s*\d{3}\s*\]?", text):
                continue

            items = numbered_items(text)
            if items:
                first_item = NUMBERED_ITEM.search(text)
                leading = text[: first_item.start()].strip() if first_item else ""
                if current and leading:
                    lead_prefix, lead_options = split_options(leading)
                    lead_hindi = has_hindi(lead_prefix or leading)
                    if lead_options:
                        if lead_hindi:
                            current.hindi_options.update(lead_options)
                        else:
                            current.english_options.update(lead_options)
                    if lead_prefix:
                        if lead_hindi:
                            current.hindi = append_text(current.hindi, lead_prefix)
                        else:
                            current.english = append_text(current.english, lead_prefix)
                for candidate_number, body in items:
                    pair = descriptive_pair(body, section)
                    if pair:
                        finish()
                        current = Question(candidate_number, section, page_number, english=pair[1], hindi=pair[0])
                        finish()
                        continue
                    if current and candidate_number == current.number and not has_hindi(body):
                        prefix, options = split_options(body)
                        current.english = append_text(current.english, prefix)
                        current.english_options.update(options)
                        expecting_english = False
                        continue
                    finish()
                    current = Question(candidate_number, section, page_number)
                    prefix, options = split_options(body)
                    if has_hindi(prefix):
                        current.hindi = prefix
                        current.hindi_options.update(options)
                        expecting_english = True
                    else:
                        current.english = prefix
                        current.english_options.update(options)
                continue

            question_match = QUESTION_START.match(text)
            if question_match and int(question_match.group(1)) > 0:
                candidate_number = int(question_match.group(1))
                body = question_match.group(2).strip()
                if current and candidate_number == current.number and not has_hindi(body):
                    prefix, options = split_options(body)
                    current.english = append_text(current.english, prefix)
                    current.english_options.update(options)
                    expecting_english = False
                    continue
                finish()
                current = Question(candidate_number, section, page_number)
                prefix, options = split_options(body)
                if has_hindi(prefix):
                    current.hindi = prefix
                    current.hindi_options.update(options)
                    expecting_english = True
                else:
                    current.english = prefix
                    current.english_options.update(options)
                continue

            if current is None:
                continue

            if text.strip() in {"2", "5", "A", "B", "C", "D"}:
                continue

            prefix, options = split_options(text)
            language_is_hindi = has_hindi(prefix or text)
            if options:
                if language_is_hindi or (expecting_english and current.hindi and not current.english):
                    current.hindi_options.update(options)
                else:
                    current.english_options.update(options)
                if prefix:
                    if language_is_hindi:
                        current.hindi = append_text(current.hindi, prefix)
                    else:
                        current.english = append_text(current.english, prefix)
                continue

            if language_is_hindi and not current.hindi:
                current.hindi = text
                expecting_english = True
            elif not language_is_hindi and (expecting_english or not current.english):
                current.english = append_text(current.english, text)
                expecting_english = False
            elif language_is_hindi:
                current.hindi = append_text(current.hindi, text)
            else:
                current.english = append_text(current.english, text)

    finish()
    consolidated: list[Question] = []
    by_key: dict[tuple[str, int], Question] = {}

    def better(old: str, new: str) -> str:
        if not old:
            return new
        if not new or new in old:
            return old
        if old in new:
            return new
        return new if len(new) > len(old) else old

    for question in questions:
        key = (question.section, question.number)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = question
            consolidated.append(question)
            continue
        existing.english = better(existing.english, question.english)
        existing.hindi = better(existing.hindi, question.hindi)
        for letter, value in question.english_options.items():
            existing.english_options[letter] = better(existing.english_options.get(letter, ""), value)
        for letter, value in question.hindi_options.items():
            existing.hindi_options[letter] = better(existing.hindi_options.get(letter, ""), value)
    return consolidated
