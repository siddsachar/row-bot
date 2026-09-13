import { expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import WorkspaceFileEditor from './WorkspaceFileEditor';
import type {
  WorkspaceEditableFile,
  WorkspaceEditResult,
} from '../../api/types';

const snapshot: WorkspaceEditableFile = {
  review_token: 'a'.repeat(64),
  resource_id: 'workspace',
  conversation_id: 'chat',
  relative_path: 'hello.txt',
  resource_revision: '1',
  binding_id: 'binding',
  binding_revision: '1',
  target: 'workspace',
  status: 'text',
  content: 'Original',
  digest: 'a'.repeat(64),
};
const result: WorkspaceEditResult = {
  ...snapshot,
  status: 'saved',
  file_saved: true,
  ledger_saved: true,
  digest: 'b'.repeat(64),
};
function fixture() {
  const props = {
    path: 'hello.txt',
    visible: true,
    load: vi.fn().mockResolvedValue(snapshot),
    save: vi.fn().mockResolvedValue(result),
    close: vi.fn(),
    discard: vi.fn(),
  };
  const view = render(<WorkspaceFileEditor {...props} />);
  return { props, view };
}

it('loads inertly and saves only the exact reviewed file on explicit action', async () => {
  const { props } = fixture();
  const input = await screen.findByRole('textbox', { name: 'File contents' });
  expect(input).toHaveValue('Original');
  expect(props.save).not.toHaveBeenCalled();
  await userEvent.clear(input);
  await userEvent.type(input, 'Edited');
  await userEvent.click(screen.getByRole('button', { name: 'Save file' }));
  await screen.findByText(
    'File saved. Original bytes remain available in edit recovery.',
  );
  expect(props.save).toHaveBeenCalledWith(snapshot, 'Edited');
});

it('preserves draft and reviewed snapshot across closing and reopening', async () => {
  const { props, view } = fixture();
  const input = await screen.findByRole('textbox', { name: 'File contents' });
  await userEvent.clear(input);
  await userEvent.type(input, 'Unsent');
  await userEvent.click(
    screen.getByRole('button', { name: 'Close editor and keep draft' }),
  );
  expect(props.close).toHaveBeenCalledOnce();
  view.rerender(<WorkspaceFileEditor {...props} visible={false} />);
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  view.rerender(<WorkspaceFileEditor {...props} visible />);
  expect(screen.getByRole('textbox')).toHaveValue('Unsent');
  expect(props.load).toHaveBeenCalledOnce();
});

it('locks uncertain intent and explicitly retries the original snapshot and bytes', async () => {
  const { props } = fixture();
  props.save.mockResolvedValueOnce({
    ...result,
    status: 'partial',
    code: 'change_ledger_incomplete',
  });
  const input = await screen.findByRole('textbox');
  await userEvent.clear(input);
  await userEvent.type(input, 'Retained');
  await userEvent.click(screen.getByRole('button', { name: 'Save file' }));
  await screen.findByText(/The save is incomplete/);
  expect(input).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Discard draft' })).toBeDisabled();
  await userEvent.click(
    screen.getByRole('button', { name: 'Retry original save' }),
  );
  await waitFor(() => expect(input).toBeEnabled());
  expect(props.save.mock.calls).toEqual([
    [snapshot, 'Retained'],
    [snapshot, 'Retained'],
  ]);
});

it('refreshes a conflicting file while retaining the draft until explicit save', async () => {
  const { props } = fixture();
  props.save.mockResolvedValueOnce({
    ...result,
    status: 'conflict',
    code: 'file_revision_conflict',
  });
  const input = await screen.findByRole('textbox');
  await userEvent.clear(input);
  await userEvent.type(input, 'My draft');
  await userEvent.click(screen.getByRole('button', { name: 'Save file' }));
  await screen.findByText(/The file was not saved/);
  const changed = {
    ...snapshot,
    content: 'External edit',
    digest: 'c'.repeat(64),
  };
  props.load.mockResolvedValue(changed);
  await userEvent.click(
    screen.getByRole('button', { name: 'Refresh file revision' }),
  );
  await waitFor(() => expect(props.load).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(input).toBeEnabled());
  expect(input).toHaveValue('My draft');
  await userEvent.click(screen.getByRole('button', { name: 'Save file' }));
  expect(props.save).toHaveBeenLastCalledWith(changed, 'My draft');
});

it('does not expose truncated or binary data as an editable draft', async () => {
  const props = {
    path: 'large.txt',
    visible: true,
    load: vi.fn().mockResolvedValue({
      ...snapshot,
      relative_path: 'large.txt',
      status: 'too_large',
      content: '',
      code: 'inline_edit_too_large',
    }),
    save: vi.fn(),
    close: vi.fn(),
    discard: vi.fn(),
  };
  render(<WorkspaceFileEditor {...props} />);
  await screen.findByText(/Inline editing unavailable/);
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save file' })).toBeDisabled();
  expect(props.save).not.toHaveBeenCalled();
});
