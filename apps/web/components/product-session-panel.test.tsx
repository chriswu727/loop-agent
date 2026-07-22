import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { Task } from '@repo/api-contract';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ProductSessionPanel } from './product-session-panel';

const { revisions, createRevision, push } = vi.hoisted(() => ({
  revisions: vi.fn(),
  createRevision: vi.fn(),
  push: vi.fn(),
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {},
  tasksApi: { revisions, createRevision },
}));

const task = {
  id: 'task-v1',
  status: 'completed',
  stop_reason: 'goal_achieved',
  verified_by: 'execution',
  receipt_hash: 'receipt-v1',
  change_set: { state: 'pending' },
  product_revision: {
    session_id: 'session-1',
    revision: 1,
    previous_task_id: null,
    superseded_by_task_id: null,
    feedback_kind: null,
    feedback_delta: null,
    specification: {
      schema: 'loop.product-specification/v1',
      original_goal: 'Ship the greeting',
      required_acceptance_criteria: [],
      feedback_history: [],
      previous_contract_hash: null,
      previous_receipt_hash: null,
    },
    specification_hash: 'a'.repeat(64),
    is_latest: true,
  },
} as unknown as Task;

describe('ProductSessionPanel', () => {
  beforeEach(() => {
    revisions.mockReset();
    createRevision.mockReset();
    push.mockReset();
    revisions.mockResolvedValue([task]);
  });

  it('turns feedback into the next linked product revision', async () => {
    createRevision.mockResolvedValue({ ...task, id: 'task-v2' });
    render(<ProductSessionPanel task={task} />);

    expect(await screen.findByText('v1 · Receipt')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Feedback type'), {
      target: { value: 'product_decision' },
    });
    fireEvent.change(screen.getByLabelText('Continue from this verified delivery'), {
      target: { value: 'Make the greeting configurable.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create next revision' }));

    await waitFor(() =>
      expect(createRevision).toHaveBeenCalledWith('task-v1', {
        feedback: 'Make the greeting configurable.',
        kind: 'product_decision',
      }),
    );
    expect(push).toHaveBeenCalledWith('/tasks/task-v2');
  });

  it('keeps a superseded revision read-only', async () => {
    render(
      <ProductSessionPanel
        task={{
          ...task,
          product_revision: {
            ...task.product_revision!,
            is_latest: false,
            superseded_by_task_id: 'task-v2',
          },
        }}
      />,
    );

    expect(
      await screen.findByText('This is immutable history. Continue from the latest revision.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Create next revision' })).not.toBeInTheDocument();
  });
});
