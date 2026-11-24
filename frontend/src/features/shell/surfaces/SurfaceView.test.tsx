import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { TranscriptRow } from '../../../api/types';
import { registerPromptSender, setPromptReady } from '../composer-bridge';
import { TranscriptBlocks } from '../TranscriptBlocks';
import { evaluate, formatDate, writePath } from './a2ui';

type Block = TranscriptRow['blocks'][number];

let unregister: (() => void) | undefined;
afterEach(() => unregister?.());

function composer(accepts = true) {
  const sent = vi.fn((_text: string, _tapped?: unknown) => accepts);
  unregister = registerPromptSender('conversation-s', sent);
  setPromptReady('conversation-s', true);
  return sent;
}

function surface(tree: object, id = 'block:surface'): Block {
  return {
    id,
    type: 'surface',
    surface_id: 'trip',
    title: 'Lisbon trip',
    surface_json: JSON.stringify({
      surfaceId: 'trip',
      title: 'Lisbon trip',
      catalogId: 'https://row-bot.ai/a2ui/catalogs/rowbot/v1',
      ...tree,
    }),
    text: 'Lisbon trip',
  };
}

function show(block: Block) {
  return render(
    <TranscriptBlocks
      blocks={[block]}
      conversationId="conversation-s"
      messageId="reply-1"
    />,
  );
}

const TRIP = {
  components: [
    {
      id: 'root',
      component: 'Column',
      children: ['heading', 'budget', 'total', 'days', 'book'],
    },
    { id: 'heading', component: 'Text', text: '## Lisbon in **March**' },
    {
      id: 'budget',
      component: 'Slider',
      label: 'Budget',
      min: 500,
      max: 3000,
      value: { '@path': '/budget' },
    },
    {
      id: 'total',
      component: 'Text',
      text: {
        '@call': 'formatString',
        args: { value: 'Total ${/budget} for ${/days/0/name}' },
      },
    },
    {
      id: 'days',
      component: 'List',
      children: { path: '/days', componentId: 'day' },
    },
    {
      id: 'day',
      component: 'TextField',
      label: 'Stop',
      value: { '@path': 'name' },
    },
    {
      id: 'book',
      component: 'Button',
      child: 'book_label',
      variant: 'primary',
      action: {
        event: {
          name: 'book_hotel',
          context: { budget: { '@path': '/budget' }, fixed: 'yes' },
        },
      },
    },
    { id: 'book_label', component: 'Text', text: 'Book the hotel' },
  ],
  dataModel: { budget: 1500, days: [{ name: 'Alfama' }, { name: 'Belém' }] },
};

it('draws the reply’s interface from Row-Bot’s own controls', () => {
  composer();
  show(surface(TRIP));
  const region = screen.getByRole('region', { name: 'Lisbon trip' });
  expect(
    within(region).getByRole('heading', { name: 'Lisbon in March' }),
  ).toBeVisible();
  expect(within(region).getByRole('slider', { name: 'Budget' })).toHaveValue(
    '1500',
  );
  expect(within(region).getByText('Total 1,500 for Alfama')).toBeVisible();
  // A template draws one item per row of its list, each bound to its own row.
  expect(
    within(region)
      .getAllByRole('textbox', { name: 'Stop' })
      .map((input) => (input as HTMLInputElement).value),
  ).toEqual(['Alfama', 'Belém']);
});

it('updates every part bound to a value as the person edits it, sending nothing', () => {
  const sent = composer();
  show(surface(TRIP));
  fireEvent.change(screen.getByRole('slider', { name: 'Budget' }), {
    target: { value: '2000' },
  });
  fireEvent.change(screen.getAllByRole('textbox', { name: 'Stop' })[0], {
    target: { value: 'Sintra' },
  });
  expect(screen.getByText('Total 2,000 for Sintra')).toBeVisible();
  expect(sent).not.toHaveBeenCalled();
});

it('sends a press as the surface’s action with the values it names', () => {
  const sent = composer();
  show(surface(TRIP));
  fireEvent.change(screen.getByRole('slider', { name: 'Budget' }), {
    target: { value: '1800' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Book the hotel' }));
  expect(sent).toHaveBeenCalledWith('Book the hotel', {
    surface: {
      message_id: 'reply-1',
      surface_id: 'trip',
      name: 'book_hotel',
      source_component_id: 'book',
      context: { budget: 1800, fixed: 'yes' },
    },
  });
});

it('keeps a button off while its check fails, and says why', () => {
  composer();
  show(
    surface({
      components: [
        { id: 'root', component: 'Column', children: ['email', 'send'] },
        {
          id: 'email',
          component: 'TextField',
          label: 'Email',
          value: { '@path': '/email' },
        },
        {
          id: 'send',
          component: 'Button',
          child: 'send_label',
          action: { event: { name: 'subscribe' } },
          '@checks': [
            {
              condition: {
                '@call': 'email',
                args: { value: { '@path': '/email' } },
              },
              message: 'Enter an email address.',
            },
          ],
        },
        { id: 'send_label', component: 'Text', text: 'Subscribe' },
      ],
      dataModel: { email: '' },
    }),
  );
  expect(screen.getByRole('button', { name: 'Subscribe' })).toBeDisabled();
  expect(screen.getByText('Enter an email address.')).toBeVisible();
  fireEvent.change(screen.getByRole('textbox', { name: 'Email' }), {
    target: { value: 'sam@example.com' },
  });
  expect(screen.getByRole('button', { name: 'Subscribe' })).toBeEnabled();
});

it('never loads a picture from elsewhere by itself', () => {
  composer();
  const { container } = show(
    surface({
      components: [
        {
          id: 'root',
          component: 'Image',
          url: 'https://tracker.example/pixel.png',
          description: 'Hotel front',
        },
      ],
      dataModel: {},
    }),
  );
  expect(container.querySelector('img')).toBeNull();
  expect(screen.getByText('Hotel front')).toBeVisible();
  expect(screen.getByText('tracker.example')).toBeVisible();
});

it('says so when a surface can’t be read', () => {
  show({ ...surface(TRIP), surface_json: '{"components":[]}' } as Block);
  expect(screen.getByText('This interface can’t be shown.')).toBeVisible();
});

it('evaluates the basic catalog’s functions', () => {
  const model = { amount: 1234.5, count: 1, when: '2026-01-16T14:30:00' };
  expect(
    evaluate(
      {
        '@call': 'formatCurrency',
        args: { value: { '@path': '/amount' }, currency: 'GBP' },
      },
      model,
      undefined,
    ),
  ).toBe('£1,234.50');
  expect(
    evaluate(
      {
        '@call': 'pluralize',
        args: { value: { '@path': '/count' }, one: 'night', other: 'nights' },
      },
      model,
      undefined,
    ),
  ).toBe('night');
  expect(formatDate('2026-01-16T14:30:00', 'EEEE, d MMMM')).toBe(
    'Friday, 16 January',
  );
  expect(formatDate('2026-01-16T14:30:00', 'h:mm a')).toBe('2:30 PM');
  expect(
    evaluate(
      {
        '@call': 'formatString',
        args: { value: "On ${formatDate(value:${/when}, format:'MMM d')}" },
      },
      model,
      undefined,
    ),
  ).toBe('On Jan 16');
  // A pattern that could backtrack forever is refused, not run.
  expect(
    evaluate(
      { '@call': 'regex', args: { value: 'aaaa', pattern: '(a+)+$' } },
      model,
      undefined,
    ),
  ).toEqual({ valid: false });
  expect(evaluate({ '@call': 'fetch', args: {} }, model, undefined)).toBeNull();
});

it('writes a template item’s relative path into its own row', () => {
  const model = { days: [{ name: 'A' }, { name: 'B' }] };
  expect(writePath(model, 'name', 'C', { base: '/days/1', index: 1 })).toEqual({
    days: [{ name: 'A' }, { name: 'C' }],
  });
  expect(model.days[1].name).toBe('B');
});
