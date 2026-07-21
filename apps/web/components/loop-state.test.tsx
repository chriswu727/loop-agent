import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { LoopState } from './loop-state';

describe('LoopState', () => {
  it('shows the persisted phase and transition reason', () => {
    render(
      <LoopState
        loop={{ state: 'verifying', transition_reason: 'finish_requested', sequence: 7 }}
      />,
    );

    expect(screen.getByTestId('loop-state')).toHaveTextContent(
      'Loop phase: Verifying evidence · transition #7 · Finish requested',
    );
    expect(screen.getByTitle('Transition reason: finish_requested')).toBeInTheDocument();
  });
});
