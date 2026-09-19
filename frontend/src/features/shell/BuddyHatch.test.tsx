import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import BuddyHatch, {
  type BuddyHatchProps,
  type HatchResult,
  type HatchReview,
  type HatchRemoval,
} from './BuddyHatch';

const result: HatchResult = {
  schema_version: 1,
  command_id: 'command-one',
  job_id: 'job-one',
  status: 'running',
  stage: 'motion_provider_started',
  pack_id: null,
  selected: false,
  completed_clips: 2,
  total_clips: 6,
  code: '',
  retained_copy: true,
  has_still: true,
};
function props(): BuddyHatchProps {
  return {
    scopeKey: 'owner-one',
    configRevision: 'revision-one',
    personality: 'warm_mystical',
    styleNotes: 'Warm and luminous',
    result: null,
    selectedPack: {
      id: 'hatch-one',
      name: 'Synthetic',
      revision: 'pack-one',
      runtime: 'generated_motion_pack',
      generated: true,
      available: true,
      assets: [{ id: 'preview', content_type: 'image/png' }],
      animation_map: {},
    },
    review: vi.fn(async (request) => ({
      review_id: 'review-one',
      action: request.action,
      config_revision: request.config_revision,
      image_model: request.action === 'full' ? 'openai/image' : null,
      video_model: ['full', 'motion'].includes(request.action)
        ? 'xai/video'
        : null,
      provider_calls:
        request.action === 'full' ? 7 : request.action === 'motion' ? 6 : 0,
    })),
    confirm: vi.fn(async () => result),
    refresh: vi.fn(async () => result),
    cancel: vi.fn(async () => {}),
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
async function reviewNew() {
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Describe your Buddy' }),
    { target: { value: 'Synthetic Buddy' } },
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Review Generate full Buddy' }),
  );
  await screen.findByRole('region', { name: 'Review Hatch action' });
}

describe('Buddy Hatch explicit controls', () => {
  it('seeds the local draft from the saved owner prompt without reviewing or generating', () => {
    const input = props();
    render(<BuddyHatch {...input} initialPrompt="Saved Buddy concept" />);

    expect(screen.getByLabelText('Describe your Buddy')).toHaveValue(
      'Saved Buddy concept',
    );
    expect(input.review).not.toHaveBeenCalled();
    expect(input.confirm).not.toHaveBeenCalled();
  });

  it('presents the owner generation hierarchy without bypassing review', () => {
    const input = props();
    render(<BuddyHatch {...input} />);

    expect(
      screen.getByRole('heading', { name: 'Generate Look' }),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: 'Review Generate full Buddy' }),
    ).toBeVisible();
    expect(screen.getByRole('button', { name: 'Review motion' })).toBeVisible();
    expect(input.review).not.toHaveBeenCalled();
    expect(input.confirm).not.toHaveBeenCalled();
  });

  it('never generates on mount and reviews exact models before one explicit confirmation', async () => {
    const input = props();
    render(<BuddyHatch {...input} />);
    expect(input.review).not.toHaveBeenCalled();
    expect(input.confirm).not.toHaveBeenCalled();
    await reviewNew();
    expect(input.review).toHaveBeenCalledWith(
      expect.objectContaining({
        prompt: expect.stringContaining('User style notes: Warm and luminous'),
      }),
    );
    expect(screen.getByText('Image model: openai/image')).toBeInTheDocument();
    expect(screen.getByText('Provider calls: 7')).toBeInTheDocument();
    expect(input.confirm).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Confirm Hatch action' }),
    );
    await screen.findByText('2 of 6 motion clips retained.');
    expect(input.confirm).toHaveBeenCalledExactlyOnceWith('review-one');
  });
  it('drops a stale review when configuration changes', async () => {
    const input = props();
    const view = render(<BuddyHatch {...input} />);
    await reviewNew();
    view.rerender(<BuddyHatch {...input} configRevision="revision-two" />);
    expect(
      screen.queryByRole('button', { name: 'Confirm Hatch action' }),
    ).not.toBeInTheDocument();
    expect(input.confirm).not.toHaveBeenCalled();
  });
  it('does not release an active submission when authority or selection changes', async () => {
    const input = props();
    const pending = deferred<HatchResult>();
    input.confirm = vi.fn(() => pending.promise);
    const view = render(<BuddyHatch {...input} />);
    await reviewNew();
    fireEvent.click(
      screen.getByRole('button', { name: 'Confirm Hatch action' }),
    );
    view.rerender(
      <BuddyHatch
        {...input}
        scopeKey="owner-two"
        configRevision="revision-two"
      />,
    );
    expect(
      screen.getByRole('button', { name: 'Review motion' }),
    ).toBeDisabled();
    await act(async () => pending.resolve(result));
    expect(
      screen.queryByText('2 of 6 motion clips retained.'),
    ).not.toBeInTheDocument();
    expect(input.confirm).toHaveBeenCalledTimes(1);
  });
  it('ignores delayed review after revocation', async () => {
    const input = props();
    const pending = deferred<HatchReview>();
    input.review = vi.fn(() => pending.promise);
    const view = render(<BuddyHatch {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Review motion' }));
    view.rerender(<BuddyHatch {...input} configRevision={null} />);
    await act(async () =>
      pending.resolve({
        review_id: 'late',
        action: 'motion',
        config_revision: 'revision-one',
        provider_calls: 6,
        image_model: null,
        video_model: 'xai/video',
      }),
    );
    expect(
      screen.queryByRole('region', { name: 'Hatch a Buddy' }),
    ).not.toBeInTheDocument();
  });
  it('refreshes uncertain outcomes without repeating generation and recovers still explicitly', async () => {
    const input = props();
    input.result = { ...result, status: 'uncertain' };
    input.refresh = vi.fn(async () => input.result!);
    render(<BuddyHatch {...input} />);
    fireEvent.click(
      screen.getByRole('button', { name: 'Refresh Hatch status' }),
    );
    await act(async () => {});
    expect(input.refresh).toHaveBeenCalledExactlyOnceWith('command-one');
    expect(input.confirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Review still only' }));
    await screen.findByText('Provider calls: 0');
    expect(input.review).toHaveBeenCalledWith(
      expect.objectContaining({
        action: 'still',
        source_command_id: 'command-one',
      }),
    );
  });
  it('cancels exact active job and retains status after request', async () => {
    const input = props();
    input.result = result;
    render(<BuddyHatch {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Stop Hatch' }));
    await screen.findByText(/Stop requested/);
    expect(input.cancel).toHaveBeenCalledExactlyOnceWith('job-one');
    expect(
      screen.getByText('2 of 6 motion clips retained.'),
    ).toBeInTheDocument();
  });
  it('consumes failed confirmation and requires fresh explicit review rather than retry', async () => {
    const input = props();
    input.confirm = vi.fn(async () => {
      throw new Error('private provider body');
    });
    render(<BuddyHatch {...input} />);
    await reviewNew();
    fireEvent.click(
      screen.getByRole('button', { name: 'Confirm Hatch action' }),
    );
    await screen.findByText('Hatch needs attention');
    expect(
      screen.queryByRole('button', { name: 'Confirm Hatch action' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('private provider body')).not.toBeInTheDocument();
  });
  it('reviews removal with exact pack revision and reports retained files', async () => {
    const input = props();
    input.confirm = vi.fn(async (): Promise<HatchRemoval> => ({
      status: 'removed',
      pack_id: 'hatch-one',
      retained_copy: true,
      config_changed: true,
    }));
    render(<BuddyHatch {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Review removal' }));
    await screen.findByRole('button', { name: 'Confirm removal' });
    expect(input.review).toHaveBeenCalledWith(
      expect.objectContaining({
        action: 'remove',
        source_pack_id: 'hatch-one',
        source_pack_revision: 'pack-one',
      }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Confirm removal' }));
    await screen.findByText(
      'Generated look removed. Its files are retained for recovery.',
    );
  });
});
