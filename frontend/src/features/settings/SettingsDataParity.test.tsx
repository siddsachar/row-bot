import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type {
  KnowledgeSettingsSnapshot,
  WikiSettingsSnapshot,
} from '../../api/types';
import WikiSettings, {
  WikiSettingsSession,
  type WikiSettingsIO,
} from '../knowledge/WikiSettings';
import ChannelSettings, {
  createChannelSettingsSession,
  type ChannelPage,
} from './ChannelSettings';
import KnowledgeCatalog from './KnowledgeCatalog';

const revision = 'a'.repeat(64);

it('presents saved graph totals and an owner-sized initial knowledge catalog', async () => {
  const snapshot: KnowledgeSettingsSnapshot = {
    availability: 'available',
    memory_available: true,
    memory_enabled: true,
    entities: 599,
    relations: 956,
    entity_types: [
      { kind: 'fact', count: 411 },
      { kind: 'person', count: 23 },
    ],
    connected_components: 60,
    largest_component: 538,
    isolated_entities: 57,
    status_counts: {
      active: 597,
      needs_review: 0,
      superseded: 0,
      archived: 2,
    },
  };
  const view = render(
    <KnowledgeCatalog
      snapshot={snapshot}
      load={async () => ({
        schema_version: 1,
        revision: 'saved-knowledge',
        availability: 'available',
        total: 30,
        next_cursor: null,
        items: Array.from({ length: 30 }, (_, index) => ({
          id: `knowledge-${index}`,
          entity_type: index % 2 ? 'person' : 'fact',
          subject: `Knowledge ${index}`,
          description: 'Saved description',
          updated_at: '2026-09-14',
          truncated: false,
          saved_state: 'saved' as const,
          semantic_state: 'unknown' as const,
        })),
      })}
    />,
  );

  await screen.findByLabelText('Knowledge 0 · fact');
  expect(
    view.container.querySelectorAll('.settings-knowledge-result'),
  ).toHaveLength(25);
  expect(screen.getByText('Showing 25 of 30')).toBeVisible();
  expect(screen.getByRole('combobox')).toHaveValue('');
  expect(screen.getByRole('option', { name: 'All categories' })).toBeVisible();
  expect(screen.getByRole('option', { name: 'person' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Load more knowledge' }));
  expect(
    view.container.querySelectorAll('.settings-knowledge-result'),
  ).toHaveLength(30);
  expect(screen.getByText('Showing 30 of 30')).toBeVisible();
  expect(screen.getByText('599 entities')).toBeVisible();
  expect(screen.getByText('956 relations')).toBeVisible();
  expect(screen.getByText('Types: fact: 411, person: 23')).toBeVisible();
  expect(screen.getByText('538 entities')).toBeVisible();
  expect(screen.getByText('597 active')).toBeVisible();
  expect(screen.getByText('2 archived')).toBeVisible();
  expect(
    screen.getByRole('link', { name: 'Open Wiki settings' }),
  ).toHaveAttribute('href', '/settings/wiki');
  expect(screen.getByText('Recent recall decisions')).toBeVisible();
  expect(screen.getByText('Memory change log')).toBeVisible();
  expect(
    screen.getByRole('button', { name: 'Delete all knowledge' }),
  ).toBeDisabled();
  const graph = screen.getByRole('heading', { name: 'Memory graph' });
  const catalog = screen.getByRole('heading', { name: 'Stored Knowledge' });
  expect(
    graph.compareDocumentPosition(catalog) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

it('shows the saved wiki vault and counts without authorizing or mutating it', async () => {
  const io: WikiSettingsIO = {
    status: vi.fn(async () => ({
      schema_version: 1,
      revision,
      enabled: true,
      availability: 'scope_required' as const,
      scope_id: null,
      articles: null,
      edited: null,
      conflicts: null,
    })),
    articles: vi.fn(async () => {
      throw new Error('an unauthorized vault must not be read');
    }),
    article: vi.fn(async () => {
      throw new Error('not called');
    }),
    review: vi.fn(async () => {
      throw new Error('not called');
    }),
    execute: vi.fn(async () => {
      throw new Error('not called');
    }),
    receipt: vi.fn(async () => null),
  };
  const snapshot: WikiSettingsSnapshot = {
    availability: 'available',
    enabled: true,
    vault_path: 'C:\\Synthetic\\wiki-vault',
    path_state: 'available',
    articles: 601,
    conversations: 14,
  };
  render(
    <WikiSettings session={new WikiSettingsSession(io)} snapshot={snapshot} />,
  );

  expect(screen.getByText('C:\\Synthetic\\wiki-vault')).toBeVisible();
  expect(screen.getByText('601')).toBeVisible();
  expect(screen.getByText('14')).toBeVisible();
  expect(
    await screen.findByText(
      'Sync status: Select an authorized vault to read or change its files.',
    ),
  ).toBeVisible();
  expect(io.status).toHaveBeenCalledTimes(1);
  expect(io.articles).not.toHaveBeenCalled();
  expect(io.review).not.toHaveBeenCalled();
  expect(io.execute).not.toHaveBeenCalled();
  expect(
    screen.getByRole('button', { name: 'Check vault sync' }),
  ).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Review rebuild' })).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Open vault folder' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('link', { name: 'Browse or create knowledge' }),
  ).toHaveAttribute('href', '/settings/knowledge');
});

it('renders five unloaded core channels as compact passive disclosures', async () => {
  const names = ['Telegram', 'Slack', 'SMS', 'Discord', 'WhatsApp'];
  const page: ChannelPage = {
    schema_version: 1,
    total: names.length,
    truncated: false,
    items: [...names].reverse().map((displayName) => ({
      schema_version: 1,
      channel_id: displayName.toLowerCase(),
      display_name: displayName,
      source: { kind: 'core', label: 'Bundled channel' },
      revision,
      configured: displayName === 'Telegram',
      running: false,
      activity: 'unknown',
      activity_history: [],
      fields: [
        {
          key: 'credential',
          label: 'Credential',
          field_type: 'password',
          storage: 'env',
          help_text: '',
          configured: displayName === 'Telegram',
          source: displayName === 'Telegram' ? 'channel keyring' : '',
          fingerprint: displayName === 'Telegram' ? 'fp:masked' : '',
          externally_managed: false,
          writable: false,
        },
      ],
      paired_identities: [],
      capabilities: ['streaming'],
      availability: {
        configuration: 'limited',
        lifecycle: 'configuration_required',
        pairing: 'unsupported',
        monitor: 'unavailable',
      },
    })),
  };
  const load = vi.fn(async () => page);
  const review = vi.fn(async () => {
    throw new Error('not called');
  });
  const execute = vi.fn(async () => {
    throw new Error('not called');
  });
  const { container } = render(
    <ChannelSettings
      session={createChannelSettingsSession()}
      load={load}
      review={review}
      execute={execute}
    />,
  );

  await screen.findByText('1 configured');
  expect(screen.getByText('1 configured')).toBeVisible();
  expect(screen.getByText('0 running')).toBeVisible();
  expect(container.querySelectorAll('details')).toHaveLength(5);
  expect(container.querySelectorAll('details[open]')).toHaveLength(0);
  expect(
    container.querySelectorAll('.settings-disclosure-chevron'),
  ).toHaveLength(5);
  expect(container.querySelector('input[type="password"]')).toBeNull();
  expect(
    Array.from(container.querySelectorAll('summary strong')).map(
      (node) => node.textContent,
    ),
  ).toEqual(names);

  fireEvent.click(screen.getByText('Telegram').closest('summary')!);
  expect(
    screen.getByText('Saved via channel keyring (fp:masked)'),
  ).toBeVisible();
  expect(review).not.toHaveBeenCalled();
  expect(execute).not.toHaveBeenCalled();
});
