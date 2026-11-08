import type {CreativeRecord, CreativeSummary, CreativeLibrary, KnowledgeDocument} from '../api/creativeTypes';
export function typeRecord(id='genre.romance',name='Romance',aliases:string[]=[]):CreativeRecord {
  const kind=id.startsWith('genre.')?'genre':'tag';
  const document:KnowledgeDocument={audience_guidance:{schema:"audience-guidance.v1",audience_channels:["female"],guides:{female:"让关系、主体性与代价进入具体人物选择。"},original_sha256:"b".repeat(64)},id,kind,name,aliases,genre_ids:kind==='tag'?['genre.romance']:[],sections:{'核心承诺':'通过人物选择与代价改变关系。'},missing_sections:[],appendices:[]};
  const draft=`---\nschema: novel-knowledge.v1\nid: ${id}\nkind: ${kind}\nname: ${name}\n---\n\n## 核心承诺\n\n通过人物选择与代价改变关系。\n`;
  return {id,kind,revision:0,status:'enabled',origin:'builtin',updated_at:'2026-11-05T00:00:00Z',draft,active_version:1,document,
    versions:[{version:1,markdown:draft,sha256:'a'.repeat(64),created_at:'2026-11-05T00:00:00Z'}]};
}
export function typeSummary(r:CreativeRecord):CreativeSummary {const d=r.document!;return {audience_channels:d.audience_guidance?.audience_channels,audience_ready:!!d.audience_guidance,audience_issue:null,id:r.id,kind:r.kind,revision:r.revision,status:r.status,origin:r.origin,updated_at:r.updated_at,active_version:r.active_version,source_sha256:r.versions.at(-1)?.sha256??null,name:d.name,aliases:d.aliases,genre_ids:d.genre_ids,promise:d.sections['核心承诺'],has_draft_changes:false,missing_sections:[]};}
export function typeLibrary(records=[typeRecord(),typeRecord('tag.betrayal','背叛',['Betrayal']),typeRecord('tag.second-chance','第二次机会')]):CreativeLibrary{return {items:records.map(typeSummary)};}
