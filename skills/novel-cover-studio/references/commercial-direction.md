# Commercial Cover Direction

The output is an original mobile-first serialized-fiction cover, not an imitation of a published cover or artist.

## Visual hierarchy

1. The exact title is readable at feed-thumbnail size and appears exactly once.
2. One protagonist action or decision carries the core conflict.
3. One relationship or power contrast makes the stakes legible without synopsis text.
4. One subordinate signal supports a key secondary task; it never competes with the focal action.
5. The fictional world appears through setting, costume, objects, era, light, or social pressure rather than real landmarks.

## Conflict tableau and emotional geography

Translate synopsis-level conflict into one visible cause-and-consequence tableau. The foreground carries the emotional consequence, threatened bond, or protagonist decision. A middle or background plane reveals the causal relationship action when approved characters and evidence support it. Connect the planes through gaze, body direction, distance, touch, refusal, departure, or interrupted movement. The viewer should understand both what hurts and what caused it before reading a synopsis.

Emotional atmosphere must come from behavior and relationship geometry, not from a generic sad face, a color wash, or decorative symbolism. Keep the focal character active even in pain: choosing, refusing, leaving, protecting, witnessing, reaching, or freezing at a consequential instant. An opposing pair may show proximity, secrecy, exclusion, attention, or alignment only to the degree supported by story evidence. Use one continuous scene, not a split panel, explanatory collage, or flat group portrait.

Use a portrait `2:3` canvas. Preserve the provider's native resolution rather than forcing a pixel count; the default request is `2048x3072`. Export covers as JPEG or PNG, preferring JPEG for the default delivery. Reserve stable title space, keep faces and decisive objects clear of typography, and avoid collage layouts, UI chrome, logos, watermarks, author lines, taglines, and subtitles.

## English / United States market profile

When `language` or `market_scope` identifies English-language or United States / North American release, add this profile to every image prompt:

- **Casting and identity:** preserve only identity traits explicitly established by the approved story. Do not infer or invent ethnicity, nationality, or other identity traits from the release market. Keep faces, hair, wardrobe, and styling consistent with the story's stated character and setting facts.
- **Finish:** direct a premium US commercial fiction magazine cover and entertainment key art. Use editorial photography, cinematic production design, controlled color grading, realistic skin texture, and a polished campaign finish. Avoid stock-photo posing, generic AI portrait lighting, and flat character sheets.
- **Story signal:** make the cover read as a scene, not a headshot. Freeze one consequential moment with visible action, reaction, and stakes; show the power relationship through gaze, gesture, spatial distance, and one meaningful prop. When the story supports multiple conflict participants, let the foreground show the emotional cost and a secondary plane show the causal relationship action. The image should imply what just happened and what is about to happen.
- **Typography:** reserve a clean title-safe zone and use Western editorial typography: a premium display serif or refined modern grotesk selected for the genre, with deliberate hierarchy, precise kerning, restrained tracking, and strong thumbnail contrast. Render only the exact approved title; never add taglines or extra copy.

Do not apply this typography profile to a non-Western market unless the approved handoff explicitly requests it. Market profile changes art direction only; story facts, character identities, fictional locations, and the protagonist's agency remain authoritative.

## Distinct concept families

Choose 3-5 concepts with different focal scenes, camera distance, pressure geometry, palette, and emotional temperature. The default families are:

- `protagonist_confrontation`: foreground agency against compressed opposition;
- `decisive_story_node`: the irreversible event frozen at its turning instant;
- `symbolic_evidence`: protagonist-led close composition around one consequential object;
- `world_relationship_pressure`: environmental lines and relationship positions tighten around the protagonist;
- `emotional_reversal`: the earned shift from pressure to visible control.

Changing only color or pose is not distinct. Each concept should make a different truthful promise about the same book.

## Prompt quality gate

Every generation prompt names the audience, genre, core conflict, focal scene, composition, palette, fictional world signals, one secondary signal, emotional promise, exact title, and title language. For English / United States market branches it also preserves only explicitly established identity traits and includes a premium US magazine/key-art finish, a single visible story moment, and Western editorial typography. Reject a concept when it depends on real place names, extra copy, inferred identity traits, unsupported character facts, multiple competing scenes, hidden protagonist agency, illegible title space, or a spoiler outside the approved handoff.
