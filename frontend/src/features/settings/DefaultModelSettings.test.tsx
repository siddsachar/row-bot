import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { DefaultModelSnapshot } from '../../api/types';
import DefaultModelSettings, {
  DefaultModelSession,
} from './DefaultModelSettings';

const snapshot: DefaultModelSnapshot = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  selection_ref: 'model:openai:same',
  provider_id: 'openai',
  model_id: 'same',
  saved_state: 'saved',
  runtime_state: 'unknown',
};
function fixture() {
  return {
    load: vi.fn().mockResolvedValue(snapshot),
    review: vi.fn(
      async (revision: string, provider: string, model: string) => ({
        settings_revision: revision,
        provider_id: provider,
        model_id: model,
        operation: 'provider.default_model.save' as const,
        action_digest: 'digest',
        nonce: 'original-nonce',
      }),
    ),
    apply: vi.fn().mockResolvedValue({
      ...snapshot,
      provider_id: 'anthropic',
      selection_ref: 'model:anthropic:same',
    }),
    receipt: vi
      .fn()
      .mockResolvedValue({ status: 'uncertain', selection: snapshot }),
    onSaved: vi.fn(),
    onBrowseModels: vi.fn(),
  };
}
async function save(props: ReturnType<typeof fixture>) {
  fireEvent.click(screen.getByRole('button', { name: 'Save default model' }));
  await waitFor(() => expect(props.apply).toHaveBeenCalledOnce());
}
it('reads saved state without effects and requires exact reviewed provider identity', async () => {
  const props = fixture();
  render(<DefaultModelSettings {...props} />);
  expect(
    screen.getByRole('heading', { name: 'Defaults', level: 3 }),
  ).toBeVisible();
  expect(
    screen.getByRole('heading', { name: 'Brain', level: 4 }),
  ).toBeVisible();
  expect(await screen.findByLabelText('Default provider ID')).toHaveValue(
    'openai',
  );
  expect(
    screen.getByRole('status', { name: 'Default model saved state' }),
  ).toHaveTextContent('Saved default: model:openai:same');
  expect(screen.getByText('Readiness not checked')).toBeVisible();
  expect(props.review).not.toHaveBeenCalled();
  expect(props.apply).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Default provider ID'), {
    target: { value: 'anthropic' },
  });
  await save(props);
  await screen.findByText(
    'Global default saved for future work. No provider was started or unloaded.',
  );
  expect(props.apply).toHaveBeenCalledExactlyOnceWith(
    expect.objectContaining({
      provider_id: 'anthropic',
      model_id: 'same',
      nonce: 'original-nonce',
    }),
    expect.any(String),
  );
});
it('retains an unsent draft through full remount', async () => {
  const props = fixture(),
    session = new DefaultModelSession();
  const first = render(<DefaultModelSettings {...props} session={session} />);
  await screen.findByDisplayValue('openai');
  fireEvent.change(screen.getByLabelText('Default exact model ID'), {
    target: { value: 'changed' },
  });
  expect(props.apply).not.toHaveBeenCalled();
  first.unmount();
  render(<DefaultModelSettings {...props} session={session} />);
  expect(screen.getByLabelText('Default exact model ID')).toHaveValue(
    'changed',
  );
  expect(props.load).toHaveBeenCalledOnce();
  expect(session.hasRetained()).toBe(true);
});
it('settles a late uncertain save after unmount and reads only its original receipt', async () => {
  const props = fixture(),
    session = new DefaultModelSession();
  let reject!: (cause: unknown) => void;
  props.apply.mockReturnValue(
    new Promise((_resolve, fail) => {
      reject = fail;
    }),
  );
  const first = render(<DefaultModelSettings {...props} session={session} />);
  await screen.findByDisplayValue('openai');
  await save(props);
  const originalId = props.apply.mock.calls[0][1];
  first.unmount();
  await act(async () => reject({ code: 'operation_uncertain' }));
  render(<DefaultModelSettings {...props} session={session} />);
  expect(screen.getByLabelText('Default provider ID')).toBeDisabled();
  expect(
    screen.queryByRole('button', { name: 'Discard unsent default' }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole('button', { name: 'Check original default receipt' }),
  );
  await waitFor(() =>
    expect(props.receipt).toHaveBeenCalledWith(
      originalId,
      expect.any(AbortSignal),
    ),
  );
  expect(props.apply).toHaveBeenCalledOnce();
  expect(session.hasRetained()).toBe(true);
});
it('clears private state and fences late success when the authentication owner is disposed', async () => {
  const props = fixture(),
    session = new DefaultModelSession();
  let resolve!: (value: DefaultModelSnapshot) => void;
  props.apply.mockReturnValue(
    new Promise((yes) => {
      resolve = yes;
    }),
  );
  render(<DefaultModelSettings {...props} session={session} />);
  await screen.findByDisplayValue('openai');
  await save(props);
  act(() => session.dispose());
  await act(async () => resolve(snapshot));
  expect(screen.getByLabelText('Default provider ID')).toHaveValue('');
  expect(props.onSaved).not.toHaveBeenCalled();
  expect(session.hasRetained()).toBe(false);
});
it('shows missing and unsupported saved choices explicitly without inferring or saving a fallback', async () => {
  const props = fixture();
  props.load.mockResolvedValue({
    ...snapshot,
    selection_ref: null,
    provider_id: null,
    model_id: null,
    saved_state: 'unavailable',
  });
  render(<DefaultModelSettings {...props} />);
  await screen.findByText(
    /Existing choice needs an explicit provider selection/,
  );
  expect(screen.getByLabelText('Default provider ID')).toHaveValue('');
  expect(props.apply).not.toHaveBeenCalled();
});
