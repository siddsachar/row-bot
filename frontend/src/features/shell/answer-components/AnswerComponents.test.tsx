import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { TranscriptRow } from '../../../api/types';
import SafeMarkdown from '../chat-parity-markdown';
import { registerPromptSender } from '../composer-bridge';
import { publicBlockText, TranscriptBlocks } from '../TranscriptBlocks';

type Block = TranscriptRow['blocks'][number];

let unregister: (() => void) | undefined;
afterEach(() => unregister?.());

function composer(accepts = true) {
  const sent = vi.fn((_text: string) => accepts);
  unregister = registerPromptSender('conversation-a', sent);
  return sent;
}

function show(block: Block, conversationId = 'conversation-a') {
  return render(
    <TranscriptBlocks blocks={[block]} conversationId={conversationId} />,
  );
}

const CHOICES: Block = {
  id: 'block:choices',
  type: 'choices',
  prompt: 'Which laptop?',
  options: [
    { label: 'Air', reply: 'Tell me more about the Air' },
    { label: 'Pro', reply: 'Pro' },
  ],
  text: 'Which laptop?\n1. Air\n2. Pro',
};

it('shows a table component with its title, sortable headers and note', () => {
  show({
    id: 'block:table',
    type: 'table',
    title: 'Laptops',
    columns: [
      { key: 'name', label: 'Model', type: 'text' },
      { key: 'price', label: 'Price', type: 'currency' },
    ],
    rows: [
      ['Pro', '£1,599'],
      ['Air', '£999'],
    ],
    note: 'Prices from the shop.',
    actions: [],
    text: '',
  });
  const region = screen.getByRole('region', { name: 'Laptops' });
  expect(screen.getByRole('heading', { name: 'Laptops' })).toBeVisible();
  fireEvent.click(within(region).getByRole('button', { name: 'Price' }));
  const cells = within(region)
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell')[1].textContent);
  expect(cells).toEqual(['£999', '£1,599']);
  expect(screen.getByText('Prices from the shop.')).toBeVisible();
});

it('counts a checklist’s ticks as the person ticks it', () => {
  show({
    id: 'block:list',
    type: 'checklist',
    title: 'Before Friday',
    items: [
      { id: 'a', text: 'Book the room', done: true },
      { id: 'b', text: 'Send the agenda', done: false },
      { id: 'c', text: 'Order lunch', done: false },
    ],
    actions: [],
    text: '',
  });
  expect(screen.getByText('1 of 3 done')).toBeVisible();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Send the agenda' }));
  expect(screen.getByText('2 of 3 done')).toBeVisible();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Book the room' }));
  expect(screen.getByText('1 of 3 done')).toBeVisible();
});

it('writes figures with their units where people write them, and the change in its tone', () => {
  show({
    id: 'block:figures',
    type: 'figures',
    title: 'October',
    items: [
      { label: 'Spent', value: 2140, unit: '£', delta: '-8%', tone: 'good' },
      { label: 'Share', value: 12.5, unit: '%', delta: '', tone: 'neutral' },
      { label: 'Walked', value: 42, unit: 'km', delta: '', tone: 'neutral' },
      { label: 'Mood', value: 'Calm', unit: '', delta: '+1', tone: 'bad' },
    ],
    actions: [],
    text: '',
  });
  expect(
    screen.getAllByRole('definition').map((value) => value.textContent),
  ).toEqual(['£2,140-8%', '12.5%', '42km', 'Calm+1']);
  expect(screen.getByText('-8%')).toHaveAttribute('data-tone', 'success');
  expect(screen.getByText('+1')).toHaveAttribute('data-tone', 'danger');
});

it('shows progress against each target, and how far over a target is', () => {
  show({
    id: 'block:progress',
    type: 'progress',
    title: 'Training',
    items: [
      { label: 'Running', value: 42, target: 50, unit: 'km' },
      { label: 'Saving', value: 1300, target: 1200, unit: '£' },
    ],
    actions: [],
    text: '',
  });
  const running = screen.getByRole('progressbar', { name: 'Running' });
  expect(running).toHaveAttribute('aria-valuenow', '84');
  expect(running).toHaveAttribute('aria-valuetext', '42 km of 50 km (84%)');
  expect(screen.getByText('42 km of 50 km (84%)')).toBeVisible();
  expect(screen.getByRole('progressbar', { name: 'Saving' })).toHaveAttribute(
    'aria-valuenow',
    '100',
  );
  expect(screen.getByText('£1,300 · +£100 over')).toBeVisible();
});

it('sends a choice’s words through the composer once, then shows which was picked', () => {
  const sent = composer();
  show(CHOICES);
  fireEvent.click(screen.getByRole('button', { name: 'Air' }));
  expect(sent).toHaveBeenCalledExactlyOnceWith('Tell me more about the Air');
  const air = screen.getByRole('button', { name: /Air/ });
  expect(air).toHaveAttribute('aria-pressed', 'true');
  expect(air).toHaveTextContent('Sent');
  expect(air).toBeDisabled();
  const pro = screen.getByRole('button', { name: 'Pro' });
  expect(pro).toBeDisabled();
  fireEvent.click(pro);
  expect(sent).toHaveBeenCalledOnce();
});

it('keeps the choices open when the composer can’t take a reply now', () => {
  const sent = composer(false);
  show(CHOICES);
  fireEvent.click(screen.getByRole('button', { name: 'Pro' }));
  expect(sent).toHaveBeenCalledExactlyOnceWith('Pro');
  expect(screen.getByRole('button', { name: 'Pro' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Pro' })).not.toHaveTextContent(
    'Sent',
  );
});

it('sends nothing from another conversation’s answer', () => {
  const sent = composer();
  show(CHOICES, 'conversation-b');
  fireEvent.click(screen.getByRole('button', { name: 'Pro' }));
  expect(sent).not.toHaveBeenCalled();
});

it('puts a component’s reply buttons under it, a chart’s too', () => {
  const sent = composer();
  show({
    id: 'block:progress',
    type: 'progress',
    title: '',
    items: [{ label: 'Saving', value: 1, target: 2, unit: '' }],
    actions: [{ label: 'Plan the rest', reply: 'Plan how I save the rest' }],
    text: '',
  });
  fireEvent.click(screen.getByRole('button', { name: 'Plan the rest' }));
  expect(sent).toHaveBeenCalledWith('Plan how I save the rest');
});

it('copies each component as its plain text', () => {
  expect(publicBlockText(CHOICES)).toBe('Which laptop?\n1. Air\n2. Pro');
});

it('shows a component still being written as a placeholder, and a saved bad fence as code', () => {
  const fence = '```rowbot-table\n{"columns": [';
  const { rerender } = render(<SafeMarkdown text={fence} streaming />);
  expect(screen.getByRole('status')).toHaveTextContent('Preparing a table…');
  expect(screen.queryByText(/columns/)).toBeNull();
  rerender(<SafeMarkdown text={`${fence}\n\`\`\``} />);
  expect(screen.queryByText('Preparing a table…')).toBeNull();
  expect(screen.getByText(/columns/)).toBeVisible();
});
