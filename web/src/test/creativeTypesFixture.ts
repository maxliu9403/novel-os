import type {
  CreativeProfile,
  CreativeRecord,
  CreativeLibrary,
  CreativeSummary,
} from "../api/creativeTypes";
export function typeRecord(
  id = "ct_marriage_betrayal",
  name = "婚姻背叛",
  aliases = ["Marriage Betrayal"],
): CreativeRecord {
  const profile: CreativeProfile = {
    schema: "creative-type-profile.v1",
    profile_id: id,
    version: 1,
    name,
    aliases,
    dimension: "marriage",
    summary: "在关系承诺与行动的矛盾中设计选择与代价。",
    applicability: { readers: [], markets: [], languages: [], lengths: [] },
    reader_promises: [
      {
        id: "promise_choice",
        text: "关系变化来自人物选择，不预设离婚或复合。",
        importance: "recommended",
        stages: ["brainstorm", "story_design"],
      },
    ],
    mechanisms: [],
    character_dynamics: [],
    voice_guidance: [],
    pitfalls: [],
    diagnostics: [],
    combinations: [],
    publication_mapping: {
      status: "unmapped",
      catalog_version: "novel-types.2026-10",
      primary_genre_ids: [],
      facet_ids: [],
    },
  };
  return {
    id,
    revision: 0,
    status: "enabled",
    origin: "builtin",
    coverage: "specialized_guidance",
    updated_at: "2026-11-01T00:00:00Z",
    draft: profile,
    active_version: 1,
    versions: [
      {
        profile: structuredClone(profile),
        sha256: "a".repeat(64),
        created_at: "2026-11-01T00:00:00Z",
      },
    ],
  };
}
export function typeSummary(r: CreativeRecord): CreativeSummary {
  const p =
    r.versions.find((v) => v.profile.version === r.active_version)?.profile ||
    r.draft;
  return {
    id: r.id,
    revision: r.revision,
    status: r.status,
    origin: r.origin,
    coverage: r.coverage,
    updated_at: r.updated_at,
    name: p.name,
    summary: p.summary,
    dimension: p.dimension,
    aliases: p.aliases,
    mapping_status: p.publication_mapping.status,
    active_version: r.active_version,
    source_sha256: r.versions.at(-1)?.sha256 || null,
    has_draft_changes: false,
  };
}
export function typeLibrary(
  records = [
    typeRecord(),
    typeRecord("ct_fantasy", "奇幻", ["Fantasy"]),
    typeRecord("ct_werewolf", "狼人", ["Werewolf"]),
  ],
): CreativeLibrary {
  return {
    items: records.map(typeSummary),
    dimensions: { marriage: "婚姻与亲密关系", world: "类型与世界" },
    publication_catalog: {
      catalog_version: "novel-types.2026-10",
      axes: { primary_genres: [], story_types: [], tones: [], settings: [] },
    },
  };
}
