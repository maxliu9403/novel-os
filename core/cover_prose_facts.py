"""Conservative, source-quoted identity recovery from legacy English biographies.

This is a fallback for a missing structured identity, not a general prose parser.
Only an explicit opening assertion under one exact character-name heading is
eligible. Ambiguous prose stays pending for the existing confirmation flow.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_BIOGRAPHY_SECTIONS = {
    "character core", "characters", "main characters", "character profiles",
    "character biographies",
}
# Copular assertions need a concrete lived role; 'is a protagonist' or 'is a
# woman who wants to become a teacher' is not enough. Explicit 'works as' can
# introduce other occupations without expanding this conservative vocabulary.
_LIVED_ROLE = re.compile(
    r"\b(?:teacher|nurse|doctor|physician|lawyer|attorney|accountant|bookkeeper|"
    r"clerk|engineer|designer|architect|artist|writer|journalist|librarian|"
    r"professor|scientist|researcher|manager|director|executive|assistant|"
    r"consultant|mechanic|electrician|plumber|carpenter|chef|cook|farmer|"
    r"owner|entrepreneur|caregiver|homemaker|housewife|mother|father|"
    r"widow|widower|student)\b",
    re.IGNORECASE,
)
_UNCONFIRMED = re.compile(
    r"\b(?:not|never|pretend|pretending|fake|fictional|imaginary|hypothetical|"
    r"would|could|might|may|wish|wishes|wished|hope|hopes|hoped|dream|dreams|"
    r"dreamed|wants?|wanted|aspiring|posing)\b",
    re.IGNORECASE,
)
_PERFORMED_ROLE = re.compile(
    r"\bin\s+(?:(?:a|an|the)\s+(?:\w+\s+){0,3}(?:play|film|movie|dream)|name only)\b",
    re.IGNORECASE,
)


def biography_identity(
    prompt: str, character: Mapping[str, Any],
) -> tuple[str, str] | None:
    """Return the unchanged identity sentence and its source line, if explicit."""
    name = str(character.get("name") or character.get("full_name") or "").strip()
    if not name:
        return None
    # Accept a standalone name, optionally age and a descriptive heading suffix.
    # A joint heading or a longer name (e.g. 'Jane Smith Jr.') is not a match.
    title = re.compile(
        re.escape(name)
        + r"(?:\s*,\s*\d{1,3})?(?:\s+[—–-]\s+[^\n]+)?",
        re.IGNORECASE,
    )
    lines = prompt.splitlines()
    headings: list[tuple[int, int, str]] = []
    fence = ""
    visible: list[str] = []
    for index, line in enumerate(lines):
        marker = _FENCE.match(line)
        if marker:
            token = marker.group(1)
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = ""
            visible.append("")
            continue
        visible.append("" if fence else line)
        heading = _HEADING.match(visible[-1])
        if heading:
            headings.append((index, len(heading.group(1)), heading.group(2).replace("**", "").strip()))
    parents: list[tuple[int, str]] = []
    matches: list[int] = []
    for index, (_, level, text) in enumerate(headings):
        while parents and parents[-1][0] >= level:
            parents.pop()
        if (
            parents
            and parents[-1][0] == level - 1
            and parents[-1][1].casefold() in _BIOGRAPHY_SECTIONS
            and title.fullmatch(text)
        ):
            matches.append(index)
        parents.append((level, text))
    if len(matches) != 1:
        return None
    position = matches[0]
    start = headings[position][0] + 1
    end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
    while start < end and not visible[start].strip():
        start += 1
    paragraph: list[str] = []
    for line in visible[start:end]:
        if not line.strip():
            break
        paragraph.append(line.strip())
    text = " ".join(paragraph)
    sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    if not sentence or len(sentence) > 800:
        return None
    subjects = "|".join(re.escape(value) for value in (name, name.split()[0], "She", "He", "They"))
    declaration = re.match(
        rf"(?:{subjects})\s+(?P<verb>is|was|has been|had been|used to be|works as|worked as)"
        r"\s+(?:a|an|the)\s+(?P<identity>.+)",
        sentence,
        re.IGNORECASE,
    )
    if not declaration:
        return None
    identity = declaration.group("identity")
    # Do not scan relative clauses or somebody else's role for an occupation.
    predicate = re.split(r"\b(?:who|whose|that|which)\b|[,;.!?]", identity, maxsplit=1, flags=re.IGNORECASE)[0]
    if _UNCONFIRMED.search(predicate) or _PERFORMED_ROLE.search(predicate):
        return None
    if declaration.group("verb").casefold() not in {"works as", "worked as"}:
        role = _LIVED_ROLE.search(predicate)
        if role is None:
            return None
        before = predicate[:role.start()]
        after = predicate[role.end():]
        if re.search(r"\b(?:of|to|for|with|by|like|unlike)\b", before, re.IGNORECASE):
            return None
        if after and not re.match(r"\s*(?:$|(?:at|in|for|and|now|before|after|until|since|from)\b)", after, re.IGNORECASE):
            return None
    return sentence, f"outputs/input/prompt.md:{start + 1}"
