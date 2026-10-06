"""Build this author-approved novel's handoff; never starts a model."""
from pathlib import Path
import copy
import json
import sys

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent.parent
sys.path.insert(0, str(ROOT / 'core'))
from novel_classification import NovelClassification
from narrative_format import infer_narrative_format, NarrativeFormat, validate_volume_contracts
from commercial_story import CommercialStoryContract

approved = copy.deepcopy(json.loads((BASE / 'long-form-design.proposal.json').read_text()))
approved['artifact_type'] = 'author_approved_long_novel_design'
approved['approval_status'] = 'approved'
approved['approval_evidence'] = {'date': '2026-10-04', 'author_reply': '同意', 'scope': 'Entire presented A–E design, including title, audience, ages, new details and ending.'}
approved['title_status'] = 'author_confirmed'
approved['format']['word_target_status'] = 'author_confirmed'
approved['audience_profile']['status'] = 'author_confirmed'
approved['ending_contract']['status'] = 'approved'
approved['section_e']['design_review'] = 'approved_after_repair'
approved['section_e']['unapproved_new_facts'] = []
approved['section_c']['proposed_budget']['status'] = 'author_confirmed_fictional_values_not_market_quotes'
for c in approved['section_b']['principal_characters']:
    if 'age_status' in c:
        c['age_status'] = 'author_confirmed'
(BASE / 'long-form-design.approved.json').write_text(json.dumps(approved, ensure_ascii=False, indent=2)+'\n')
d = approved
title = d['working_title']
premise = "After her husband leaves their nine-year-old daughter behind during a mall shooting, Evelyn, a landscape professional returning after two years away, leaves the marriage. To build a lasting home she must turn a job offer into dependable income, correct his unauthorized use of her professional name, own her mistakes at work, and let her daughter have a life beyond the betrayal."
classification = NovelClassification.from_dict({
    'schema_version': 'novel-classification.v1', 'catalog_version': 'novel-types.2026-09',
    'primary_genre_id': 'womens_fiction', 'secondary_genre_ids': ['family_drama','contemporary_realism'],
    'story_type_ids': ['domestic_betrayal','marriage_crisis'], 'tone_ids': ['angst','emotional_realism'],
    'setting_ids': ['family_domestic','social_life'], 'audience': {'channel':'female','age_band':'midlife'},
    'length': {'form':'long','chapter_band':'chapters_51_100'}, 'source':'author_confirmed','confidence':1,
})
narrative = infer_narrative_format(classification, chapters=60, target_words=72000, premise=premise,
    raw_prompt=json.dumps(d['section_d']['volumes'],ensure_ascii=False), explicit_length=True,
    source='author_selected', mode='multi_volume', volume_count=3).to_dict()
commercial = {
    'schema_version':1,
    'reader_contract': {'audience_age_band':'50–65','life_contexts':['Marriage and its accumulated disappointments','Caregiving and invisible emotional work','Professional return','Financial independence','Rebuilding an adult life'],
        'emotional_jobs':{'recognition':5,'anger':4,'pity':2,'regret':4,'belonging':4,'agency':5,'hope':5}},
    'premise_engine': {'protagonist_life_stage':'active_parenting','protagonist_desire_beyond_escape':'Build useful landscapes, maintain a dependable home, recover an adult creative life, and respect her daughter as a separate person.',
        'invisible_labor':'emotional_management','sacred_asset':'child_safety','boundary_transfer':'time','beneficiary_role':'favored_child',
        'proof_type':'financial_record','deadline_type':'child_harm_threshold','agency_source':'professional_skill','agency_seeded_in_chapter':2,
        'action_cost':'housing','belonging_anchors':['self','child','work','friend'],'relationship_shape':'parent_child_displacement'},
    'conflict_ladder':[
        {'level':1,'resource_dimension':'time','protagonist_action':'Try to complete the garden display after Christian leaves, then allow Olivia to stop the shared model project.','observable_consequence':'The ninth spire is removed; the child is no longer required to preserve a false appearance of happy cooperation.'},
        {'level':2,'resource_dimension':'money','protagonist_action':'Challenge the proposal to reduce Olivia’s learning support.','observable_consequence':'The supposedly necessary sacrifice falls on their child while Christian keeps outside commitments.'},
        {'level':3,'resource_dimension':'relationship','protagonist_action':'Read the $12,000 bill and note, preserve records actually obtained, and answer the recruitment email.','observable_consequence':'A specific resource double standard is visible; Evelyn takes a seeded route toward earned income.'},
        {'level':4,'resource_dimension':'body','protagonist_action':'After receiving the job offer, respond to the shooting alert, call Christian and locate Olivia through site staff.','observable_consequence':'Calls do not provide safety; Evelyn finds the child and learns what she directly experienced. The short-term rescue question is answered.'},
        {'level':5,'resource_dimension':'space','protagonist_action':'Pay the first night of a three-night stay and prepare to leave with Olivia.','observable_consequence':'Cash is spent and a real place to go exists; chapter 4 completes the move and starts the practical consequences.'},
    ],
    'free_trial_arc':{'chapter_count':3,'recognition_event':'Christian leaves the garden event; Evelyn accepts the ninth spire from Olivia.','pattern_proof':'The $12,000 equestrian bill and note contrast with proposed cuts to learning support.','first_boundary_test':'Permit Olivia to stop the model project and reenter recruitment instead of maintaining the family appearance.','local_payoff':'Evelyn earns the director offer and finds Olivia alive; the child receives immediate care.','irreversible_choice':'Spend accessible cash on the first night of a separate stay.','visible_cost':'The first paid night is a real expenditure and reduces the finite transition reserve.','next_concrete_expectation':'Pack, leave and actually enter the booked accommodation in chapter 4.','action_sequence':['recognize','verify','test_boundary','protect','accept_cost']},
    'quality_budgets':{'consecutive_humiliation_scenes_max':2,'identical_hook_type_max':1,'unseeded_rescue_max':0,'child_voice_age_check':True,'institutional_plausibility_check':True,'protagonist_causes_major_turn':True},
}
# Exercise native validation without hand-authoring a contract id.
CommercialStoryContract.from_dict(commercial)

personality = {
 'evelyn': {'personality_core':'Observant, capable, caring, proud; competence sometimes becomes overcontrol.','pressure_response':'Takes on too much before admitting a limit; increasingly asks, delegates and owns errors.','visible_behaviours':['Notices who must walk around an obstacle','Checks a promised time against an actual schedule','Makes room on a crowded table instead of explaining a moral'], 'decision_style':'Evidence and care, initially distorted by a need to prove herself indispensable.','speech_habits':'Concrete questions, unfinished polite explanations early, fewer justifications later.','emotional_expression':'Attention shifts, hands occupied with practical tasks, occasional dry humor; grief need not become a speech.','fear':'Her child will lose safety if she stops controlling every outcome.','boundary':'Will not supply false professional assurances or ask her child to validate the divorce.','resources':['Former professional reputation','An earned job offer','Documented accessible cash','Bounded paid care and friendship']},
 'olivia': {'personality_core':'Attentive, funny, inventive, sometimes stubborn; a child rather than an exemplary survivor.','pressure_response':'Goes quiet or tries to become easy to care for, but can also be bored, annoyed and unfair.','visible_behaviours':['Makes paper figures','Argues about a rocket scene','Asks for cue cards','Stops a conversation she does not want'], 'decision_style':'Immediate and concrete; sees her own intention sooner than its effect on a friend.','speech_habits':'Short specific requests, odd jokes, occasional evasions; no financial analysis or adult verdicts.','emotional_expression':'May keep playing after a difficult question; delight and fear can coexist.','fear':'An adult leaving means she was too difficult to stay for.','boundary':'Her memories and interest are hers; adults retain legal and safety decisions.','resources':['Mara and school support','Maya and shared craft','Predictable adult arrangements']},
 'christian': {'personality_core':'Sociable, eager to be needed, practiced at explaining himself; selective responsibility.','pressure_response':'Frames the intent as proof of virtue, offers money or familiar affection, eventually accepts specific obligations.','visible_behaviours':['Asks for reassurance before discussing costs','Uses a familiar tone as if it were consent','Later reports a schedule problem before the agreed time'], 'decision_style':'Short-term rescue and reputation first, delayed cost assigned to Evelyn.','speech_habits':'Early explanations about urgency and misunderstanding; later names the act and the arrangement without a demand for thanks.','emotional_expression':'Discomfort when his account fails to restore the old response.','fear':'Losing the image and convenience of a good husband and father.','boundary':'No claim to reconciliation, technical help or child reassurance through payment.','resources':['Own small firm and actual staff','Completed small-job income','A limited adult role in child arrangements']},
 'helena': {'personality_core':'Direct about her needs, protective of Carter, willing to overlook what assistance costs another household.','pressure_response':'Treats costly help as established, then has to budget without it.','visible_behaviours':['Requests another quarter of equestrian support','Confirms the end of expensive help herself'], 'decision_style':'Her household’s immediate needs and the comfort of Christian’s attention.','speech_habits':'Requests with practical detail and emotional implications; not a mastermind confession.','emotional_expression':'Embarrassment and defensiveness can accompany a real need.','fear':'Losing reliable material and emotional help.','boundary':'Cannot authorize Christian’s conduct or make either child accountable.','resources':['Her own household decisions','Appropriate adult support, not an unlimited benefactor']},
 'jack': {'personality_core':'Professionally loyal, discerning and bounded.','pressure_response':'Asks what services actually exist rather than promising a favored outcome.','visible_behaviours':['Requests the current role in the bid','Accepts a written correction'], 'decision_style':'Relationship enables an introduction; capability determines a bid.','speech_habits':'Short professional questions with a defined answer.','emotional_expression':'Respect through appropriate limits.','fear':'A recommendation being mistaken for a guarantee.','boundary':'No authority over the new employer, marriage, or custody.','resources':['Spruce Valley role','Knowledge of Evelyn’s past reputation']},
 'claire': {'personality_core':'Warm, practical, candid, with her own family and interests.','pressure_response':'Offers a specific favor and states the part she cannot cover.','visible_behaviours':['Names a pickup time','Attends a sketch class','Accepts Evelyn’s help with her own errand'], 'decision_style':'Reciprocity and realistic time.','speech_habits':'Everyday detail and a little humor; not therapeutic lectures.','emotional_expression':'Conversation and shared activity, sometimes impatience.','fear':'Friendship becoming another unlimited obligation.','boundary':'Cannot supply housing or recurring overnight childcare on demand.','resources':['A few agreed favors','Adult companionship','An independent home and schedule']},
}
characters=[]
for original in d['section_b']['principal_characters']:
    c=copy.deepcopy(original)
    c['character_id']=c.pop('id')
    if 'age_proposal' in c:
        c['age']=c.pop('age_proposal')
    c['age_status']='author_confirmed'
    c['personality']=personality[c['character_id']]
    c['source_status']='verified_current_project_facts_plus_author_approved_continuation'
    characters.append(c)
relationships=[
 {'id':'rel_01','participants':['evelyn','olivia'],'power':'Adult controls material and safety decisions; child owns feelings and age-appropriate preferences.','trust_evidence':[7,18,33,42,52,58,60],'suspicion_evidence':[3,17],'shared_risk':'Anxious protection can turn into controlling the child’s whole life.','boundary':'Olivia never decides the marriage or negotiates safety with her father.','next_change':'Evelyn lets the child stop the model, then learns to accept the child’s independent choices.'},
 {'id':'rel_02','participants':['evelyn','christian'],'power':'Old habits assume her time and reputation are available; she gains income without gaining control of his business.','trust_evidence':[38,47],'suspicion_evidence':[1,2,3,21,37],'shared_risk':'Finite resources and their child’s needs continue after separation.','boundary':'Money and apology do not buy reconciliation; his improved acts are acknowledged without renewing the marriage.','next_change':'Written refusal, correction of the bid, refusal of conditional money, final choice at 40 and formal result at 49.'},
 {'id':'rel_03','participants':['olivia','christian'],'power':'Adults carry arrangements; Olivia can express a wish without having to forgive.','trust_evidence':[47,54,58],'suspicion_evidence':[1,3,36],'shared_risk':'He may ask her to repair his self-image.','boundary':'No demand for reassurance, hug, photo or expanded pickup access as a reward for paying.','next_change':'Limited contact is tested by behavior across months.'},
 {'id':'rel_04','participants':['christian','helena'],'power':'Financial aid and being needed reinforce emotional expectations.','trust_evidence':[],'suspicion_evidence':[2,30],'shared_risk':'His outside commitments consume resources owed elsewhere.','boundary':'No inferred sexual affair, secret paternity or manufactured emergency; Carter is not culpable.','next_change':'Helena asks for renewed equestrian support at 30 and confirms its end at 35.'},
 {'id':'rel_05','participants':['evelyn','jack'],'power':'Her reputation earned an invitation, not control of the award.','trust_evidence':[20,23,35],'suspicion_evidence':[21],'shared_risk':'A recommendation may be misrepresented as a service commitment.','boundary':'Separate organizations, independent award; no rescue romance.','next_change':'Correct the future role, then leave the result to the bidding process.'},
 {'id':'rel_06','participants':['evelyn','claire'],'power':'Both adults have needs and limited time.','trust_evidence':[6,10,18,45,55,60],'suspicion_evidence':[],'shared_risk':'A crisis friendship can become one-sided.','boundary':'Specific favors, paid routine care, no unlimited overnight childcare.','next_change':'Evelyn completes an errand, attends sketch class and admits envy to an adult friend.'},
 {'id':'rel_07','participants':['olivia','maya'],'power':'Two peers own different parts of a shared piece.','trust_evidence':[10,26,42,52,58],'suspicion_evidence':[28,52],'shared_risk':'One child may erase the other’s work while intending to improve it.','boundary':'Learning support does not exempt Olivia from fair work or apology.','next_change':'She remakes two altered panels and gives up some favorite design so the play belongs to both.'},
 {'id':'rel_08','participants':['evelyn','noah','ruth','lena'],'power':'Ruth owns approvals; Noah has design authorship; Lena knows maintenance; Evelyn leads within those limits.','trust_evidence':[18,25,28,34,39,56,57],'suspicion_evidence':[27,32],'shared_risk':'An inhabited site must work within a finite budget.','boundary':'Her expertise cannot replace qualified safety, engineering or contract review.','next_change':'She owns the early staging error and earns a bounded role through actual delivery.'},
]
secrets=[
 {'id':'secret_01','truth':'Evelyn recommended Christian for the private bidding invitation.','initial_knowers':['evelyn','jack'],'initial_nonknowers':['christian'],'clue_chapters':[2],'payoff_chapters':[11,20,23,35],'fair_misreading':'An invitation looks like evidence that the firm can deliver; it is not a guarantee.'},
 {'id':'secret_02','truth':'Christian paid $12,000 for Carter’s quarter of equestrian activity while asking to reduce Olivia’s support.','initial_knowers':['christian','helena'],'discovery':{'evelyn':2},'payoff_chapters':[29,46,49],'limit':'Financial double standard is established; illegality, equity and sexual relationship are not inferred.'},
 {'id':'secret_03','truth':'He pried Olivia’s fingers off his coat, scratching her hand; later left in the ambulance without arranging a search handoff.','initial_knowers':['christian'],'child_knowledge':'Olivia knows the physical separation and closing door, not the entire ambulance or hospital sequence.','discovery':{'evelyn_child_report':3,'evelyn_father_account':8},'payoff_chapters':[8,38,47,58],'limit':'Chapter38 is accountability, not a late withholding of facts already learned in chapter8.'},
 {'id':'secret_04','truth':'The bid names Evelyn as a future adviser without her consent after she refuses a new request.','initial_knowers':['christian'],'discovery':{'evelyn':21},'payoff_chapters':[23,35],'limit':'No invented two-year history of unpaid technical ghostwork.'},
 {'id':'secret_05','truth':'Evelyn approved staging before the full maintenance-turning test.','initial_knowers':['evelyn','project_team'],'clue_chapters':[27,28,31],'payoff_chapters':[32,34,39],'limit':'Resident route trials did not test maintenance access; no injury is invented to make the mistake consequential.'},
 {'id':'secret_06','truth':'Publicity staff know only the approved career-break profile.','initial_knowers':['evelyn','ruth'],'clue_chapters':[6,39],'payoff_chapters':[51],'limit':'They do not magically know the mall trauma or use a child’s story already published elsewhere.'},
]
payoffs=copy.deepcopy(d['payoff_ledger'])
for p in payoffs:
    p['setup_ids']=[f'ch{n}:setup:{p["id"]}' for n in p['setup']]
ending=copy.deepcopy(d['ending_contract'])
ending.update({'schema_version':1,'enforce':True,'status':'author_approved',
    'plot_payoffs':[{'id':p['id'],'setup_ids':p['setup_ids'],'required_payoff':p['promise'],'deadline':p['deadline'],'allow_intentional_open':False} for p in payoffs],
    'antagonist_outcome':{'required':True,'character_id':'christian','required_outcome':'accountable_father_outside_the_marriage','consequence':'Corrected bid faces independent costs and loses; expensive outside support ends; marriage ends; financial and parental responsibilities remain without promised forgiveness.'},
    'emotional_contract':{'required':True,'target':'Earned hope with grief, limits, ordinary humor and an adult life beyond betrayal.','final_image':'Evelyn completes her own arrangement and meets Olivia as promised; their day no longer depends on his reliability.','forbidden_substitutes':['Forced reunion','Replacement-lover rescue','Instant cure or forced child forgiveness','New central mystery at the end']},
})
for arc in ending['character_arcs']:
    arc.update({'outcome_match_mode':'exact','required_outcome_aliases':[], 'required_choice':personality[arc['character_id']]['boundary']+' Verify the concrete actions in the listed evidence chapters.'})
ending['main_conflict'].update({'protagonist_choice':'Keep the separation, build sustainable work and accept the daughter’s separate preferences.','consequence':d['section_a']['ending_direction']})
ending['antagonist_outcome'].update({'thread_id':'plot_003','required_status':'resolved'})
ending['emotional_contract'].update({'reader_emotion':'Recognition, grief, relief, earned hope.','afterglow_state':'Mother and daughter have a life of their own; accountability continues without a marriage reunion.'})
volume_contracts=[]
for span,v,climax in zip(narrative['volumes'], d['section_d']['volumes'], [19,39,58]):
    volume_contracts.append(dict(span, title=v['title'],central_conflict=v['central_conflict'],volume_promise=v['reader_promise'],protagonist_shift=v['protagonist_shift'],climax_chapter=climax,payoff=v['payoff'],carryover_hook=v['carryover'] or ''))
volume_contracts=validate_volume_contracts(NarrativeFormat.from_dict(narrative),volume_contracts)
chapters=copy.deepcopy(d['section_d']['chapter_map'])
for c in chapters:
    c['entry_state']=c['character_arc_advancement']['from']
    c['choice_and_cost']=c['scenes'][-1]['planned_action']
    c['changed_exit_state']=c['primary_plot_advancement']
    c['reader_value']=c['character_arc_advancement']['toward']
    c['next_consequence']=chapters[c['chapter']]['scenes'][0]['planned_action'] if c['chapter']<60 else 'Complete emotional closure; no new central puzzle.'
    c['setup_ids']=[sid for p in payoffs for sid in p['setup_ids'] if sid.startswith(f'ch{c["chapter"]}:')]
    c['due_payoff_ids']=[p['id'] for p in payoffs if c['chapter'] in p['payoff']]
    c['world_event_ids']=[]
for v in d['section_d']['volumes']:
    prefix=v['volume_id']
    for n,suffix in [(v['chapter_start'],'promise'),(v['midpoint_chapter'],'midpoint'),([19,39,58][v['volume_number']-1],'climax'),(v['payoff_chapter'],'payoff')]:
        chapters[n-1]['world_event_ids'].append(f'{prefix}_{suffix}')
    if v['carryover']:
        chapters[v['chapter_end']-1]['world_event_ids'].append(f'{prefix}_carryover')
# Keep each scene once; the causal contract refers to its actual action/cost.
compact_chapters=[]
for c in chapters:
    compact_chapters.append({k:v for k,v in c.items() if k not in ['primary_plot_advancement','character_arc_advancement','choice_and_cost','next_consequence']} | {
        'choice_and_cost_scene_refs':[f'ch{c["chapter"]}:scene2'],
        'next_consequence_chapter':c['chapter']+1 if c['chapter']<60 else None,
    })
chapters=compact_chapters
setting=copy.deepcopy(d['section_c']['setting_policy'])
setting.update({'mode':'fictionalized','story_place_names':'invented_or_abstract','real_place_names_in_story':False,'market_metadata_may_name_real_places':True})
opening={
 'opening_event':'A garden display is about to be assembled while Christian leaves in rain to help Helena with her reported custody crisis.',
 'opening_stakes':'Olivia’s months of effort and expectation are displaced; Evelyn has the component but cannot make its fastening work.',
 'opening_question':'Will Evelyn ask Olivia to keep presenting a family partnership after her father has left?',
 'protagonist_immediate_choice':'Help with the display, then accept the ninth spire and let the child stop the model.',
 'first_screen_signals':['event','loss','contradiction'], 'first_screen_window':{'unit':'words','target_range':[100,180]},
 'chapter_1_value':d['section_d']['opening_micro_arc']['reader_value'][0],
 'chapter_2_reversal_or_resource':d['section_d']['opening_micro_arc']['reader_value'][1],
 'chapter_3_irreversible_step':d['section_d']['opening_micro_arc']['irreversible_step'],
 'conflict_braid':['external_resources_and_safety','marital_and_parental_relationship','habit_of_keeping_the_family_appearance'],
 'satisfaction_loop':'Recognize a specific cost -> use a seeded resource -> make a bounded choice -> face changed costs or resistance -> deliver local value -> pursue a larger sustainable life.',
 'atmosphere_pressure':'Rain delays assembly and exposes the missing partner; the small rental makes tradeoffs felt; inhabited routes reveal whose needs a design overlooked.',
 'identification_anchor':'A mother’s habit of explaining an absence before asking what it costs her child.',
 'paid_bridge':'If the platform uses the proposed three-chapter trial, chapter4 directly enacts packing and moving into the paid room; safety is already resolved. No platform or payment boundary is imposed.',
}
contract={
 'schema_version':1,'status':'author_approved','authority':'Author-approved current-project continuation and explicit logic repairs; full 60-chapter design.',
 'material_use':{'classification':'project_canon','verified_endpoint':'Job offer received; mother found child, packed, and saw model destroyed; original supplied endpoint did not yet show a move, divorce or first paycheck.','chapter_scope':'Reconstruct approved opening events in chapters1–4; continue through60. No promoted chapter receipts exist for these supplied fragments.','unrelated_references':'None supplied to runtime; use no corpus, other book or private workshop record.'},
 'audience_profile':d['audience_profile'],'setting_policy':setting,
 'workshop_trace':{'intake':'Develop one continuous English novel of60 chapters in3 volumes of20, with a full ending.','audience_decision':'US women50–65; marital betrayal, dignity and rebuilding.','market_decision':'United States, natural American English, single market; confirmed2026-10-04.','approach_options':[{'id':'approach_a','summary':'Leave, reclaim professional identity, then sustain an independent family life.','tradeoffs':'Real costs and recovery support length; no repeated disasters or added paternity mystery.','selected':True}], 'section_decisions':{k:'Author confirmed the presented full section on2026-10-04.' for k in ['section_a','section_b','section_c','section_d','section_e']},'open_assumptions':['Exact calendar year, jurisdiction, physical appearance and byline remain unspecified; see Assumptions.']},
 'story_contract':d['section_a'],'characters':characters,'supporting_characters':d['section_b']['supporting_characters'],'relationships':relationships,
 'world':{'rules':d['section_c']['world_rules'],'timeline':d['section_c']['timeline'],'resources':d['section_c']['proposed_budget'],'project':d['section_c']['project_spine'],'child_project':d['section_c']['child_story_spine'],'secrets':secrets},
 'authorized_opening_repairs':[{'id':r['id'],'retained':r['retained'],'repair':r['proposed'].replace('section_c.timeline.mall_day','world.timeline.mall_day')} for r in d['source_repairs']],
 'story_lead_contract':dict(d['section_d']['story_lead_contract'], emotional_target='mixed'),
 'retention_profile':{'mode':'retention_first','language':'English','market_scope':'United States, single market','target_audience':'Women aged50–65','setting_mode':'fictionalized','reader_promise':d['section_a']['emotional_promise'],'primary_satisfaction':'boundary and competence','opening_window':{'unit':'words','target_range':[100,180]}},
 'opening_contract':opening,'first_three_chapter_value_map':d['section_d']['opening_micro_arc'],'volumes':d['section_d']['volumes'],
 'chapter_map':chapters,'payoff_ledger':payoffs,'ending_contract':ending,
 'story_outline':d['story_outline'],'volume_contracts':volume_contracts,
 'plot_threads':[
    {'id':'plot_001','name':'Sustainable independent mother–daughter life','required_status':'resolved','deadline':60},
    {'id':'plot_002','name':'End the marriage and execute financial arrangements','required_status':'resolved','deadline':49},
    {'id':'plot_003','name':'Father accepts continuing responsibility without guaranteed forgiveness','required_status':'resolved','deadline':60},
    {'id':'plot_004','name':'Return to work, deliver the courtyard and choose a sustainable role','required_status':'resolved','deadline':57},
    {'id':'plot_005','name':'Olivia owns her voice, peer creativity and memories','required_status':'resolved','deadline':60},
    {'id':'plot_006','name':'Reciprocal adult support and a personal life','required_status':'resolved','deadline':60},
    {'id':'plot_007','name':'Correct the bid representation and let it reach an independent result','required_status':'resolved','deadline':35},
 ],
 'quality_status':{'design_review':'author_approved_after_repair','material_boundary':'authorized_current_project_canon','draft_review':'not_run','parser_validation':'reported_separately_after_native_intake'},
}

# Cover handoff is metadata, not a cover-generation request.
cover_cast=[]
cover_specs=[
 ('evelyn','Evelyn','protagonist',True,41,'female','Landscape professional returning after two years away; mother ofOlivia.','Small furnished rental, work plans, crowded table and ordinary art supplies.','Protective resolve with grief.','Packs and leaves with her daughter, then carries her own work.',['olivia','christian']),
 ('olivia','Olivia','co-protagonist',True,9,'female','Nine-year-old schoolchild at the opening; ten by the final volume.','Model fragments, paper puppets, school support and a small new home.','Hurt and tentative curiosity.','Stops the shared model and later chooses her own collaborative work.',['evelyn','christian']),
 ('christian','Christian Davis','opposing husband and father',False,43,'male','Small landscape-firm owner, husband and father.','Family doorway and professional bid materials.','Urgency directed away from his own daughter.','Leaves his daughter while assisting Helena and Carter; later must carry consequences.',['evelyn','olivia','helena','carter']),
 ('helena','Helena','outside emotional and material beneficiary',False,42,'female','Christian’s former girlfriend and Carter’s mother.','Her own household and a costly equestrian bill.','Need and expectation of help.','Seeks continued support and later acknowledges its end.',['christian','carter']),
 ('carter','Carter Hayes','child affected by adult favoritism',False,6,'male','Helena’s six-year-old son; not responsible for adult conduct.','With his mother, no independent financial or romantic role.','Child-level dependence.','Accompanies the adult who receives help; no scheming or paternity revelation.',['helena','christian']),
]
for ident,name,role,required,age,gender,occupation,environment,emotion,agency,rels in cover_specs:
    cover_cast.append({'character_id':ident,'name':name,'narrative_role':role,'must_appear':required,'age':age,'age_band':'','gender_presentation':gender,'physical_identity':'','occupation_and_status':occupation,'daily_wardrobe':'','lived_environment':environment,'current_emotional_state':emotion,'agency_signal':agency,'relationships':rels,'source_refs':[f'approved_story_contract.characters.{ident}' if ident!='carter' else 'approved_story_contract.supporting_characters.carter','approved_story_contract.chapter_map.4']})
cover={
 'schema_version':2,'title':title,'author':'','language':'English','genre':"Women's Fiction / Family Drama",'target_audience':'US women aged50–65','market_scope':'United States, English, single market',
 'core_task':premise,'core_conflict':'Christian diverts money and attention to his former girlfriend and her child, then physically separates his own daughter from his coat and leaves with them during a mall emergency. Evelyn must spend finite resources to leave, correct a false professional-service promise and build a home without requiring her child to erase every good memory. A current sexual affair and biological paternity are unestablished.',
 'emotional_promise':'Recognition of accumulated betrayal, maternal agency, grief and an earned independent life.','principal_characters':cover_cast,
 'relationship_map':[{'from_character_id':'evelyn','to_character_id':'olivia','relationship':'mother and daughter','power_balance':'Mother carries adult decisions; daughter has her own feelings.','visible_tension':'Mother packs while child releases the last model spire.','shared_risk':'Leaving familiar housing for a finite paid retreat.'},{'from_character_id':'christian','to_character_id':'olivia','relationship':'father and daughter','power_balance':'His promised protection has failed.','visible_tension':'A physical and emotional gap after a broken promise.','shared_risk':'Trust cannot be restored by a pose or a payment.'},{'from_character_id':'christian','to_character_id':'helena','relationship':'former partners with continuing emotional and material expectations','power_balance':'His selective help places costs on his own household.','visible_tension':'His attention is directed toward her household while his daughter is excluded.','shared_risk':'Do not assert a sexual affair or secret paternity.'}],
 'lived_environment':{'era':'contemporary','fictional_place':'Alderwick','primary_spaces':['family home doorway','small furnished rental','working shared courtyard'],'economic_signals':['packed ordinary belongings','small rental table','work plans'],'cultural_signals':['school support','paper-puppet craft','adult sketch class'],'weather_and_season':'','environment_truths':['Model is a relationship object, not magic.','Professional recovery requires ordinary work and paid care.']},
 'decisive_story_nodes':[{'node_id':'node_departure','description':'At the packed family doorway, Evelyn carries the adult decision to leave while Olivia lets go of the last gold spire.','evidence_refs':['character:evelyn','character:olivia','approved_story_contract.chapter_map.4']},{'node_id':'node_own_work','description':'Mother and daughter share a small home but pursue different creations: adult work plans and the child’s paper puppets.','evidence_refs':['character:evelyn','character:olivia','approved_story_contract.chapter_map.24']}],
 'secondary_signals':[{'signal_id':'golden_spire','description':'A small physical fragment from the ten-spire model.','story_function':'Accrued disappointment and the possibility of releasing an old expectation.'},{'signal_id':'work_plan','description':'A real landscape plan on an ordinary table.','story_function':'Seeded professional agency without glamorous rescue.'}],
 'genre_emotion_profile':{'primary_genre':"Women's Fiction",'submode':'family_ethics','emotional_temperature':'restrained anguish turning toward deliberate action','desired_viewer_feeling':'Recognition and concern, followed by belief in the mother’s agency.','relationship_motion':'Mother and daughter leave a broken promise while retaining separate interior lives.','prohibited_shortcuts':['Glamorous replacement-lover rescue','Fantasy towers or erasure magic','Graphic child injury','Secret paternity or confirmed-affair imagery']},
 'commercial_visual_goal':{'market':'US English','audience_segment':'women50–65','display_context':'mobile_thumbnail','thumbnail_reference_width':120,'thumbnail_reference_height':180,'first_glance_priority':'Mother and daughter making a concrete departure','reader_identification':'The adult who keeps covering another person’s absence finally changes what she will carry.','truthful_story_promise':'A domestic betrayal novel with costly agency and lasting rebuilding.'},
 'title_direction':{'hierarchy':'The Last Golden Spire is the dominant text; no unsupported subtitle or author.','preferred_zone':'top','readability':'mobile_thumbnail'},
 'forbidden_elements':['real landmarks','logos','watermarks','unsupported spoilers','invented author name'],
 'visual_assumptions':[]}
for c in contract['supporting_characters']:
    if 'age_proposal' in c:
        c['age']=c.pop('age_proposal')
    if c.get('status')=='new_proposal':
        c['status']='author_approved_continuation'
for c in cover['principal_characters']:
    c['occupation_and_status']=c['occupation_and_status'].replace('ofOlivia','of Olivia')

def block(name,obj):
    return '['+name+']\n'+json.dumps(obj,ensure_ascii=False,indent=2)+'\n[/'+name+']\n'

parts=[f'''Title: {title}
Genre: Women's Fiction
Audience: US women aged 50–65; marital betrayal, reclaimed dignity, motherhood and rebuilding
Language: English
Tone: Intimate emotional realism, restrained angst, dry humor, earned hope
POV: Evelyn — first person, past tense, strictly limited
Chapters: 60
Words: 72000

{premise}

# Production task and authority

Write one complete English novel in three internal volumes,20 chapters each. Deliver exactly60 chapters, about1,200 narrative words each (1,080–1,320), targeting72,000 chapter words. The separate120–180-word story lead, headings and working reports are outside that target. Chapters1–4 reconstruct and continue the authorized opening; do not add60 chapters after those events. Volumes are not three separately publishable books.

The author confirmed the entire A–E design on2026-10-04. The English title, US women50–65 audience, approved ages and continuation facts are fixed. Planning instructions may be Chinese; all reader prose is natural American English. This is authorized current-project continuation, not an unrelated reference-based new premise. Verified facts and explicitly authorized repairs below are authoritative; private audits, unrelated books and local source paths are not runtime inputs. There are no promoted chapter receipts to overwrite. Preserve the final marriage ending and the separate father–daughter outcome.

Plan before drafting. Native intake reads the publication and commercial blocks. The custom APPROVED_STORY_CONTRACT_JSON is an explicit Architect instruction, not a claim of a new parser feature. Preserve it in the foundation and working story bible: audience, setting, selected workshop trace, character and relationship ledgers, knowledge/timeline/resource ledgers, volume milestones, all60 chapter contracts, lead, payoff ledger and ending contract. Persist the authoritative ending contract through the existing foundation workflow, including outputs/input/ending_contract.json. Keep ids stable, and repeat the ending contract in all final-window agent contexts.

# Canonical publication metadata
''',block('NOVEL_CLASSIFICATION_JSON',classification.to_dict()),block('NARRATIVE_FORMAT_JSON',narrative),
'''# Commercial contract

The controlled label favored_child describes the allocation to Carter, not blame or scheming by a child. parent_child_displacement is the central betrayal; no catalog label creates an unproved affair. Emotional labor means covering absences and family needs, not unestablished years of technical ghostwork. The three-chapter trial is a narrative value window; no platform or paywall is chosen. This block projects the author-approved story without adding a different plot.
''',block('COMMERCIAL_STORY_JSON',commercial),'# Approved story and complete chapter contracts\n',block('APPROVED_STORY_CONTRACT_JSON',contract)]
# Remaining prose and voice samples are assembled below.
(BASE / 'production-contract.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2)+'\n')
(BASE / 'production-cover-handoff.json').write_text(json.dumps(cover,ensure_ascii=False,indent=2)+'\n')
(BASE / 'production-prompt-prefix.md').write_text('\n'.join(parts))
(BASE / 'production-publication.json').write_text(json.dumps({'classification':classification.to_dict(),'narrative_format':narrative,'commercial_story':commercial},ensure_ascii=False,indent=2)+'\n')
print('Approved design, publication contracts, chapter map and cover handoff built.')
