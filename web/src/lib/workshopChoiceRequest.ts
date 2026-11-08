import type { GuidedTurn, WorkshopSession, WorkshopTurnIntent } from '../api/workshop';

export function currentGuidance(session: WorkshopSession) {
  const round = session.creative_design?.rounds.at(-1);
  return { round, advice: round?.guidance,
    fresh: round?.guidance?.reviewed_revision === session.revision && session.status !== 'running' };
}

const pending = new Map<string, GuidedTurn>();
const keyFor = (id: string, action: 'turn' | 'save') => `novel-os.workshop.choice-request:${id}:${action}`;

export function choiceRequest(id: string, action: 'turn' | 'save', revision: number, message: string, intent: WorkshopTurnIntent, proposal_ids: string[], story_action?: GuidedTurn['story_action'], capacity_choice?: GuidedTurn['capacity_choice'], capacity_replan?: GuidedTurn['capacity_replan']): GuidedTurn {
  const key = keyFor(id, action);
  let previous = pending.get(key);
  try { previous ??= JSON.parse(sessionStorage.getItem(key) || 'null') ?? undefined; } catch { /* Memory fallback. */ }
  if (previous && previous.message === message && previous.intent === intent &&
      JSON.stringify(previous.proposal_ids) === JSON.stringify(proposal_ids) && JSON.stringify(previous.story_action) === JSON.stringify(story_action) && JSON.stringify(previous.capacity_choice) === JSON.stringify(capacity_choice) && JSON.stringify(previous.capacity_replan) === JSON.stringify(capacity_replan)) return previous;
  const body = { revision, message, intent, proposal_ids, operation_id: crypto.randomUUID(), ...(story_action ? { story_action } : {}), ...(capacity_choice ? { capacity_choice } : {}), ...(capacity_replan ? { capacity_replan } : {}) };
  pending.set(key, body);
  try { sessionStorage.setItem(key, JSON.stringify(body)); } catch { /* Keep in memory. */ }
  return body;
}

export function clearChoiceRequest(id: string, action: 'turn' | 'save') {
  const key = keyFor(id, action);
  pending.delete(key);
  try { sessionStorage.removeItem(key); } catch { /* Memory already cleared. */ }
}

export function readChoices(key: string): Record<string, string> {
  try {
    const value = JSON.parse(sessionStorage.getItem(`novel-os.workshop.choices:${key}`) || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

export function writeChoices(key: string, values: Record<string, string>) {
  try { sessionStorage.setItem(`novel-os.workshop.choices:${key}`, JSON.stringify(values)); } catch { /* Component retains it. */ }
}
