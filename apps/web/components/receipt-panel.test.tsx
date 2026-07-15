import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ReceiptPanel } from './receipt-panel';

const { receipt, replayReceipt } = vi.hoisted(() => ({
  receipt: vi.fn(),
  replayReceipt: vi.fn(),
}));

vi.mock('@/lib/api-client', () => ({
  tasksApi: { receipt, replayReceipt },
}));

describe('ReceiptPanel', () => {
  beforeEach(() => {
    receipt.mockReset();
    replayReceipt.mockReset();
    receipt.mockResolvedValue({
      valid: true,
      authentic: true,
      assurance: 'authentic',
      signature: 'valid',
      anchor_ok: true,
      files_ok: true,
      receipt: {
        schema: 'loop.receipt/v1',
        receipt_hash: 'receipt-hash',
        goal: 'Ship verified work',
        verified_by: 'execution',
        isolation: 'kubernetes',
        score: 100,
        criteria: [{ id: 'criterion-001', text: 'The verified output exists' }],
        contract: { criteria_source: 'user', verification_mode: 'strict' },
        provenance: {
          executor_models: [{ provider: 'anthropic', model: 'claude-sonnet-4-6' }],
          verifier: { provider: 'gemini', model: 'gemini-2.5-flash' },
        },
        checks: [
          {
            check_id: 'check-001',
            criterion_ids: ['criterion-001'],
            kind: 'file_exists',
            target: 'result.txt',
            passed: true,
            evidence: 'present',
            source: 'contract',
            baseline_passed: false,
          },
        ],
        baseline_checks: [
          {
            check_id: 'system-js-test',
            kind: 'command',
            target: 'pnpm test',
            passed: false,
            evidence: 'missing dependency',
            source: 'system',
          },
        ],
      },
    });
  });

  it('shows authenticity, evidence, and criterion mappings', async () => {
    render(<ReceiptPanel taskId="task-1" />);

    fireEvent.click(await screen.findByRole('button', { name: /Receipt/ }));

    expect(screen.getByText('authentic')).toBeInTheDocument();
    expect(screen.getAllByText(/criterion-001/)).toHaveLength(2);
    expect(screen.getByText(/The verified output exists/)).toBeInTheDocument();
    expect(screen.getAllByText(/check-001 \[contract\]/)).toHaveLength(2);
    expect(screen.getByText(/executor: anthropic:claude-sonnet-4-6/)).toBeInTheDocument();
    expect(screen.getByText('Pre-change baseline')).toBeInTheDocument();
    expect(screen.getByText(/system-js-test \[system\] pnpm test/)).toBeInTheDocument();
    expect(screen.getByText('matches independent DB anchor')).toBeInTheDocument();
  });

  it('replays recorded checks and reports the result', async () => {
    replayReceipt.mockResolvedValue({ passed: true });
    render(<ReceiptPanel taskId="task-1" />);

    fireEvent.click(await screen.findByRole('button', { name: /Receipt/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Replay checks' }));

    await waitFor(() => expect(replayReceipt).toHaveBeenCalledWith('task-1'));
    expect(await screen.findByText('Replay passed')).toBeInTheDocument();
  });
});
