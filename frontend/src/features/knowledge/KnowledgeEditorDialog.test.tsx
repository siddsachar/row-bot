import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
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
    reviewKnowledge: vi.fn(),
    executeKnowledge: vi.fn(),
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
