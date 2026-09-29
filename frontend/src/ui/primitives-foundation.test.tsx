import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, it, vi } from 'vitest';
import { Drawer } from './overlays';
import {
  Button,
  Combobox,
  Disclosure,
  EntityList,
  EntityRow,
  IconButton,
  InlineEmpty,
  Kbd,
  Segmented,
  SettingRow,
  Stat,
  StatGroup,
  StatusDot,
  Toolbar,
  ToolbarSeparator,
  type ComboboxOption,
} from './primitives';

it('names icon actions, exposes the shortcut, and shows label plus keycaps on keyboard focus', async () => {
  const user = userEvent.setup();
  const search = vi.fn();
  render(
    <IconButton label="Search conversations" shortcut="Mod+K" onClick={search}>
      <svg aria-hidden />
    </IconButton>,
  );
  const button = screen.getByRole('button', { name: 'Search conversations' });
  expect(button).toHaveClass('icon-action', 'icon-action-md', 'ghost');
  expect(button.getAttribute('aria-keyshortcuts')).toMatch(
    /^(Meta|Control)\+K$/,
  );
  await user.tab();
  const tooltip = await screen.findByRole('tooltip');
  expect(tooltip).toHaveTextContent('Search conversations');
  expect(tooltip).toHaveTextContent('K');
  await user.keyboard('{Enter}');
  expect(search).toHaveBeenCalledTimes(1);
});

it('formats keycaps per platform', () => {
  const { container, rerender } = render(
    <Kbd keys="Mod+Shift+K" platform="mac" />,
  );
  expect(
    [...container.querySelectorAll('kbd kbd')].map((key) => key.textContent),
  ).toEqual(['⌘', '⇧', 'K']);
  rerender(<Kbd keys="Mod+Enter" platform="other" />);
  expect(
    [...container.querySelectorAll('kbd kbd')].map((key) => key.textContent),
  ).toEqual(['Ctrl', '↵']);
});

it('keeps status text available when only the dot is drawn', () => {
  render(
    <>
      <StatusDot tone="success" label="Connected" />
      <StatusDot tone="danger" label="Needs attention" showLabel />
    </>,
  );
  expect(screen.getByText('Connected')).toHaveClass('visually-hidden');
  expect(screen.getByText('Needs attention')).toHaveClass(
    'status-indicator-label',
  );
  expect(
    screen.getByText('Needs attention').closest('.status-indicator'),
  ).toHaveAttribute('data-tone', 'danger');
});

function SegmentedHarness() {
  const [value, setValue] = useState<'all' | 'chats' | 'code' | 'designs'>(
    'all',
  );
  return (
    <Segmented
      label="Conversation type"
      value={value}
      onChange={setValue}
      options={[
        { value: 'all', label: 'All' },
        { value: 'chats', label: 'Chats', icon: <svg />, hideLabel: true },
        { value: 'designs', label: 'Designs', disabled: true },
        { value: 'code', label: 'Code' },
      ]}
    />
  );
}

it('behaves as a single-stop radio group with arrow keys that skip disabled options', async () => {
  const user = userEvent.setup();
  render(<SegmentedHarness />);
  const group = screen.getByRole('radiogroup', { name: 'Conversation type' });
  const radios = within(group).getAllByRole('radio');
  expect(radios.map((radio) => radio.tabIndex)).toEqual([0, -1, -1, -1]);
  expect(screen.getByRole('radio', { name: 'Chats' })).toBeInTheDocument();
  await user.tab();
  expect(screen.getByRole('radio', { name: 'All' })).toHaveFocus();
  await user.keyboard('{ArrowRight}');
  expect(screen.getByRole('radio', { name: 'Chats' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
  await user.keyboard('{ArrowRight}');
  expect(screen.getByRole('radio', { name: 'Code' })).toHaveFocus();
  expect(screen.getByRole('radio', { name: 'Code' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
  await user.keyboard('{Home}');
  expect(screen.getByRole('radio', { name: 'All' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
  await user.click(screen.getByRole('radio', { name: 'Chats' }));
  expect(screen.getByRole('radio', { name: 'Chats' })).toHaveAttribute(
    'aria-checked',
    'true',
  );
});

it('renders an Advanced disclosure with plain summary text and optional meta', () => {
  const toggle = vi.fn();
  const view = render(
    <Disclosure summary="Advanced" meta="3 settings" onOpenChange={toggle}>
      <p>Hidden detail</p>
    </Disclosure>,
  );
  const summary = screen.getByText('Advanced', { selector: 'summary' });
  expect(summary.querySelector('.disclosure-chevron')).not.toBeNull();
  expect(screen.getByText('3 settings')).toHaveClass('disclosure-meta');
  const details = view.container.querySelector('details')!;
  expect(details.open).toBe(false);
  view.rerender(
    <Disclosure summary="Advanced" open onOpenChange={toggle}>
      <p>Hidden detail</p>
    </Disclosure>,
  );
  expect(details.open).toBe(true);
});

it('opens a disclosure when defaultOpen turns true but never closes it under the person (B168)', () => {
  const view = render(
    <Disclosure summary="Sandbox changes" defaultOpen>
      <p role="status">Sandbox changes imported.</p>
    </Disclosure>,
  );
  const details = view.container.querySelector('details')!;
  expect(details.open).toBe(true);
  // What it holds finished (nothing waiting any more): the confirmation stays in view.
  view.rerender(
    <Disclosure summary="Sandbox changes" defaultOpen={false}>
      <p role="status">Sandbox changes imported.</p>
    </Disclosure>,
  );
  expect(details.open).toBe(true);
  details.open = false;
  view.rerender(
    <Disclosure summary="Sandbox changes" defaultOpen={false}>
      <p role="status">Sandbox changes imported.</p>
    </Disclosure>,
  );
  expect(details.open).toBe(false);
  // Something new is waiting: it opens again.
  view.rerender(
    <Disclosure summary="Sandbox changes" defaultOpen>
      <p role="status">Sandbox changes imported.</p>
    </Disclosure>,
  );
  expect(details.open).toBe(true);
});

it('labels a setting row group and ties its visible label to a native control', () => {
  render(
    <SettingRow
      label="Send on Enter"
      description="Shift+Enter adds a new line."
      htmlFor="send-on-enter"
      modified
    >
      <input id="send-on-enter" type="checkbox" />
    </SettingRow>,
  );
  const group = screen.getByRole('group', { name: 'Send on Enter' });
  expect(group).toHaveAccessibleDescription('Shift+Enter adds a new line.');
  expect(
    screen.getByRole('checkbox', { name: 'Send on Enter' }),
  ).toBeInTheDocument();
  expect(within(group).getByText('Modified')).toHaveClass('visually-hidden');
});

it('shows one entity row with status words, a named overflow menu and inline details', async () => {
  const user = userEvent.setup();
  const configure = vi.fn();
  render(
    <EntityList label="Providers">
      <EntityRow
        title="OpenAI"
        icon={<svg />}
        status={{ tone: 'success', label: 'Connected' }}
        meta="API key · 12 models"
        action={<Button onClick={configure}>Configure</Button>}
        menu={[{ label: 'Disconnect', danger: true, onSelect: vi.fn() }]}
        details={<p>Key saved · ····c99</p>}
      />
    </EntityList>,
  );
  const list = screen.getByRole('list', { name: 'Providers' });
  const row = within(list).getByRole('listitem');
  expect(row).toHaveTextContent('OpenAI');
  expect(row).toHaveTextContent('Connected');
  expect(row).toHaveTextContent('API key · 12 models');
  expect(
    within(row).getByRole('button', { name: 'More actions for OpenAI' }),
  ).toBeInTheDocument();
  const expand = within(row).getByRole('button', {
    name: 'Show details for OpenAI',
  });
  expect(expand).toHaveAttribute('aria-expanded', 'false');
  expect(screen.getByText('Key saved · ····c99')).not.toBeVisible();
  await user.click(expand);
  expect(
    within(row).getByRole('button', { name: 'Hide details for OpenAI' }),
  ).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByText('Key saved · ····c99')).toBeVisible();
  await user.click(within(row).getByRole('button', { name: 'Configure' }));
  expect(configure).toHaveBeenCalledTimes(1);
});

it('renders stats as a definition list and an inline empty state with its action', () => {
  render(
    <>
      <StatGroup label="Knowledge totals">
        <Stat label="Memories" value="658" delta="+12" tone="success" />
        <Stat label="Links" value="1,016" />
      </StatGroup>
      <InlineEmpty action={<Button>Add resource</Button>}>
        Nothing attached yet.
      </InlineEmpty>
    </>,
  );
  const stats = screen.getByText('Memories').closest('dl')!;
  expect(stats).toHaveAttribute('aria-label', 'Knowledge totals');
  expect(within(stats).getByText('Memories').tagName).toBe('DT');
  expect(within(stats).getByText('658').tagName).toBe('DD');
  expect(within(stats).getByText('+12')).toHaveAttribute(
    'data-tone',
    'success',
  );
  expect(screen.getByText('Nothing attached yet.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Add resource' })).toBeVisible();
});

it('moves focus between toolbar controls with arrow keys, Home and End', async () => {
  const user = userEvent.setup();
  render(
    <Toolbar label="Canvas controls" floating placement="bottom-center">
      <IconButton label="Zoom out" tooltip={false}>
        <svg />
      </IconButton>
      <IconButton label="Zoom in" tooltip={false}>
        <svg />
      </IconButton>
      <ToolbarSeparator />
      <IconButton label="Fit to screen" tooltip={false}>
        <svg />
      </IconButton>
    </Toolbar>,
  );
  const toolbar = screen.getByRole('toolbar', { name: 'Canvas controls' });
  expect(toolbar).toHaveAttribute('data-placement', 'bottom-center');
  await user.tab();
  expect(screen.getByRole('button', { name: 'Zoom out' })).toHaveFocus();
  await user.keyboard('{ArrowRight}');
  expect(screen.getByRole('button', { name: 'Zoom in' })).toHaveFocus();
  await user.keyboard('{End}');
  expect(screen.getByRole('button', { name: 'Fit to screen' })).toHaveFocus();
  await user.keyboard('{ArrowRight}');
  expect(screen.getByRole('button', { name: 'Zoom out' })).toHaveFocus();
  await user.keyboard('{ArrowLeft}');
  expect(screen.getByRole('button', { name: 'Fit to screen' })).toHaveFocus();
});

const models: ComboboxOption[] = [
  { value: 'openai:gpt', label: 'GPT-5.6 Sol', group: 'OpenAI' },
  {
    value: 'openai:mini',
    label: 'GPT-5.6 Mini',
    group: 'OpenAI',
    disabled: true,
  },
  {
    value: 'local:qwen',
    label: 'Qwen 3.8 27B',
    group: 'Ollama',
    keywords: ['local', 'private'],
  },
];

function ComboboxHarness({ onChange }: { onChange: (value: string) => void }) {
  const [value, setValue] = useState('openai:gpt');
  return (
    <Combobox
      label="Model"
      value={value}
      options={models}
      onChange={(next) => {
        setValue(next);
        onChange(next);
      }}
    />
  );
}

it('searches a large set with a combobox, skips disabled choices and returns focus on choose', async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  render(<ComboboxHarness onChange={onChange} />);
  const trigger = screen.getByRole('button', { name: 'Model' });
  expect(trigger).toHaveAccessibleDescription('GPT-5.6 Sol');
  await user.click(trigger);
  const input = await screen.findByRole('combobox', { name: 'Search model' });
  expect(input).toHaveFocus();
  const listbox = screen.getByRole('listbox', { name: 'Model' });
  expect(
    within(listbox).getByRole('group', { name: 'OpenAI' }),
  ).toBeInTheDocument();
  expect(
    within(listbox).getByRole('option', { name: /GPT-5.6 Sol/ }),
  ).toHaveAttribute('aria-selected', 'true');
  await user.keyboard('{ArrowDown}');
  // The disabled Mini option is skipped.
  const qwen = within(listbox).getByRole('option', { name: /Qwen 3.8 27B/ });
  expect(qwen).toHaveAttribute('aria-selected', 'true');
  expect(input).toHaveAttribute('aria-activedescendant', qwen.id);
  await user.type(input, 'private');
  expect(within(listbox).getAllByRole('option')).toHaveLength(1);
  await user.keyboard('{Enter}');
  expect(onChange).toHaveBeenCalledWith('local:qwen');
  expect(screen.queryByRole('listbox')).toBeNull();
  expect(trigger).toHaveFocus();
  expect(trigger).toHaveAccessibleDescription('Qwen 3.8 27B');
  await user.click(trigger);
  await user.type(
    await screen.findByRole('combobox', { name: 'Search model' }),
    'nothing like this',
  );
  expect(screen.getByRole('status')).toHaveTextContent('No matches');
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('listbox')).toBeNull();
});

it('opens a non-modal inspector drawer that focuses its heading and closes on Escape', async () => {
  const user = userEvent.setup();
  function Harness() {
    const [open, setOpen] = useState(true);
    return (
      <>
        <Button>Canvas node</Button>
        <Drawer
          open={open}
          onOpenChange={setOpen}
          title="Alpha project"
          description="Fact · 3 connections"
        >
          <p>Inspector body</p>
        </Drawer>
      </>
    );
  }
  render(<Harness />);
  const drawer = await screen.findByRole('dialog', { name: 'Alpha project' });
  expect(drawer).toHaveAccessibleDescription('Fact · 3 connections');
  expect(
    within(drawer).getByRole('heading', { name: 'Alpha project' }),
  ).toHaveFocus();
  expect(
    within(drawer).getByRole('button', { name: 'Close Alpha project' }),
  ).toHaveAttribute('aria-keyshortcuts', 'Escape');
  // Non-modal: the rest of the page stays interactive.
  await user.click(screen.getByRole('button', { name: 'Canvas node' }));
  expect(screen.getByRole('dialog', { name: 'Alpha project' })).toBeVisible();
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('dialog')).toBeNull();
});
