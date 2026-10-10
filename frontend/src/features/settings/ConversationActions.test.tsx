import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { expect, it, vi } from 'vitest';
import { OverlayProvider, useOverlay } from '../../ui/overlays';
import ConversationActions, {
  conversationActionsDialog,
  createConversationActionsSession,
  runConversationAction,
  type ConversationActionReview,
  type ConversationActionSnapshot,
  type ConversationActionsProps,
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

function exportReceipt(
  props: ReturnType<typeof options>,
  fileName = 'conversation-export.md',
) {
  props.execute.mockImplementationOnce(async (_id, command) => ({
    command_id: command.command_id,
    status: 'completed',
    action: command.type,
    export: {
      attachment_ref: 'conversation-1:export-1',
      file_name: fileName,
      size_bytes: 321,
      checkpoint_revision: 'checkpoint-7',
    },
  }));
}

/** Opens the actions the way the header and the sidebar do (B237). */
function Opener({
  props,
  deleted,
}: {
  props: Omit<ConversationActionsProps, 'onDelete'>;
  deleted?: () => void;
}) {
  const overlay = useOverlay();
  return (
    <button
      type="button"
      onClick={() =>
        overlay.open(
          conversationActionsDialog(
            {
              title: 'Saved conversation',
              updated_at: new Date().toISOString(),
              category: 'chat',
            },
            props.session,
            <ConversationActions
              {...props}
              onDelete={(closeActions) =>
                overlay.open({
                  kind: 'alert',
                  title: 'Delete conversation?',
                  description: 'This removes its history.',
                  confirmLabel: 'Delete conversation',
                  onConfirm: () => {
                    closeActions();
                    deleted?.();
                  },
                })
              }
            />,
          ),
        )
      }
    >
      Share or export
    </button>
  );
}

async function openDialog(
  props: ReturnType<typeof options>,
  deleted?: () => void,
) {
  const user = userEvent.setup();
  render(
    <OverlayProvider>
      <Opener props={props} deleted={deleted} />
    </OverlayProvider>,
  );
  await user.click(screen.getByRole('button', { name: 'Share or export' }));
  const dialog = screen.getByRole('dialog', { name: 'Saved conversation' });
  await within(dialog).findByDisplayValue('Saved conversation');
  return { user, dialog };
}

it('is one dialog named after the conversation, with no second heading or archive text (B237)', async () => {
  const props = options();
  const { dialog } = await openDialog(props);
  expect(within(dialog).getAllByRole('heading')).toHaveLength(1);
  expect(dialog).toHaveAccessibleDescription(/^Chat · updated /);
  expect(within(dialog).queryByText(/archive/i)).toBeNull();
  expect(within(dialog).queryByText(/Review changes|Rename, pin/)).toBeNull();
  // Opening only reads; nothing is reviewed or changed.
  expect(props.load).toHaveBeenCalledWith(
    'conversation-1',
    expect.any(AbortSignal),
  );
  expect(props.review).not.toHaveBeenCalled();
  expect(props.execute).not.toHaveBeenCalled();
});

it('renames on Enter, not when the field loses focus, and retitles the dialog (B237)', async () => {
  const props = options();
  const { user, dialog } = await openDialog(props);
  const name = within(dialog).getByLabelText('Name');
  const saveName = within(dialog).getByRole('button', { name: 'Save' });
  expect(saveName).toBeDisabled();
  await user.clear(name);
  await user.type(name, 'Reviewed name');
  expect(saveName).toBeEnabled();
  await user.tab();
  expect(props.review).not.toHaveBeenCalled();
  await user.click(name);
  await user.keyboard('{Enter}');
  await within(dialog).findByText('Name saved.');
  expect(props.review).toHaveBeenCalledExactlyOnceWith(
    'conversation-1',
    'conversation.rename',
    '4',
    { title: 'Reviewed name' },
    expect.any(AbortSignal),
  );
  expect(props.execute).toHaveBeenCalledOnce();
  expect(props.onChanged).toHaveBeenCalledWith(
    expect.objectContaining({ title: 'Reviewed name', revision: '5' }),
  );
  expect(
    screen.getByRole('dialog', { name: 'Reviewed name' }),
  ).toBeInTheDocument();
  expect(saveName).toBeDisabled();
  expect(props.session.hasRetained()).toBe(false);
});

it('pins and unpins with a switch through the reviewed command (B237)', async () => {
  const props = options();
  const { user, dialog } = await openDialog(props);
  const pin = within(dialog).getByRole('switch', { name: 'Pin' });
  expect(pin).not.toBeChecked();
  await user.click(pin);
  await waitFor(() => expect(pin).toBeChecked());
  expect(props.review).toHaveBeenCalledWith(
    'conversation-1',
    'conversation.pin',
    '4',
    { pinned: true },
    expect.any(AbortSignal),
  );
  expect(within(dialog).getByRole('status')).toHaveTextContent('Pinned.');
  await user.click(pin);
  await waitFor(() => expect(pin).not.toBeChecked());
  expect(props.review.mock.calls[1][3]).toEqual({ pinned: false });
  expect(props.execute).toHaveBeenCalledTimes(2);
});

it('keeps the switch where it was moved while the pin is saved, and moves it back if it fails', async () => {
  const props = options();
  let answer!: () => void;
  props.review.mockImplementationOnce(
    () =>
      new Promise((_, reject) => {
        answer = () => reject(Error('changed'));
      }),
  );
  const { user, dialog } = await openDialog(props);
  const pin = within(dialog).getByRole('switch', { name: 'Pin' });
  await user.click(pin);
  expect(pin).toBeChecked();
  await act(async () => answer());
  expect(pin).not.toBeChecked();
  expect(within(dialog).getByRole('status')).toHaveTextContent(
    'The conversation changed or this action is unavailable.',
  );
  expect(props.execute).not.toHaveBeenCalled();
});

it('saves a Markdown export in one click, then closes and confirms it (B237)', async () => {
  const props = options();
  exportReceipt(props);
  const { user, dialog } = await openDialog(props);
  await user.click(within(dialog).getByRole('button', { name: 'Markdown' }));
  expect(await screen.findByText('Conversation export saved.')).toBeVisible();
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(props.execute.mock.calls[0][1].payload).toEqual({
    checkpoint_revision: 'checkpoint-7',
    action_digest: 'a'.repeat(64),
    export_title: 'Saved conversation',
  });
  expect(props.save).toHaveBeenCalledExactlyOnceWith(
    'conversation-1:export-1',
    'conversation-export.md',
  );
  expect(props.session.hasRetained()).toBe(false);
});

it('says a save into Exports went there, with Show in folder (B238)', async () => {
  const props = options();
  exportReceipt(props);
  const reveal = vi.fn().mockResolvedValue(true);
  props.save.mockResolvedValueOnce({
    status: 'ok',
    value: {
      kind: 'exports',
      fileName: 'conversation-export.md',
      folder: 'Row-Bot › Exports',
      reveal,
    },
  });
  const { user, dialog } = await openDialog(props);
  await user.click(within(dialog).getByRole('button', { name: 'Markdown' }));
  expect(await screen.findByText('Saved to Row-Bot › Exports')).toBeVisible();
  // jsdom lacks the pointer capture Radix toasts use for swipes.
  fireEvent.click(screen.getByRole('button', { name: 'Show in folder' }));
  await waitFor(() => expect(reveal).toHaveBeenCalledTimes(1));
});

it('never says a browser download was saved (B238)', async () => {
  const props = options();
  exportReceipt(props);
  props.save.mockResolvedValueOnce({
    status: 'ok',
    value: { kind: 'download' },
  });
  const { user, dialog } = await openDialog(props);
  await user.click(within(dialog).getByRole('button', { name: 'Markdown' }));
  expect(await screen.findByText('Download started.')).toBeVisible();
  expect(screen.queryByText(/export saved|Saved to/)).toBeNull();
});

it('keeps the dialog open to say a file was not written, retries the same export, and a cancel says nothing (B238)', async () => {
  const props = options();
  exportReceipt(props);
  props.save
    .mockResolvedValueOnce({ status: 'unavailable', reason: 'save_failed' })
    .mockResolvedValueOnce({ status: 'cancelled' });
  const { user, dialog } = await openDialog(props);
  await user.click(within(dialog).getByRole('button', { name: 'Markdown' }));
  expect(
    await within(dialog).findByText(
      'Row-Bot couldn’t write the file there. Choose another folder and try again.',
    ),
  ).toBeVisible();
  await user.click(within(dialog).getByRole('button', { name: 'Save export' }));
  await waitFor(() => expect(props.save).toHaveBeenCalledTimes(2));
  expect(props.save.mock.calls[1]).toEqual(props.save.mock.calls[0]);
  expect(props.execute).toHaveBeenCalledOnce();
  await waitFor(() =>
    expect(within(dialog).queryByText(/couldn’t write/)).toBeNull(),
  );
  expect(within(dialog).queryByRole('button', { name: 'Save export' })).toBe(
    null,
  );
  expect(screen.getByRole('dialog')).toBe(dialog);
  expect(screen.queryByText(/export saved|Saved to/)).toBeNull();
});

it('exports the same reviewed transcript as a PDF (parity row 2)', async () => {
  const props = options();
  exportReceipt(props, 'conversation-export.pdf');
  const { user, dialog } = await openDialog(props);
  await user.click(within(dialog).getByRole('button', { name: 'PDF' }));
  expect(await screen.findByText('Conversation export saved.')).toBeVisible();
  expect(props.review.mock.calls[0][3]).toEqual({ format: 'pdf' });
  expect(props.execute.mock.calls[0][1].payload).toEqual({
    checkpoint_revision: 'checkpoint-7',
    action_digest: 'a'.repeat(64),
    export_title: 'Saved conversation',
    export_format: 'pdf',
  });
  expect(props.save).toHaveBeenCalledWith(
    'conversation-1:export-1',
    'conversation-export.pdf',
  );
});

it('opens the delete confirmation over the dialog; Cancel returns, Delete closes both (B237)', async () => {
  const props = options();
  const deleted = vi.fn();
  const { user, dialog } = await openDialog(props, deleted);
  await user.click(
    within(dialog).getByRole('button', { name: 'Delete conversation…' }),
  );
  let confirmation = screen.getByRole('alertdialog', {
    name: 'Delete conversation?',
  });
  await user.click(
    within(confirmation).getByRole('button', { name: 'Cancel' }),
  );
  await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
  expect(
    screen.getByRole('dialog', { name: 'Saved conversation' }),
  ).toBeVisible();
  expect(deleted).not.toHaveBeenCalled();

  await user.click(
    within(dialog).getByRole('button', { name: 'Delete conversation…' }),
  );
  confirmation = screen.getByRole('alertdialog', {
    name: 'Delete conversation?',
  });
  await user.click(
    within(confirmation).getByRole('button', { name: 'Delete conversation' }),
  );
  expect(deleted).toHaveBeenCalledOnce();
  await waitFor(() =>
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
  );
  expect(screen.queryByRole('alertdialog')).toBeNull();
});

it('reads the conversation afresh each time it opens', async () => {
  const props = options();
  const first = renderActions(<ConversationActions {...props} />);
  await screen.findByDisplayValue('Saved conversation');
  first.unmount();
  props.load.mockResolvedValueOnce({
    ...snapshot,
    revision: '9',
    title: 'Renamed in the header',
  });
  renderActions(<ConversationActions {...props} />);
  await screen.findByDisplayValue('Renamed in the header');
  expect(props.load).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole('switch', { name: 'Pin' }));
  await waitFor(() => expect(props.review).toHaveBeenCalledOnce());
  expect(props.review.mock.calls[0][2]).toBe('9');
});

it('opens with the name ready to type over', async () => {
  const props = options();
  renderActions(<ConversationActions {...props} />);
  const name = await screen.findByDisplayValue('Saved conversation');
  await waitFor(() => expect(name).toHaveFocus());
  expect((name as HTMLInputElement).selectionEnd).toBe(
    'Saved conversation'.length,
  );
});

it('retains an uncertain command across remount and checks the same identity', async () => {
  const props = options();
  props.execute.mockRejectedValueOnce(Error('response lost'));
  const first = renderActions(<ConversationActions {...props} />);
  await screen.findByDisplayValue('Saved conversation');
  fireEvent.click(screen.getByRole('switch', { name: 'Pin' }));
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
  await screen.findByText('Pinned.');
  expect(props.execute.mock.calls[1]).toEqual(firstCall);
  expect(props.review).toHaveBeenCalledTimes(1);
  expect(props.load).toHaveBeenCalledTimes(1);
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

it('runs one reviewed action without the dialog for bulk use', async () => {
  const props = options();
  const outcome = await runConversationAction(
    props,
    'conversation-1',
    'conversation.pin',
    { pinned: true },
    new AbortController().signal,
  );
  expect(outcome).toMatchObject({
    status: 'completed',
    receipt: { conversation: { pinned: true } },
  });
  expect(props.execute.mock.calls[0][1]).toMatchObject({
    type: 'conversation.pin',
    expected_revision: '4',
    payload: {
      pinned: true,
      checkpoint_revision: 'checkpoint-7',
      action_digest: 'a'.repeat(64),
    },
  });
  props.review.mockResolvedValueOnce({
    ...(await props.review.getMockImplementation()!(
      'conversation-1',
      'conversation.pin',
      '4',
      {},
    )),
    revision: '3',
  });
  await expect(
    runConversationAction(
      props,
      'conversation-1',
      'conversation.pin',
      { pinned: true },
      new AbortController().signal,
    ),
  ).resolves.toEqual({ status: 'failed' });
  props.execute.mockRejectedValueOnce(Error('response lost'));
  await expect(
    runConversationAction(
      props,
      'conversation-1',
      'conversation.export',
      {},
      new AbortController().signal,
    ),
  ).resolves.toEqual({ status: 'uncertain' });
  expect(props.execute).toHaveBeenCalledTimes(2);
});
