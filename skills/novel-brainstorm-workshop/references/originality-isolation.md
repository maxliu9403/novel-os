# Originality Isolation

Use this reference whenever examples, samples, prior books, or a corpus inform
story selection. Learn mechanisms, not expression.

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

Before approval, ask:

1. Does the premise still work after removing every sample-specific object?
2. Does the protagonist's agency arise from this character's lived history?
3. Are proof, deadline, action cost, and belonging combined differently from
   high-risk structural clusters?
4. Does the free-window action sequence reflect this premise rather than a
   remembered sample?
5. Can every key scene be justified from the approved contract alone?

If a design is too close, change at least six controlled dimensions and the
ordered free-window actions. Ordinary genre conventions may remain; the goal
is structural distance from distinctive combinations, not novelty by random
surface decoration.

## Runtime handoff

The runtime receives only the approved `COMMERCIAL_STORY_JSON`, the current
story and chapter contracts, recent verified outcomes, and current canon. It
does not receive this corpus snapshot, a source lookup result, or a matching
sample identity.
