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
        checks: [
          {
            check_id: 'check-001',
            criterion_ids: ['criterion-001'],
            kind: 'file_exists',
            target: 'result.txt',
            passed: true,
            evidence: 'present',
          },
        ],
      },
    });
  });

  it('shows authenticity, evidence, and criterion mappings', async () => {
    render(<ReceiptPanel taskId="task-1" />);

    fireEvent.click(await screen.findByRole('button', { name: /Receipt/ }));

    expect(screen.getByText('authentic')).toBeInTheDocument();
    expect(screen.getByText(/criterion-001/)).toBeInTheDocument();
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
