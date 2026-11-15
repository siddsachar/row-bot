import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import type { BuddySnapshot } from '../shell/BuddyControls';
import { BuddyCard, buddyStatus } from './BuddyCard';

const snapshot: BuddySnapshot = {
  schema_version: 1,
  revision: 'r1',
  placement: 'docked',
  native_placement_retained: false,
  preferences: {
    visible: true,
    collapsed: false,
    display_name: 'Pip',
    personality: 'warm_mystical',
    personality_description: '',
    bubble_verbosity: 'normal',
    animation_intensity: 'normal',
    pack_id: 'glyph',
  },
  status: {
    mood: 'curious',
    animation: 'idle_breathe',
    energy: 60,
    focus: 20,
    alert: 0,
    event_id: 1,
    label: 'Idle',
  },
  activity: 'idle',
};

function at(activity: BuddySnapshot['activity'], label: string) {
  return { ...snapshot, activity, status: { ...snapshot.status, label } };
}

it('says Ready at rest, never Idle, and gives each activity its tone', () => {
  expect(buddyStatus(snapshot)).toEqual({
    text: 'Ready',
    tone: 'neutral',
    pulse: false,
  });
  expect(buddyStatus(at('tool', 'Using a tool'))).toEqual({
    text: 'Using a tool',
    tone: 'accent',
    pulse: true,
  });
  expect(buddyStatus(at('approval', 'Needs approval')).tone).toBe('warning');
  expect(buddyStatus(at('error', 'Error')).tone).toBe('danger');
  expect(buddyStatus(at('completed', 'Done')).tone).toBe('success');
  // A finished turn whose label went back to Idle still reads Ready.
  expect(buddyStatus(at('completed', 'Idle')).text).toBe('Ready');
  // Work beside a conversation's own (a workflow, Learning) is live too.
  expect(buddyStatus(at('idle', 'Workflow running'))).toEqual({
    text: 'Workflow running',
    tone: 'accent',
    pulse: false,
  });
});

function Where() {
  const location = useLocation();
  return <p data-testid="at">{location.pathname}</p>;
}

it('opens the conversation Buddy is busy in when it is not the open one', async () => {
  const state = {
    selectedConversationId: 'here',
    conversations: [{ id: 'there', title: 'Ship 5.3.1' }],
  };
  const controller = {
    getSnapshot: () => state,
    subscribe: () => () => undefined,
  } as unknown as ClientController;
  render(
    <RuntimeContext.Provider
      value={{ controller, platform: {} as ClientPlatform }}
    >
      <MemoryRouter initialEntries={['/conversations/here']}>
        <Routes>
          <Route
            path="*"
            element={
              <>
                <BuddyCard
                  snapshot={{
                    ...at('approval', 'Needs approval'),
                    conversation_id: 'there',
                  }}
                  avatar={<span>avatar</span>}
                  portrait={<span>portrait</span>}
                  onSettings={vi.fn()}
                />
                <Where />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </RuntimeContext.Provider>,
  );
  const card = screen.getByRole('complementary', { name: 'Buddy companion' });
  await userEvent.click(
    within(card).getByRole('button', { name: /Needs approval/ }),
  );
  expect(screen.getByTestId('at')).toHaveTextContent('/conversations/there');
});
