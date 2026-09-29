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

/** Publish asks once: the review, then Confirm publish (U37). */
async function publishLocal() {
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Publish local link' })),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Confirm publish' })),
  );
}

it('opens passively and asks once before publishing a local link', async () => {
  const current = props();
  render(<ArtifactSharing {...current} />);
  expect(current.prepare).not.toHaveBeenCalled();
  expect(current.execute).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Publish local link' })),
  );
  expect(current.execute).not.toHaveBeenCalled();
  expect(
    screen.getByRole('group', { name: 'Confirm sharing destination' }),
  ).toHaveTextContent(
    'Publish these 2 pages at a local link? It opens on this computer only.',
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Confirm publish' })),
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
  await publishLocal();
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
  await publishLocal();
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
  await publishLocal();
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
  await publishLocal();
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
});

it('retains a confirmed result when its revision event arrives afterward and clears it on resource switch', async () => {
  const current = props();
  const view = render(<ArtifactSharing {...current} />);
  await publishLocal();
  view.rerender(<ArtifactSharing {...current} resourceRevision="r2" />);
  expect(
    screen.getByRole('link', { name: 'Open local link' }),
  ).toBeInTheDocument();
  view.rerender(<ArtifactSharing {...current} resourceId="design-b" />);
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
});

const publication = {
  resource_id: 'design-a',
  resource_revision: 'r1',
  published: true,
  url: 'https://synthetic.invalid/published/design-a.html',
  link_kind: 'remote_access' as const,
  published_at: '2026-09-29T08:00:00+00:00',
};

it('shows the saved link with Copy, QR and Unpublish when the panel opens', async () => {
  const writeClipboard = vi.fn(async () => ({ status: 'ok' as const }));
  const current = props({
    loadPublication: vi
      .fn<NonNullable<ArtifactSharingProps['loadPublication']>>()
      .mockResolvedValueOnce(publication)
      .mockResolvedValue({
        ...publication,
        published: false,
        url: null,
        link_kind: null,
      }),
    writeClipboard,
    execute: vi.fn(async () => ({
      ...completed,
      status: 'unpublished' as const,
      url: null,
      link_kind: null,
    })),
    prepare: vi.fn(async () => ({ ...review, action: 'unpublish' as const })),
  });
  render(<ArtifactSharing {...current} />);
  const link = await screen.findByRole('region', { name: 'Published link' });
  expect(link).toHaveTextContent(publication.url);
  expect(link).toHaveTextContent(
    'opens with Row-Bot sign-in or a paired device',
  );
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Copy link' })),
  );
  expect(writeClipboard).toHaveBeenCalledWith(publication.url);
  expect(screen.getByText('Link copied.')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'QR code' }));
  expect(
    screen.getByRole('img', { name: 'QR code for the published link' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Update the published copy' }),
  ).toBeInTheDocument();
  // Unpublish takes the link back at once: no second question.
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Unpublish' })),
  );
  expect(current.execute).toHaveBeenCalledWith(
    expect.objectContaining({ action: 'unpublish' }),
    'review-a',
    'r1',
  );
  expect(
    screen.getByText('Unpublished. The link no longer opens.'),
  ).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Published link' })).toBeNull();
});

it('offers a QR code only for links another device can open', async () => {
  render(
    <ArtifactSharing
      {...props({
        loadPublication: vi.fn(async () => ({
          ...publication,
          url: 'http://127.0.0.1:8080/published/design-a.html',
          link_kind: 'local' as const,
        })),
      })}
    />,
  );
  const link = await screen.findByRole('region', { name: 'Published link' });
  expect(link).toHaveTextContent('opens on this computer');
  expect(screen.queryByRole('button', { name: 'QR code' })).toBeNull();
  expect(
    screen.getByRole('link', { name: 'Open local link' }),
  ).toBeInTheDocument();
});

it('shows the new link after publishing', async () => {
  const loadPublication = vi
    .fn<NonNullable<ArtifactSharingProps['loadPublication']>>()
    .mockResolvedValueOnce({
      ...publication,
      published: false,
      url: null,
      link_kind: null,
    })
    .mockResolvedValue({
      ...publication,
      url: completed.url,
      link_kind: 'local',
    });
  const current = props({ loadPublication });
  render(<ArtifactSharing {...current} />);
  await waitFor(() => expect(loadPublication).toHaveBeenCalledOnce());
  await publishLocal();
  expect(
    await screen.findByRole('region', { name: 'Published link' }),
  ).toHaveTextContent(completed.url!);
});
