import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, test, expect } from 'vitest';
import MethodReviews from '../components/MethodReviews';
import { api } from '../api/client';

const policy = { sha256: '', data: { policy: { mode: 'advisory' as const } } };

test('review is opt-in to open and never starts a model call by viewing reports', async () => {
  const load = vi.spyOn(api, 'methodReviews').mockResolvedValue({ reports: [], runs: [], revisions: [], decisions: [] });
  vi.spyOn(api, 'methodPolicy').mockResolvedValue(policy);
  const start = vi.spyOn(api, 'startMethodReview');
  render(<MethodReviews projectId="p" chapter={1} />);
  expect(load).not.toHaveBeenCalled();
  await userEvent.click(screen.getByText('写作评审 · 只读'));
  expect(await screen.findByText('暂无评审报告')).toBeInTheDocument();
  expect(start).not.toHaveBeenCalled();
  expect(screen.queryByText('自动修稿')).not.toBeInTheDocument();
});

test('unavailable review is shown as unreviewed, never as a passing chapter', async () => {
  vi.spyOn(api, 'methodPolicy').mockResolvedValue(policy);
  vi.spyOn(api, 'methodReviews').mockResolvedValue({
    reports: [{ report_id: 'r', revision_id: 'revision', chapter: 1, run_id: 'run', status: 'unavailable',
      candidate_sha256: 'a'.repeat(64), error_code: 'reviewer_request_failed', summary: '', findings: [],
      blocking: false, created_at: '2026-09-12', usage: { model_calls: 1, tokens: null, transport_attempts: null, elapsed_seconds: 2 },
      coverage: { complete_input: false, candidate_length: 200, submitted_intervals: [] } }],
    runs: [], revisions: [], decisions: [],
  });
  render(<MethodReviews projectId="p" chapter={1} />);
  await userEvent.click(screen.getByText('写作评审 · 只读'));
  expect(await screen.findByText('未完成评审')).toBeInTheDocument();
  expect(screen.getByText(/Token：未知/)).toBeInTheDocument();
  expect(screen.queryByText('检查通过')).not.toBeInTheDocument();
});

import { evidenceSegments } from '../lib/methodEvidence';

test('historical evidence highlights Unicode code points, not UTF-16 or normalized characters', () => {
  const text = '🌿 She said e\u0301.';
  const parts = evidenceSegments(text, [{ start: 2, end: 5, quote: 'She' }, { start: 11, end: 13, quote: 'e\u0301' }]);
  expect(parts.filter(p => p.highlighted).map(p => p.text)).toEqual(['She', 'e\u0301']);
  expect(parts.map(p => p.text).join('')).toBe(text);
  expect(evidenceSegments(text, [{ start: 3, end: 6, quote: 'She' }]).some(p => p.highlighted)).toBe(false);
});
