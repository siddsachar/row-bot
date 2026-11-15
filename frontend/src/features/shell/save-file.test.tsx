import { act, fireEvent, render, screen } from '@testing-library/react';
import type { ContextType } from 'react';
import { expect, it, vi } from 'vitest';
import type { CapabilityResult, SavedFile } from '../../platform/types';
import { RuntimeContext } from '../../runtime';
import { OverlayProvider } from '../../ui/overlays';
import { saveProblem, useSaveFile, type SaveSource } from './save-file';

const REFERENCE = `save:${'b'.repeat(32)}`;

function setup(save: () => Promise<CapabilityResult<SavedFile>>) {
  const saveOffer = vi.fn(async () => ({
    reference: REFERENCE,
    file_name: 'launch-deck.pdf',
  }));
  const platformSave = vi.fn(save);
  const runtime = {
    controller: { saveOffer },
    platform: { save: platformSave },
  } as unknown as NonNullable<ContextType<typeof RuntimeContext>>;
  const source: SaveSource = {
    kind: 'page',
    page_id: 'page-1a2b3c4d',
    theme: { mode: 'light', accent: '#336699' },
  } as unknown as SaveSource;
  let outcome: CapabilityResult<SavedFile> | null = null;
  function Button() {
    const saveFile = useSaveFile();
    return (
      <button
        type="button"
        onClick={() =>
          void saveFile!(source).then((value) => (outcome = value))
        }
      >
        Save
      </button>
    );
  }
  render(
    <RuntimeContext.Provider value={runtime}>
      <OverlayProvider>
        <Button />
      </OverlayProvider>
    </RuntimeContext.Provider>,
  );
  return { saveOffer, platformSave, source, outcome: () => outcome };
}

it('asks for an offer, saves it through the platform, then offers Show in folder', async () => {
  const reveal = vi.fn(async () => true);
  const { saveOffer, platformSave, source } = setup(async () => ({
    status: 'ok',
    value: { kind: 'file', reveal },
  }));
  await act(async () => fireEvent.click(screen.getByText('Save')));
  expect(saveOffer).toHaveBeenCalledWith(source);
  expect(platformSave).toHaveBeenCalledWith(REFERENCE, 'launch-deck.pdf');
  expect(await screen.findByText('Saved launch-deck.pdf')).toBeVisible();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Show in folder' })),
  );
  expect(reveal).toHaveBeenCalledOnce();
});

it('says where a copy went while the desktop window reconnects', async () => {
  setup(async () => ({
    status: 'ok',
    value: {
      kind: 'exports',
      fileName: 'launch-deck 2.pdf',
      folder: 'Row-Bot › Exports',
      reveal: async () => true,
    },
  }));
  await act(async () => fireEvent.click(screen.getByText('Save')));
  expect(
    await screen.findByText('Saved launch-deck 2.pdf to Row-Bot › Exports'),
  ).toBeVisible();
});

it('a browser download never claims the file was saved, and a cancel says nothing', async () => {
  setup(async () => ({ status: 'ok', value: { kind: 'download' } }));
  await act(async () => fireEvent.click(screen.getByText('Save')));
  expect(await screen.findByText('Downloading launch-deck.pdf')).toBeVisible();
  expect(screen.queryByText(/^Saved/)).toBeNull();
  expect(saveProblem({ status: 'cancelled' }, 'the page')).toBe('');
});

it('words why a save didn’t happen', () => {
  expect(
    saveProblem(
      { status: 'unavailable', reason: 'user_gesture_required' },
      'the page',
    ),
  ).toBe('Press it again to save the page.');
  expect(
    saveProblem({ status: 'unavailable', reason: 'save_failed' }, 'the page'),
  ).toMatch(/Choose another folder/);
  expect(
    saveProblem(
      { status: 'unavailable', reason: 'operation_failed' },
      'the page',
    ),
  ).toBe('Couldn’t save the page. Try again.');
});
