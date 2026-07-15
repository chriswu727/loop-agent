import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ChangeSet } from '@repo/api-contract';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChangeSetPanel } from './change-set-panel';

const { changes, applyChanges, discardChanges, undoChanges } = vi.hoisted(() => ({
  changes: vi.fn(),
  applyChanges: vi.fn(),
  discardChanges: vi.fn(),
  undoChanges: vi.fn(),
}));

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {},
  tasksApi: { changes, applyChanges, discardChanges, undoChanges },
}));

const pending: ChangeSet = {
  project_path: 'loop-agent',
  base_commit: '1234567890abcdef',
  base_branch: 'main',
  state: 'pending',
  applied_patch_sha256: null,
  patch_sha256: 'a'.repeat(64),
  files: [
    {
      path: 'app.py',
      previous_path: null,
      status: 'M',
      additions: 2,
      deletions: 1,
    },
  ],
  diff: "-print('before')\n+print('after')",
  diff_truncated: false,
  can_apply: true,
  can_discard: true,
  can_undo: false,
  blocked_reason: null,
};

describe('ChangeSetPanel', () => {
  beforeEach(() => {
    changes.mockReset();
    applyChanges.mockReset();
    discardChanges.mockReset();
    undoChanges.mockReset();
    changes.mockResolvedValue(pending);
  });

  it('reviews and applies the exact verified patch, then offers undo', async () => {
    applyChanges.mockResolvedValue({
      ...pending,
      state: 'applied',
      applied_patch_sha256: pending.patch_sha256,
      can_apply: false,
      can_discard: false,
      can_undo: true,
    });
    render(<ChangeSetPanel taskId="task-1" revision="completed:5" />);

    expect(await screen.findByText('Verified change set')).toBeInTheDocument();
    expect(screen.getByText(/app.py/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Apply verified patch' }));

    await waitFor(() => expect(applyChanges).toHaveBeenCalledWith('task-1'));
    expect(await screen.findByText('Applied to source')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Undo apply' })).toBeInTheDocument();
  });

  it('shows why an unverified change set cannot apply', async () => {
    changes.mockResolvedValue({
      ...pending,
      can_apply: false,
      blocked_reason: 'Apply requires execution-backed verification.',
    });
    render(<ChangeSetPanel taskId="task-2" revision="completed:3" />);

    expect(
      await screen.findByText('Apply requires execution-backed verification.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Apply verified patch' })).not.toBeInTheDocument();
  });
});
