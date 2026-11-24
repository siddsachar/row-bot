import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it } from 'vitest';
import ContextUsage, { describeConversationUsage } from './ContextUsage';

it('shows no ring before the first measurement, so nothing looks like it is loading', () => {
  const { container, rerender } = render(<ContextUsage />);
  expect(container).toBeEmptyDOMElement();
  rerender(
    <ContextUsage
      usage={{
        conversation_id: 'a',
        state: 'unknown',
        freshness: 'unknown',
        status: 'unavailable',
        estimated_input_tokens: null,
        usable_input_tokens: null,
        compact_at_tokens: null,
        native_window_tokens: null,
        effective_limit_tokens: null,
        model_ref: null,
        last_confirmed_input_tokens: null,
        scope: 'unknown',
        capacity_state: 'unknown',
      }}
    />,
  );
  expect(container).toBeEmptyDOMElement();
  // Measured and empty: the track alone, no lone dot of an arc.
  rerender(
    <ContextUsage
      usage={{
        conversation_id: 'a',
        state: 'live',
        freshness: 'current',
        status: 'ready',
        estimated_input_tokens: 0,
        usable_input_tokens: 100000,
        compact_at_tokens: null,
        native_window_tokens: 128000,
        effective_limit_tokens: 100000,
        model_ref: 'fake/model',
        last_confirmed_input_tokens: null,
        scope: 'agent',
        capacity_state: 'ready',
      }}
    />,
  );
  expect(screen.getByText('Context 0%')).toBeInTheDocument();
  expect(container.querySelector('.context-ring-fill')).toBeNull();
});

it('labels stale usage without claiming a current measurement', () => {
  const { rerender } = render(<ContextUsage />);
  rerender(
    <ContextUsage
      usage={{
        conversation_id: 'a',
        state: 'stale',
        freshness: 'stale',
        status: 'ready',
        estimated_input_tokens: 12345,
        usable_input_tokens: 100000,
        compact_at_tokens: 75000,
        native_window_tokens: 128000,
        effective_limit_tokens: 100000,
        model_ref: 'fake/model',
        last_confirmed_input_tokens: 0,
        scope: 'agent',
        capacity_state: 'ready',
      }}
    />,
  );
  expect(screen.getByText('Context ~12%')).toBeInTheDocument();
  expect(screen.getByRole('meter', { name: 'Context usage' })).toHaveAttribute(
    'aria-valuenow',
    '12345',
  );
  // The ring marks the compaction threshold (75%) with a tick.
  expect(document.querySelector('.context-ring-threshold')).not.toBeNull();
  expect(
    screen.getByRole('button', {
      name: 'Context ~12%; automatic compaction threshold marker',
    }),
  ).toBeVisible();
  fireEvent.click(screen.getByText('Context ~12%'));
  expect(
    screen.getByText('Last provider-confirmed input: 0 tokens.'),
  ).toBeInTheDocument();
});

it('shows compaction and failure states without pretending capacity exists', () => {
  const usage = {
    conversation_id: 'a',
    state: 'live' as const,
    freshness: 'current' as const,
    status: 'compacting' as const,
    estimated_input_tokens: 80,
    usable_input_tokens: 100,
    compact_at_tokens: 75,
    native_window_tokens: 128,
    effective_limit_tokens: 100,
    model_ref: 'fake/model',
    last_confirmed_input_tokens: null,
    scope: 'chat_only' as const,
    capacity_state: 'ready',
  };
  const { rerender } = render(<ContextUsage usage={usage} />);
  expect(screen.getByText('Compacting context…')).toBeInTheDocument();
  rerender(<ContextUsage usage={{ ...usage, status: 'failed' as const }} />);
  expect(
    screen.getByText('Context 80% · compaction failed'),
  ).toBeInTheDocument();
});

const measured = {
  conversation_id: 'a',
  state: 'live' as const,
  freshness: 'current' as const,
  status: 'ready' as const,
  estimated_input_tokens: 12000,
  usable_input_tokens: 100000,
  compact_at_tokens: 75000,
  native_window_tokens: 128000,
  effective_limit_tokens: 100000,
  model_ref: 'openrouter/anthropic/claude-sonnet-4.5',
  last_confirmed_input_tokens: null,
  scope: 'agent' as const,
  capacity_state: 'ready',
};

it('shows the tokens this conversation used, the share read from cache and a reported cost', () => {
  render(
    <ContextUsage
      usage={{
        ...measured,
        conversation_usage: {
          calls: 9,
          input_tokens: 1_200_000,
          output_tokens: 34_567,
          cache_read_percent: 82,
          cost_usd: 0.4213,
        },
      }}
    />,
  );
  fireEvent.click(screen.getByText('Context 12%'));
  expect(
    screen.getByText(
      'This conversation: 1,234,567 tokens used, 82% of input read from cache. Cost reported by the provider: $0.42.',
    ),
  ).toBeInTheDocument();
  expect(
    describeConversationUsage({
      calls: 9,
      input_tokens: 1_200_000,
      output_tokens: 34_567,
      cache_read_percent: 82,
      cost_usd: 0.4213,
    })?.hint,
  ).toBe('1.2M tokens used, 82% from cache');
});

it('leaves out what the provider did not report, and says nothing before the first call', () => {
  const { rerender } = render(
    <ContextUsage
      usage={{
        ...measured,
        conversation_usage: {
          calls: 2,
          input_tokens: 900,
          output_tokens: 100,
          cache_read_percent: null,
          cost_usd: null,
        },
      }}
    />,
  );
  fireEvent.click(screen.getByText('Context 12%'));
  expect(
    screen.getByText('This conversation: 1,000 tokens used.'),
  ).toBeInTheDocument();
  expect(screen.queryByText(/from cache|Cost reported/)).toBeNull();
  rerender(<ContextUsage usage={measured} />);
  expect(screen.queryByText(/This conversation:/)).toBeNull();
  expect(describeConversationUsage(null)).toBeNull();
  expect(
    describeConversationUsage({
      calls: 1,
      input_tokens: 10,
      output_tokens: 1,
      cache_read_percent: 0,
      cost_usd: 0.0042,
    }),
  ).toEqual({
    hint: '11 tokens used, 0% from cache',
    detail:
      'This conversation: 11 tokens used, 0% of input read from cache. Cost reported by the provider: $0.0042.',
    cache: null,
  });
});

const breakdown = {
  instructions: 1200,
  tools: 6000,
  skills: 800,
  memory: 0,
  conversation: 4000,
  tool_count: 85,
};

it('shows what fills the context, one row a part, leaving out empty parts', () => {
  render(<ContextUsage usage={{ ...measured, breakdown }} />);
  fireEvent.click(screen.getByText('Context 12%'));

  const rows = screen
    .getAllByRole('listitem')
    .map((row) => row.textContent?.replace(/\s+/g, ' ').trim());
  expect(rows).toEqual([
    "Row-Bot's instructions1,20010%",
    'Tools (85)6,00050%',
    'Skills and guides8007%',
    'Conversation4,00033%',
  ]);
  expect(screen.queryByText('Memories')).toBeNull();
  expect(
    screen.getByRole('img', {
      name: "What fills the context: Row-Bot's instructions 10%, Tools (85) 50%, Skills and guides 7%, Conversation 33%",
    }),
  ).toBeInTheDocument();
  // The parts add up to the ring's number: no "as of" note.
  expect(screen.queryByText('As of the last reply')).toBeNull();
});

it('marks the parts as of the last reply once the live count has moved on', () => {
  render(
    <ContextUsage
      usage={{
        ...measured,
        estimated_input_tokens: 13500,
        breakdown: { ...breakdown, tools: 0, tool_count: 0, memory: 3 },
      }}
    />,
  );
  fireEvent.click(screen.getByText('Context 14%'));
  expect(screen.getByText('As of the last reply')).toBeInTheDocument();
  // Chat Only: no tools row; a tiny part still shows its share.
  expect(screen.queryByText(/^Tools/)).toBeNull();
  expect(screen.getByText('Memories').closest('li')).toHaveTextContent(
    'Memories3<1%',
  );
});

it('shows no breakdown section for an older measurement without one', () => {
  render(<ContextUsage usage={measured} />);
  fireEvent.click(screen.getByText('Context 12%'));
  expect(screen.queryByRole('img')).toBeNull();
  expect(screen.queryByRole('list')).toBeNull();
});

it('says quietly when a summary of older messages is ready, and only then', () => {
  const { rerender } = render(
    <ContextUsage usage={{ ...measured, summary_ready: true }} />,
  );
  fireEvent.click(screen.getByText('Context 12%'));
  expect(
    screen.getByText('A summary of older messages is ready.'),
  ).toBeInTheDocument();
  rerender(<ContextUsage usage={measured} />);
  expect(screen.queryByText(/A summary of older messages/)).toBeNull();
});

it("gives the last request's cache share its own line when the provider reports it", () => {
  const { rerender } = render(
    <ContextUsage
      usage={{
        ...measured,
        conversation_usage: {
          calls: 3,
          input_tokens: 30000,
          output_tokens: 600,
          cache_read_percent: 81,
          cost_usd: null,
          last_cache_read_percent: 92,
        },
      }}
    />,
  );
  fireEvent.click(screen.getByText('Context 12%'));
  expect(
    screen.getByText(
      "Cache: 92% of the last request came from the provider's cache (81% over this conversation).",
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText('This conversation: 30,600 tokens used.'),
  ).toBeInTheDocument();
  // A provider without cache numbers (a local model): nothing about a cache.
  rerender(
    <ContextUsage
      usage={{
        ...measured,
        conversation_usage: {
          calls: 3,
          input_tokens: 30000,
          output_tokens: 600,
          cache_read_percent: null,
          cost_usd: null,
          last_cache_read_percent: null,
        },
      }}
    />,
  );
  expect(screen.queryByText(/cache/i)).toBeNull();
});

it('keeps the technical lines behind Details', () => {
  render(
    <ContextUsage
      usage={{ ...measured, breakdown, last_confirmed_input_tokens: 11900 }}
    />,
  );
  fireEvent.click(screen.getByText('Context 12%'));
  const details = screen.getByText('Details').closest('details')!;
  expect(details.open).toBe(false);
  expect(details).toHaveTextContent('Model window: 100,000 tokens.');
  expect(details).toHaveTextContent(
    'Last provider-confirmed input: 11,900 tokens.',
  );
  expect(details).toHaveTextContent('Capacity source state: ready.');
  fireEvent.click(screen.getByText('Details'));
  expect(details.open).toBe(true);
  // The short summary stays outside.
  expect(
    screen
      .getByText(
        'Approximately 12,000 of 100,000 usable tokens. Automatic compaction starts around 75,000 tokens.',
      )
      .closest('details'),
  ).toBeNull();
});

it('says when a local model’s window limits the chat, with the way to its settings', () => {
  const local = {
    ...measured,
    estimated_input_tokens: 15_000,
    usable_input_tokens: 27_852,
    effective_limit_tokens: 32_768,
    model_ref: 'model:ollama:qwen3:8b',
  };
  const { rerender } = render(
    <MemoryRouter>
      <ContextUsage usage={local} />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByText('Context 54%'));

  expect(screen.getByText(/This model reads 32K\./)).toBeInTheDocument();
  expect(
    screen.getByRole('link', { name: 'Give it more in its settings' }),
  ).toHaveAttribute(
    'href',
    '/settings/models?model-settings=model%3Aollama%3Aqwen3%3A8b',
  );

  // A server the person runs sets its own window: no link that would change nothing.
  rerender(
    <MemoryRouter>
      <ContextUsage
        usage={{
          ...local,
          model_ref: 'model:custom_openai_lmstudio-1234:gemma-4',
        }}
      />
    </MemoryRouter>,
  );
  expect(screen.queryByText(/This model reads/)).toBeNull();
});
