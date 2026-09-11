"""Shared photographic medium contract for cover planning, compilation and review."""
import re


PHOTOGRAPHIC_PROFILE = "cover-profiles.v8"
PHOTOGRAPHIC_RENDER_CONTRACT = (
    "Use professional film campaign photography with believable people, age-true skin and hair, "
    "subtle tonal variation and motivated light. Preserve identities; no beautification or exaggerated aging. "
    "Use natural focus falloff, readable key faces/gestures, soft highlight roll-off and contact shadows; "
    "avoid waxy skin, etched pores, glassy eyes, HDR halos and uniformly sharp cutout edges. "
    "Keep designed color and title hierarchy; no added grit or blur to fake realism. "
    "No oil painting, gouache, watercolor, illustration, relief or CGI/3D people. Art may be a story prop, "
    "not the rendering medium. Keep typography readable."
)
HUMAN_PERFORMANCE_CONTRACT = (
    "Direct one captured instant, not a posed tableau: each person has a scene goal, a specific eyeline "
    "and a distinct reaction beat. Use plausible weight, relaxed joints and real hand/prop contact. "
    "Emotion lives in subtle asymmetric expressions and interrupted movement, not forced smiles or "
    "matching frowns. Cast context is not an extra simultaneous action; render the frozen event only. "
    "Do not present every prop to camera."
)

RENDER_POLICY = {
    "version": "photographic-cover.v3",
    "medium": "live_action_photography",
    "requirements": PHOTOGRAPHIC_RENDER_CONTRACT,
    "human_performance": HUMAN_PERFORMANCE_CONTRACT,
}

_NONPHOTO = re.compile(
    r"\b(?:illustrat\w*|gouache|linocut|watercolou?r\w*|tempera|painterly|"
    r"painted (?:human |realistic |naturalistic )?(?:figures|people|faces)|"
    r"oil[ -]paint\w*|oil on canvas|digital painting|hand[ -]drawn|cartoon|anime|"
    r"paper[ -]relief|sculpt\w*|cgi|3[ -]?d(?:[ -]render\w*)?)\b",
    re.I,
)
_PHOTO = re.compile(r"\b(?:photograph\w*|photo[ -]?realis\w*|live[ -]action)\b", re.I)


def photographic_medium_issue(medium: str) -> str:
    """Check medium declarations, not story props (a potter may paint or sculpt)."""
    for match in _NONPHOTO.finditer(medium):
        start = max(medium.rfind(mark, 0, match.start()) for mark in (".", ";", "\n"))
        prefix = re.split(r"\b(?:but|however|instead|yet)\b", medium[start + 1:match.start()], flags=re.I)[-1]
        if not re.search(r"\b(?:no|not|never|avoid|without|exclude)\b", prefix, re.I):
            return f"non-photographic medium: {match.group()}"
    if not _PHOTO.search(medium):
        return "art_style must explicitly specify live-action photography"
    return ""
