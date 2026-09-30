import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import type { ReactElement } from 'react';
import { expect, it, vi } from 'vitest';
import { OverlayProvider } from '../../ui/overlays';
import ConversationActions, {
  createConversationActionsSession,
  type ConversationActionReview,
  type ConversationActionSnapshot,
} from './ConversationActions';

const snapshot: ConversationActionSnapshot = {
  schema_version: 1,
  conversation_id: 'conversation-1',
  revision: '4',
  checkpoint_revision: 'checkpoint-7',
  title: 'Saved conversation',
  pinned: false,
  capabilities: {
    rename: { available: true, code: null },
    pin: { available: true, code: null },
    archive: { available: false, code: 'conversation_archive_unavailable' },
    export: { available: true, code: null },
  },
};

// Saves confirm through the floating notices.
const renderActions = (ui: ReactElement) =>
  render(ui, { wrapper: OverlayProvider });

function options() {
  const session = createConversationActionsSession('conversation-1');
  const load = vi.fn().mockResolvedValue(snapshot);
  const review = vi
    .fn()
    .mockImplementation(
      async (
        conversationId: string,
        action: ConversationActionReview['action'],
        revision: string,
        fields: Record<string, unknown>,
      ): Promise<ConversationActionReview> => ({
        schema_version: 1,
        conversation_id: conversationId,
        action,
        revision,
        checkpoint_revision: snapshot.checkpoint_revision,
        fields:
          action === 'conversation.export' ? { title: snapshot.title } : fields,
        action_digest: 'a'.repeat(64),
        summary: `Review ${action}`,
        disclosures:
          action === 'conversation.export' ? ['Local export only.'] : [],
      }),
    );
  const execute = vi.fn().mockImplementation(async (_id, command) => ({
    command_id: command.command_id,
    status: 'completed',
    action: command.type,
    conversation: {
      conversation_id: 'conversation-1',
      revision: '5',
      title: command.payload.title ?? snapshot.title,
      pinned: command.payload.pinned ?? snapshot.pinned,
    },
  }));
  return {
    conversationId: 'conversation-1',
    session,
    load,
    review,
    execute,
    save: vi.fn().mockResolvedValue({ status: 'ok', value: { kind: 'file' } }),
    onChanged: vi.fn(),
  };
}

it('loads passively and explains the reversible archive boundary', async () => {
  const props = options();
  renderActions(<ConversationActions {...props} />);
  await screen.findByDisplayValue('Saved conversation');
  expect(props.load).toHaveBeenCalledWith(
    'conversation-1',
    expect.any(AbortSignal),
  );
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
  expect(screen.getByRole('status')).toHaveTextContent(
    'Archive is unavailable',
  );
});

it('validates and applies an exact rename in one click', async () => {
  const props = options();
  renderActions(<ConversationActions {...props} />);
  const name = await screen.findByLabelText('Conversation name');
  fireEvent.change(name, { target: { value: 'Reviewed name' } });
  fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
  await screen.findByText('Conversation action completed.');
  expect(props.review).toHaveBeenCalledWith(
    'conversation-1',
    'conversation.rename',
    '4',
    { title: 'Reviewed name' },
    expect.any(AbortSignal),
  );
  expect(props.execute).toHaveBeenCalledOnce();
  expect(props.execute.mock.calls[0][2]).toEqual(
    expect.objectContaining({ action: 'conversation.rename' }),
  );
  expect(props.onChanged).toHaveBeenCalledWith(
    expect.objectContaining({ title: 'Reviewed name', revision: '5' }),
  );
  expect(name).toHaveValue('Reviewed name');
  expect(props.session.hasRetained()).toBe(false);
});

it('reviews and executes an initial pin request once, then uses the receipt', async () => {
  const props = options();
  renderActions(<ConversationActions {...props} initialPin />);
  await screen.findByText('Conversation action completed.');
  expect(props.review).toHaveBeenCalledExactlyOnceWith(
    'conversation-1',
    'conversation.pin',
    '4',
    { pinned: true },
    expect.any(AbortSignal),
  );
  expect(props.execute).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'Unpin' })).toBeEnabled();
  expect(props.session.getSnapshot().snapshot?.pinned).toBe(true);
});

it('creates and downloads the local export with one click', async () => {
  const props = options();
  props.execute.mockImplementationOnce(async (_id, command) => ({
    command_id: command.command_id,
    status: 'completed',
    action: command.type,
    export: {
      attachment_ref: 'conversation-1:export-1',
      file_name: 'conversation-export.md',
      size_bytes: 321,
      checkpoint_revision: 'checkpoint-7',
    },
  }));
  renderActions(<ConversationActions {...props} />);
  await screen.findByDisplayValue('Saved conversation');
  fireEvent.click(screen.getByRole('button', { name: 'Export as Markdown' }));
  await screen.findByText('Conversation export saved.');
  expect(props.execute.mock.calls[0][1].payload).toEqual({
    checkpoint_revision: 'checkpoint-7',
    action_digest: 'a'.repeat(64),
    export_title: 'Saved conversation',
  });
  expect(props.save).toHaveBeenCalledWith(
    'conversation-1:export-1',
    'conversation-export.md',
  );
});

it('retains an uncertain command across remount and checks the same identity', async () => {
  const props = options();
  props.execute.mockRejectedValueOnce(Error('response lost'));
  const first = renderActions(<ConversationActions {...props} />);
  await screen.findByDisplayValue('Saved conversation');
  fireEvent.click(screen.getByRole('button', { name: 'Pin' }));
  const recover = await screen.findByRole('button', {
    name: 'Check original action',
  });
  const firstCall = props.execute.mock.calls[0];
  expect(props.session.hasRetained()).toBe(true);
  first.unmount();

  renderActions(<ConversationActions {...props} />);
  await waitFor(() => expect(recover).not.toBeInTheDocument());
  const remounted = screen.getByRole('button', {
    name: 'Check original action',
  });
  await act(async () => fireEvent.click(remounted));
  await screen.findByText('Conversation action completed.');
  expect(props.execute.mock.calls[1]).toEqual(firstCall);
  expect(props.review).toHaveBeenCalledTimes(1);
  expect(props.session.hasRetained()).toBe(false);
});

it('fences a retained action from a different conversation', () => {
  const props = options();
  renderActions(
    <ConversationActions {...props} conversationId="conversation-2" />,
  );
  expect(screen.getByRole('alert')).toHaveTextContent(
    'belongs to another conversation',
  );
  expect(props.load).not.toHaveBeenCalled();
});

it('exports the same reviewed transcript as a PDF (parity row 2)', async () => {
  const props = options();
  props.execute.mockImplementationOnce(async (_id, command) => ({
    command_id: command.command_id,
    status: 'completed',
    action: command.type,
    export: {
      attachment_ref: 'conversation-1:export-2',
      file_name: 'conversation-export.pdf',
      size_bytes: 4321,
      checkpoint_revision: 'checkpoint-7',
    },
  }));
  renderActions(<ConversationActions {...props} />);
  await screen.findByDisplayValue('Saved conversation');
  fireEvent.click(screen.getByRole('button', { name: 'Export as PDF' }));
  await screen.findByText('Conversation export saved.');
  expect(props.review.mock.calls[0][3]).toEqual({ format: 'pdf' });
  expect(props.execute.mock.calls[0][1].payload).toEqual({
    checkpoint_revision: 'checkpoint-7',
    action_digest: 'a'.repeat(64),
    export_title: 'Saved conversation',
    export_format: 'pdf',
  });
  expect(props.save).toHaveBeenCalledWith(
    'conversation-1:export-2',
    'conversation-export.pdf',
  );
});

async function exportWith(save: ReturnType<typeof options>['save']) {
  const props = { ...options(), save };
  props.execute.mockImplementationOnce(async (_id, command) => ({
    command_id: command.command_id,
    status: 'completed',
    action: command.type,
    export: {
      attachment_ref: 'conversation-1:export-3',
      file_name: 'conversation-export.md',
      size_bytes: 321,
      checkpoint_revision: 'checkpoint-7',
    },
  }));
  renderActions(<ConversationActions {...props} />);
  await screen.findByDisplayValue('Saved conversation');
  fireEvent.click(screen.getByRole('button', { name: 'Export as Markdown' }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  return props;
}

it('says a save into Exports went there, with Show in folder (B238)', async () => {
  const reveal = vi.fn().mockResolvedValue(true);
  await exportWith(
    vi.fn().mockResolvedValue({
      status: 'ok',
      value: {
        kind: 'exports',
        fileName: 'conversation-export.md',
        folder: 'Row-Bot › Exports',
        reveal,
      },
    }),
  );
  await screen.findByText(
    'Saved to Row-Bot › Exports as conversation-export.md.',
  );
  expect(screen.getByText('Saved to Row-Bot › Exports')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Show in folder' }));
  await waitFor(() => expect(reveal).toHaveBeenCalledTimes(1));
});

it('never says a browser download was saved (B238)', async () => {
  await exportWith(
    vi.fn().mockResolvedValue({ status: 'ok', value: { kind: 'download' } }),
  );
  await screen.findByText('Download started.');
  expect(screen.queryByText(/export saved|Saved to/)).toBeNull();
});

it('says a file that could not be written was not saved, and a cancel says nothing (B238)', async () => {
  const save = vi
    .fn()
    .mockResolvedValueOnce({ status: 'unavailable', reason: 'save_failed' })
    .mockResolvedValueOnce({ status: 'cancelled' });
  await exportWith(save);
  await screen.findByText(
    'Row-Bot couldn’t write the file there. Choose another folder and try again.',
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Download conversation export' }),
  );
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByText(/couldn’t write/)).toBeNull());
  expect(screen.queryByText(/export saved|Saved to/)).toBeNull();
});
