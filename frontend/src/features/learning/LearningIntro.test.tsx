import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { expect, it } from 'vitest';
import { LearningIntro } from './LearningIntro';
import type { LearningIO } from './learning-state';
import { fakeIO, learningSnapshot } from './test-data';

function show(io: LearningIO | null) {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<LearningIntro io={io} />} />
        <Route
          path="/settings/learning"
          element={<p>Learning settings page</p>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

const introduced = (overrides = {}) =>
  learningSnapshot({ state: 'on', introduce: true, ...overrides });

it('Home says once that Learning is on, what it does and what it costs (O195)', async () => {
  const io = fakeIO(introduced());
  show(io);

  const note = screen.getByRole('region', {
    name: 'Row-Bot learns from your corrections',
  });
  expect(note).toHaveTextContent(
    'When you correct a reply, or stop or retry one, Row-Bot works out a fix during quiet hours, tests it, and asks you before using it.',
  );
  expect(note).toHaveTextContent(
    'What it records stays on this computer. Finding and testing fixes uses GPT-5.6 Sol (your default model), up to 2M tokens a month.',
  );

  fireEvent.click(screen.getByRole('button', { name: 'Got it' }));
  await act(async () => undefined);

  expect(io.run).toHaveBeenCalledWith('close_intro');
  expect(
    screen.queryByRole('region', {
      name: 'Row-Bot learns from your corrections',
    }),
  ).toBeNull();
});

it('its settings button closes the note and opens Settings › Learning', async () => {
  const io = fakeIO(introduced());
  show(io);

  fireEvent.click(screen.getByRole('button', { name: 'Learning settings' }));
  await act(async () => undefined);

  expect(io.run).toHaveBeenCalledWith('close_intro');
  expect(screen.getByText('Learning settings page')).toBeVisible();
});

it('with no monthly model calls it says so instead of a cost', () => {
  show(
    fakeIO(
      introduced({
        spending: { ...learningSnapshot().spending, limit_tokens: 0 },
      }),
    ),
  );
  expect(
    screen.getByRole('region', {
      name: 'Row-Bot learns from your corrections',
    }),
  ).toHaveTextContent(
    'What it records stays on this computer. It makes no model calls.',
  );
});

it('shows nothing once closed, while Learning is off, or before Learning loads', () => {
  const { unmount } = show(
    fakeIO(learningSnapshot({ state: 'on', introduce: false })),
  );
  expect(screen.queryByRole('region')).toBeNull();
  unmount();
  show(fakeIO(learningSnapshot({ state: 'off', introduce: false })));
  expect(screen.queryByRole('region')).toBeNull();
  show(null);
  expect(screen.queryByRole('region')).toBeNull();
});
