import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ArtifactSharing, {
  type ArtifactSharingProps,
  type ArtifactShareReview,
  type ArtifactShareOutcome,
} from './ArtifactSharing';

const review: ArtifactShareReview = {
  review_id: 'review-a',
  resource_id: 'design-a',
  resource_revision: 'r1',
  action: 'publish',
  channel_name: null,
  recipient: null,
  delivery: 'link',
  pages: 'all',
  page_count: 2,
  remote: false,
  requires_pairing: true,
};
const completed: ArtifactShareOutcome = {
  status: 'published',
  code: null,
  resource_id: 'design-a',
  resource_revision: 'r2',
  url: 'http://127.0.0.1:8080/published/design-a.html',
  link_kind: 'local',
  submitted_count: 0,
  total_count: 0,
};
function props(
  overrides: Partial<ArtifactSharingProps> = {},
): ArtifactSharingProps {
  return {
    resourceId: 'design-a',
    resourceRevision: 'r1',
    visible: true,
    channels: [{ name: 'fake', label: 'Synthetic channel', available: true }],
    prepare: vi.fn(async () => review),
    execute: vi.fn(async () => completed),
    ...overrides,
  };
}

it('opens passively and publishes a local link in one click', async () => {
  const current = props();
  render(<ArtifactSharing {...current} />);
  expect(current.prepare).not.toHaveBeenCalled();
  expect(current.execute).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Publish local link' })),
  );
  expect(current.execute).toHaveBeenCalledWith(
    {
      action: 'publish',
      delivery: 'link',
      pages: 'all',
      text: '',
      pptx_mode: 'screenshot',
      remote: false,
    },
    'review-a',
    'r1',
  );
  expect(screen.getByRole('link', { name: 'Open local link' })).toHaveAttribute(
    'rel',
    'noopener noreferrer',
  );
});

it('discloses tunnel and pairing requirements when link access changes', async () => {
  render(<ArtifactSharing {...props()} />);
  fireEvent.change(screen.getByLabelText('Link access'), {
    target: { value: 'remote' },
  });
  expect(
    screen.getByRole('button', { name: 'Publish remote access link' }),
  ).toBeEnabled();
  expect(
    screen.getByText(/start the configured app tunnel/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/sign-in or device pairing is still required/),
  ).toBeInTheDocument();
});

it('shows the exact reviewed recipient before any channel send', async () => {
  const current = props({
    prepare: vi.fn(async (): Promise<ArtifactShareReview> => ({
      ...review,
      action: 'channel',
      channel_name: 'fake',
      recipient: 'reviewed-recipient',
    })),
  });
  render(<ArtifactSharing {...current} />);
  fireEvent.change(screen.getByLabelText('Share action'), {
    target: { value: 'channel' },
  });
  expect(
    screen.getByRole('button', { name: 'Prepare channel send' }),
  ).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Channel'), {
    target: { value: 'fake' },
  });
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Prepare channel send' }),
    ),
  );
  expect(screen.getByText('Recipient: reviewed-recipient')).toBeInTheDocument();
  expect(current.execute).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(
      screen.getByRole('button', { name: 'Confirm send to channel' }),
    ),
  );
  expect(current.execute).toHaveBeenCalledTimes(1);
});

it('never retries uncertain delivery and requires another explicit review', async () => {
  const current = props({
    execute: vi.fn(async (): Promise<ArtifactShareOutcome> => ({
      ...completed,
      status: 'uncertain',
      submitted_count: 1,
      total_count: 2,
      url: null,
    })),
  });
  render(<ArtifactSharing {...current} />);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Publish local link' })),
  );
  expect(
    screen.getByText(/Check the destination before another attempt/),
  ).toBeInTheDocument();
  expect(current.execute).toHaveBeenCalledTimes(1);
});

it('preserves admission and ignores late result after switching resource', async () => {
  let finish!: (value: ArtifactShareOutcome) => void;
  const current = props({
    execute: vi.fn(
      () =>
        new Promise<ArtifactShareOutcome>((resolve) => {
          finish = resolve;
        }),
    ),
  });
  const view = render(<ArtifactSharing {...current} />);
  fireEvent.click(screen.getByRole('button', { name: 'Publish local link' }));
  await waitFor(() => expect(current.execute).toHaveBeenCalledOnce());
  view.rerender(<ArtifactSharing {...current} resourceId="design-b" />);
  expect(
    screen.getByRole('button', { name: 'Publish local link' }),
  ).toBeDisabled();
  await act(async () => finish(completed));
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
  expect(current.execute).toHaveBeenCalledTimes(1);
});

it('accepts own confirmed publication after its resource revision event', async () => {
  let finish!: (value: ArtifactShareOutcome) => void;
  const current = props({
    execute: vi.fn(
      () =>
        new Promise<ArtifactShareOutcome>((resolve) => {
          finish = resolve;
        }),
    ),
  });
  const view = render(<ArtifactSharing {...current} />);
  fireEvent.click(screen.getByRole('button', { name: 'Publish local link' }));
  await waitFor(() => expect(current.execute).toHaveBeenCalledOnce());
  view.rerender(<ArtifactSharing {...current} resourceRevision="r2" />);
  await act(async () => finish(completed));
  expect(screen.getByText('Local link ready.')).toBeInTheDocument();
});

it('does not expose an unsafe URL supplied by a malformed outcome', async () => {
  const current = props({
    execute: vi.fn(async () => ({ ...completed, url: 'javascript:bad()' })),
  });
  render(<ArtifactSharing {...current} />);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Publish local link' })),
  );
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
});

it('retains a confirmed result when its revision event arrives afterward and clears it on resource switch', async () => {
  const current = props();
  const view = render(<ArtifactSharing {...current} />);
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Publish local link' })),
  );
  view.rerender(<ArtifactSharing {...current} resourceRevision="r2" />);
  expect(
    screen.getByRole('link', { name: 'Open local link' }),
  ).toBeInTheDocument();
  view.rerender(<ArtifactSharing {...current} resourceId="design-b" />);
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
});
