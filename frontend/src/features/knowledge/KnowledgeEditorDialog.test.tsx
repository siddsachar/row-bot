import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { KnowledgeAction } from './KnowledgeEditor';
import KnowledgeEditorDialog from './KnowledgeEditorDialog';
import { createKnowledgeSessions } from './knowledge-sessions';

function setup() {
  const state = {
    handshake: {
      instance_id: 'instance',
      server_epoch: 'epoch',
      client_session_id: 'session',
    },
  };
  const controller = {
    getSnapshot: () =>
      state as unknown as ReturnType<ClientController['getSnapshot']>,
    knowledgeEditor: vi.fn(async (id: string | null) => ({
      schema_version: 1 as const,
      entity_types: ['fact'],
      entity: id
        ? {
            id,
            revision: 'a'.repeat(64),
            fields: {
              entity_type: 'fact' as const,
              subject: 'Saved subject',
              description: 'Saved description',
              aliases: '',
              tags: '',
            },
            status: 'active' as const,
            created_at: '',
            updated_at: '',
            saved_state: 'saved' as const,
            projection_state: 'unknown' as const,
          }
        : null,
    })),
    reviewKnowledge: vi.fn(
      async (action: KnowledgeAction, payload: Record<string, unknown>) => ({
        schema_version: 1 as const,
        action,
        entity_id: payload.entity_id as string | null,
        revision: payload.revision as string,
        fields_digest: 'fields-one',
        reuse_entity_id: null,
        review_id: 'review-one',
      }),
    ),
    executeKnowledge: vi.fn(async (command: { command_id: string }) => ({
      schema_version: 1 as const,
      command_id: command.command_id,
      status: 'completed' as const,
      entity_id: 'entry',
      revision: 'b'.repeat(64),
      saved_state: 'saved' as const,
      projection_state: 'pending' as const,
      reused: false,
    })),
    knowledgeReceipt: vi.fn(),
  };
  return createKnowledgeSessions(controller);
}

it('traps the external editor in one dialog, closes with Escape, returns focus, and retains drafts', async () => {
  const owner = setup();
  render(
    <>
      <button onClick={() => owner.open('entry')}>Edit saved subject</button>
      <KnowledgeEditorDialog owner={owner} />
    </>,
  );
  const opener = screen.getByRole('button', { name: 'Edit saved subject' });
  opener.focus();
  fireEvent.click(opener);
  expect(await screen.findByRole('dialog')).toHaveAccessibleName(
    'Edit knowledge',
  );
  const subject = await screen.findByRole('textbox', { name: 'Subject' });
  fireEvent.change(subject, { target: { value: 'Retained draft' } });
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  await vi.waitFor(() => expect(opener).toHaveFocus());
  fireEvent.click(opener);
  expect(await screen.findByRole('textbox', { name: 'Subject' })).toHaveValue(
    'Retained draft',
  );
});

it('Add memory opens a blank editor that creates knowledge', async () => {
  const owner = setup();
  render(
    <>
      <button onClick={() => owner.open(null)}>Add memory</button>
      <KnowledgeEditorDialog owner={owner} />
    </>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Add memory' }));
  const dialog = await screen.findByRole('dialog');
  expect(dialog).toHaveAccessibleName('Add memory');
  expect(await screen.findByRole('textbox', { name: 'Subject' })).toHaveValue(
    '',
  );
  expect(
    screen.getByRole('combobox', { name: 'Entity type' }),
  ).toHaveDisplayValue('Fact');
  expect(screen.getByRole('button', { name: 'Save knowledge' })).toBeDisabled();
});

it('a completed save tells the page its knowledge changed before any reload', async () => {
  const owner = setup();
  const onMutation = vi.fn();
  render(
    <>
      <button onClick={() => owner.open('entry')}>Edit saved subject</button>
      <KnowledgeEditorDialog owner={owner} onMutation={onMutation} />
    </>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Edit saved subject' }));
  const subject = await screen.findByRole('textbox', { name: 'Subject' });
  expect(onMutation).not.toHaveBeenCalled();
  fireEvent.change(subject, { target: { value: 'Changed subject' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save knowledge' }));
  await screen.findByText(/Knowledge saved\./);
  expect(onMutation).toHaveBeenCalledTimes(1);
});
