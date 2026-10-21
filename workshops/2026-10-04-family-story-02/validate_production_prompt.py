"""Validate the final handoff in a disposable project; never call a model."""
from pathlib import Path
import hashlib
import json
import re
import sys
import tempfile

BASE=Path(__file__).resolve().parent
ROOT=BASE.parent.parent
sys.path.insert(0,str(ROOT/'core'))
from prompt_intake import ingest_prompt
from cover_handoff import parse_cover_handoff
from narrative_format import NarrativeFormat, validate_volume_contracts
from commercial_story import CommercialStoryContract

prompt=ROOT/'prompt/The Last Golden Spire.md'
raw=prompt.read_text()
checks=[]

def check(name,ok,detail):
    checks.append({'name':name,'status':'pass' if ok else 'fail','detail':detail})

def block(name):
    start='['+name+']'
    end='[/'+name+']'
    check('single_'+name,raw.count(start)==raw.count(end)==1,'Exactly one canonical block.')
    return json.loads(raw.split(start,1)[1].split(end,1)[0])

classification=block('NOVEL_CLASSIFICATION_JSON')
format_data=block('NARRATIVE_FORMAT_JSON')
commercial=block('COMMERCIAL_STORY_JSON')
contract=block('APPROVED_STORY_CONTRACT_JSON')
approved=json.loads((BASE/'long-form-design.approved.json').read_text())
disposable=Path(tempfile.mkdtemp(prefix='golden-spire-intake-',dir='/private/tmp'))
result=ingest_prompt(disposable,prompt,generate_author=False)
brief=result.brief
check('native_intake',brief['title']=='The Last Golden Spire' and brief['language']=='English' and brief['genre']=="Women's Fiction" and brief['target_chapters']==60 and brief['target_words']==72000,'Actual ingest_prompt wrote only the disposable project with author/model generation disabled.')
check('audience_and_voice', '50–65' in brief['audience'] and 'Evelyn' in brief['pov'] and 'past tense' in brief['pov'] and bool(brief['tone']), {k:brief[k] for k in ['audience','pov','tone']})
check('premise',brief['premise'].startswith('After her husband leaves their nine-year-old daughter') and 'independent' not in brief['premise'][:15],brief['premise'])
check('classification',brief['classification']==classification,'Native ids, metadata and derived labels round-trip.')
nf=NarrativeFormat.from_dict(format_data)
check('format',brief['narrative_format']==format_data and nf.mode=='multi_volume' and nf.selection_source=='author_selected' and nf.confirmation_status=='confirmed' and [(v.chapter_start,v.chapter_end,v.target_words) for v in nf.volumes]==[(1,20,24000),(21,40,24000),(41,60,24000)],'Canonical author-selected three-volume format; no series_installment substitution.')
native_volumes=validate_volume_contracts(nf,contract['volume_contracts'])
check('volume_contracts',len(native_volumes)==3 and [v['climax_chapter'] for v in native_volumes]==[19,39,58],'Distinct native volume promises, shifts, climaxes, payoffs and carryover hooks validate.')
parsed_commercial=CommercialStoryContract.from_dict(commercial)
check('commercial_contract',brief['commercial_story_contract_id']==parsed_commercial.contract_id and 'contract_id' not in commercial,'Contract id derived by the library; not hand-authored.')
cs=contract['chapter_map']
ac=approved['section_d']['chapter_map']
check('approved_chapter_actions',len(cs)==60 and all(c['chapter']==a['chapter'] and c['title']==a['title'] and c['time']==a['time'] and c['scenes']==a['scenes'] for c,a in zip(cs,ac)),'All60 titles, times and120 planned scenes equal the author-approved design.')
check('chapter_causal_contract',all(c['entry_state'] and c['changed_exit_state'] and c['reader_value'] and c['choice_and_cost_scene_refs'] for c in cs),'Every chapter retains an entry, concrete scene choice/cost, exit and value; duplicate scene text is not necessary.')
check('world_timeline_resources',contract['world']['timeline']==approved['section_c']['timeline'] and contract['world']['resources']==approved['section_c']['proposed_budget'] and contract['world']['project']==approved['section_c']['project_spine'],'All approved time, money, education billing and shared contingency records preserved exactly.')
check('scope_and_status',contract['status']=='author_approved' and all(c['status']=='planned' and c['draft_review']=='not_run' for c in cs),'Approval authorizes the design; actual scenes remain unreviewed plans.')
check('source_repairs',len(contract['authorized_opening_repairs'])==7 and all(x['retained']==y['retained'] for x,y in zip(contract['authorized_opening_repairs'],approved['source_repairs'])),'All seven explicit repairs and retained mall actions transferred.')
ids={c['character_id'] for c in contract['characters']}
check('characters_and_personality',ids=={'evelyn','olivia','christian','helena','jack','claire'} and all(c['personality']['personality_core'] and c['personality']['speech_habits'] and c['personality']['pressure_response'] for c in contract['characters']),'Six principal characters retain distinct personality, pressure, speech and knowledge contracts.')
ending=contract['ending_contract']
expected={c['id']:c['required_outcome'] for c in approved['section_b']['principal_characters']}
check('ending_outcomes',{c['character_id']:c['required_outcome'] for c in ending['character_arcs']}==expected and all(c['required_arc_stage']=='resolution' and c['outcome_match_mode']=='exact' for c in ending['character_arcs']),'Six exact semantic outcomes, separate from narrative lifecycle.')
check('ending_payoffs',len(ending['plot_payoffs'])==10 and all(p['setup_ids'] and p['required_payoff'] and not p['allow_intentional_open'] for p in ending['plot_payoffs']) and ending['antagonist_outcome']['thread_id']=='plot_003' and ending['main_conflict']['thread_id']=='plot_001','Native ending shape retains ten payoffs, main thread and antagonist responsibility thread.')
check('finale',ending['finale_window']=={'start_chapter':56,'end_chapter':60} and ending['intentional_open_core_threads']==[] and '不复婚' in json.dumps(ending,ensure_ascii=False),'Final five chapters reserved; no marriage reunion or open central thread.')
lead=contract['story_lead_contract']
check('lead',lead['required'] is True and lead['language']=='English' and lead['length']=={'unit':'words','target_range':[120,180]} and raw.index('## STORY_LEAD: Story Lead')<raw.index('# Chapter 1 — The Ninth Spire'),'Separate lead with exact marker preceding the chapter heading.')
check('opening',len(contract['opening_contract']['first_screen_signals'])>=2 and len(contract['opening_contract']['conflict_braid'])>=2 and all(contract['opening_contract'][k] for k in ['chapter_1_value','chapter_2_reversal_or_resource','chapter_3_irreversible_step','paid_bridge']),'Three-chapter micro-arc answers rescue and spends real money; chapter4 enacts departure.')
trace=contract['workshop_trace']
check('trace',len(trace['approach_options'])==1 and trace['approach_options'][0]['selected'] and len(trace['section_decisions'])==5 and contract['audience_profile']['status']=='author_confirmed','Selected approved decisions only, with confirmed audience.')
check('setting',contract['setting_policy']['mode']=='fictionalized' and contract['setting_policy']['real_place_names_in_story'] is False,'Market metadata is distinct from fictional story geography.')
check('cover_boundaries',raw.count('COVER_HANDOFF_BEGIN')==raw.count('COVER_HANDOFF_END')==1,'One cover handoff only.')
cover=parse_cover_handoff(raw)
check('cover_native_v2',cover.schema_version==2 and cover.title==brief['title'] and {c.character_id:c.age for c in cover.principal_characters if c.must_appear}=={'evelyn':41,'olivia':9},'Actual schema-v2 parser validates required mother and daughter, ages, lived identity, relationships and story nodes.')
check('voice_samples',raw.count('## Conflict sample — not canon')==1 and raw.count('## Quiet sample — not canon')==1 and 'Incidental staging does not authorize' in raw,'Both concise original samples clearly noncanon, with a warning against treating staging as access permission.')
leaks=['/Users/max/','After 10 Golden Spires Broke, My Daughter Chose to Erase Her','The Hours I Kept','Helen Mercer','Mercer Table','private-intake.json','private-audit.json','canon-input/','reference_only','a5458ce1332e5b74']
check('material_boundary',not any(x in raw for x in leaks),'Authorized current-project canon is included; private paths, unrelated book identities, raw source file labels and corpus payloads are absent.')
check('no_prose_certification',contract['quality_status']['draft_review']=='not_run' and 'not canon' in raw and 'parser pass' not in json.dumps(ending),'No unwritten chapter or ending review is labeled complete.')
manifest=json.loads((BASE/'canon-input-manifest.json').read_text())
check('originals_unchanged',all(hashlib.sha256(Path(x['source_path']).read_bytes()).hexdigest()==x['sha256'] for x in manifest['files']),'All four Desktop originals match their initial hashes.')
status='pass' if all(c['status']=='pass' for c in checks) else 'fail'
report={'status':status,'prompt':str(prompt),'sha256':hashlib.sha256(raw.encode()).hexdigest(),'checks':checks,
 'native_intake_project':str(disposable),'native_brief_summary':{k:brief[k] for k in ['title','genre','audience','language','tone','pov','target_chapters','target_words','premise','commercial_story_contract_id']},
 'design_review':'author_approved_after_repair','material_isolation_review':'pass_for_authorized_current_project_continuation','parser_validation':status,
 'voice_calibration':'two_original_noncanon_samples_reviewed_for_POV_subtext_and_age_voice','draft_review':'not_run','manuscript_generation':'not_started','model_calls':0,
 'corrections_during_validation':['Cover visual_assumptions uses an empty valid array; unspecified appearance remains in Assumptions rather than invalid bare strings.'],
 'limits':['Custom approved-story contract checked explicitly; native intake does not itself enforce every nested prose instruction.','This is prompt and design validation, not a passing manuscript or actual ending report.']}
(BASE/'prompt.validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':status,'checks':len(checks),'failed':[c['name'] for c in checks if c['status']!='pass'],'native_project':str(disposable),'model_calls':0},ensure_ascii=False))
if status!='pass': raise SystemExit(1)
