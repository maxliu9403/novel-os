# Originality Isolation

Use this reference whenever examples, samples, prior books, or a corpus inform
story selection. Learn mechanisms, not expression.

## Classify before extracting facts

- `reference_only`: supplied books, excerpts, sample openings, and outlines used
  to inspire a new project. This is the default for a new book, even when the
  user owns the files or says “expand this story.” Preserve the desired reading
  experience, not its named cast, distinctive scenes, or ordered revelations.
- `project_canon`: the user's identified current manuscript that they explicitly
  want continued or revised. Its verified facts and authorized prose belong to
  that project and may enter its continuity context. Do not impose reference
  isolation on the very manuscript the user asked to edit.
- `user_constraint`: independently stated preferences and approved new design
  choices. Approving an abstract mechanism does not promote its source book to
  canon. A request to retain a source's distinctive plot conflicts with
  `reference_only`; surface the conflict before proceeding, rather than silently
  relabeling the source.

An explicit “only inspiration; recreate it” instruction takes precedence over
automatic continuation routing. Reuse established material-use decisions;
do not ask for confirmation on each new file when the policy is already clear.

Keep a private record of supplied file path, material-use label, user intent,
allowed abstract mechanisms, and comparison findings. Store it outside `prompt/`
and other runtime context inputs. This is a provenance record, not another
required external-research phase. Source-specific evidence belongs here only.

## Isolation boundary

- Never put raw corpus prose, sample dialogue, titles, names, paths, summaries,
  distinctive objects, signature metaphors, or scene sequences into a runtime
  prompt.
- Never retrieve the nearest matching story for imitation, rewriting, or
  title/name substitution.
- Do not create embeddings, a vector index, semantic search, or RAG over raw
  sample chapters for generation.
- Do not preserve a distinctive reversal chain while changing surface nouns.
- Do not imitate sentence rhythm, repeated phrases, or character catchphrases.
- Do not treat multiple high-similarity samples as independent proof that one
  formula is superior.

## Allowed learning

Use only aggregate facts and controlled abstractions:

- reader jobs;
- premise dimensions;
- conflict levels and resource types;
- free-window roles;
- satisfaction and hook categories;
- failure modes and quality budgets;
- abstract structural fingerprints without source expression.

## Design review

Create the new protagonist's lived history, goal, resources, relationship power,
rule limits/costs, initiating choice, evidence/reveal progression, and ending
from the approved reader promise. Design major events forward from those new
choices. Do not keep the source outline open as a scene-by-scene writing plan.

Before approval, ask:

1. Does the premise still work after removing every sample-specific object?
2. Does the protagonist's agency arise from this character's lived history?
3. Are proof, deadline, action cost, and belonging combined differently from
   high-risk structural clusters?
4. Does the free-window action sequence reflect this premise rather than a
   remembered sample?
5. Can every key scene be justified from the approved contract alone?

Compare both the opening and the whole-book turning-point chain privately
against the references. Check character-role mappings, distinctive objects,
the order and mechanism of revelations, and how the climax is resolved. If
the sequence still maps one-to-one, fail the review even when names, setting,
or wording differ. Return to premise design, rebuild the causal chain, then
review again. A count of changed dimensions or a similarity score alone is not
proof of originality. Ordinary genre conventions may remain; the goal is
independent dramatic causality, not random decoration.

Record concrete comparison findings and their limits. Do not promise universal
uniqueness or a legal clearance from a review of the supplied material alone.

## Runtime handoff

For every genre, the runtime receives only the approved original story and
chapter contracts, recent verified outcomes, and the current project's canon;
commercial work also carries `COMMERCIAL_STORY_JSON`. Its `workshop_trace` is
selected-design-only and source-free. It does not receive the private material
record, rejected approaches, corpus snapshot, source lookup, or sample identity.

Check the whole assembled prompt and every attachment, including cover metadata,
decision history, summaries, and instruction appendices. Isolation of one JSON
block is insufficient if the raw chapters are pasted elsewhere. Inspect the
written opening and major payoffs again before delivery for accidental reuse;
passing the design review does not pre-approve future prose.
