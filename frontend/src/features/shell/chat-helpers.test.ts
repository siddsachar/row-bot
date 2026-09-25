import { describe, expect, it } from 'vitest';
import type {
  EventRecord,
  ModelChoice,
  TranscriptRow,
  TranscriptTraceGroup,
} from '../../api/types';
import { chartLayout, figureTable } from './chart-embed';
import {
  groupModels,
  isLocalProvider,
  matchesModel,
  modelRefName,
  splitModelLabel,
} from './model-choices';
import {
  languageForFile,
  languageLabel,
  resolveLanguage,
} from './syntax-languages';
import {
  activityLabel,
  approvalQuestion,
  formatElapsed,
  imperative,
  keyArgument,
  stepVerb,
  summarizeActivity,
} from './tool-activity';
import { buildTranscript, liveMedia } from './transcript-model';
import { stoppedMarker } from './TranscriptMessage';
import { turnError } from './turn-errors';

function trace(
  name: string,
  status: TranscriptTraceGroup['status'] = 'succeeded',
  extra: Partial<TranscriptTraceGroup['items'][number]> = {},
): TranscriptTraceGroup {
  return {
    group_id: `group-${name}`,
    name,
    kind: 'generic',
    group_order: 0,
    status,
    counts: { [status]: 1 },
    items: [
      {
        item_id: `item-${name}`,
        group_id: `group-${name}`,
        call_id: `call-${name}`,
        call_order: 0,
        group_order: 0,
        canonical_name: name,
        group_name: name,
        group_kind: 'generic',
        status,
        safe_summary: '',
        summary_truncated: false,
        ...extra,
      },
    ],
  };
}

describe('tool activity', () => {
  it('speaks human verbs for running, finished and failed steps', () => {
    expect(stepVerb('web_search', 'pending')).toBe('Searching the web');
    expect(stepVerb('duckduckgo_search', 'succeeded')).toBe('Searched the web');
    // Failed or denied steps never read as done.
    expect(stepVerb('read_file', 'failed')).toBe("Couldn't read a file");
    expect(stepVerb('workspace_file_delete', 'cancelled')).toBe(
      "Didn't delete a file",
    );
    expect(stepVerb('send_gmail_message', 'blocked')).toBe(
      "Didn't send an email",
    );
    expect(stepVerb('frobnicate_widget', 'failed')).toBe(
      'Frobnicate widget failed',
    );
    expect(stepVerb('frobnicate_widget', 'cancelled')).toBe(
      'Frobnicate widget skipped',
    );
    expect(stepVerb('developer_commit_changes', 'succeeded')).toBe(
      'Committed changes',
    );
    expect(stepVerb('get_current_datetime', 'succeeded')).toBe(
      'Checked the time',
    );
    // Unknown tools fall back to sentence case, never snake_case.
    expect(stepVerb('frobnicate_widget', 'succeeded')).toBe(
      'Frobnicate widget',
    );
    expect(stepVerb('frobnicate_widget', 'pending')).toBe(
      'Running frobnicate widget',
    );
    // Live steps may carry a display label with an emoji.
    expect(stepVerb('🖥️ shell', 'pending')).toBe('Running a command');
    expect(stepVerb('🧩 frobnicate', 'succeeded')).toBe('Frobnicate');
    expect(stepVerb('workspace_write_file', 'succeeded')).toBe('Wrote a file');
    expect(stepVerb('workspace_list_directory', 'pending')).toBe(
      'Listing files',
    );
  });

  it('asks approval questions in the imperative', () => {
    expect(imperative('Sending an email')).toBe('Send an email');
    expect(imperative('Running a command')).toBe('Run a command');
    expect(imperative('Committing changes')).toBe('Commit changes');
    expect(imperative('Saving a memory')).toBe('Save a memory');
    expect(imperative('Scrolling')).toBe('Scroll');
    expect(approvalQuestion('send_gmail_message')).toBe('Send an email?');
    expect(approvalQuestion('fixture_action')).toBe('Allow Fixture action?');
  });

  it('shows the one argument that matters, bounded and without markup', () => {
    expect(keyArgument('{"query": "local models", "max": 3}')).toBe(
      '“local models”',
    );
    expect(keyArgument('{"url": "https://www.example.test/docs/page"}')).toBe(
      'example.test/docs/page',
    );
    expect(keyArgument('{"path": "src/app.ts", "limit": 3}')).toBe(
      'src/app.ts',
    );
    expect(keyArgument('{"limit": 3, "offset": 1}')).toBe('');
    expect(keyArgument('plain bounded input')).toBe('plain bounded input');
    expect(keyArgument(`"${'x'.repeat(300)}"`)).toHaveLength(140);
    expect(keyArgument(undefined)).toBe('');
  });

  it('summarises a turn in one line with distinct glyphs and failures', () => {
    const summary = summarizeActivity([
      trace('web_search'),
      trace('read_url'),
      trace('read_file', 'failed'),
    ]);
    expect(activityLabel(summary)).toBe('Used 3 tools · 1 failed');
    expect(
      activityLabel(
        summarizeActivity([
          trace('read_file', 'failed'),
          trace('file_delete', 'cancelled'),
        ]),
      ),
    ).toBe('Used 2 tools · 1 failed · 1 skipped');
    expect(
      activityLabel(summarizeActivity([trace('file_delete', 'cancelled')])),
    ).toBe('1 tool skipped');
    expect(summary.icons).toHaveLength(2);
    expect(summary.current).toBeNull();
    const running = summarizeActivity([trace('web_search', 'pending')]);
    expect(running.pending).toBe(1);
    expect(running.current?.canonical_name).toBe('web_search');
    expect(activityLabel(summarizeActivity([trace('calculate')]))).toBe(
      'Used 1 tool',
    );
  });

  it('formats durations compactly', () => {
    expect(formatElapsed(820)).toBe('0.8s');
    expect(formatElapsed(8400)).toBe('8.4s');
    expect(formatElapsed(42_000)).toBe('42s');
    expect(formatElapsed(72_000)).toBe('1m 12s');
    expect(formatElapsed(-1)).toBe('');
  });
});

describe('transcript model', () => {
  const user = (id: string, text = 'Question'): TranscriptRow => ({
    id,
    message_id: id,
    role: 'user',
    blocks: [{ type: 'text', text }],
  });
  const assistant = (
    id: string,
    text: string,
    traces: TranscriptTraceGroup[] = [],
  ): TranscriptRow => ({
    id,
    message_id: id,
    role: 'assistant',
    blocks: text ? [{ type: 'text', text }] : [],
    traces,
  });

  it('merges tool-only rows into one activity row and hoists media once (B22)', () => {
    const image = trace('generate_image', 'succeeded', {
      safe_input: '{"prompt": "a lighthouse at dusk"}',
      specialization: {
        kind: 'media',
        media_kind: 'image',
        media: [{ media_ref: 'media-a', mime_type: 'image/png' }],
      },
    });
    const items = buildTranscript([
      user('u1'),
      assistant('a1', '', [trace('web_search')]),
      assistant('a2', '', [image]),
      {
        id: 'tool-1',
        role: 'tool',
        trace_parent_id: 'a2',
        blocks: [
          {
            id: 'chart-1',
            type: 'chart',
            figure_json: '{"data":[],"layout":{}}',
            text: 'Chart',
          },
        ],
      },
      assistant('a3', 'Here is the lighthouse.'),
    ]);
    expect(items.map((item) => item.row.id)).toEqual(['u1', 'a1', 'a3']);
    expect(items[1].traces).toHaveLength(2);
    expect(items[1].media).toEqual([
      {
        reference: 'media-a',
        mime: 'image/png',
        caption: 'a lighthouse at dusk',
      },
    ]);
    expect(items[1].embeds).toHaveLength(1);
    expect(items[2].media).toEqual([]);
  });

  it('keeps text rows separate and skips empty assistant rows', () => {
    const items = buildTranscript([
      user('u1'),
      assistant('a1', 'I will check.', [trace('read_file')]),
      assistant('a2', ''),
      assistant('a3', 'Done.'),
    ]);
    expect(items.map((item) => item.row.id)).toEqual(['u1', 'a1', 'a3']);
  });

  it('shows live media only until a settled row renders it', () => {
    const record = (ref: string) =>
      ({
        cursor: '1',
        event: {
          type: 'media.available',
          event_id: ref,
          payload: { media_ref: ref, mime_type: 'image/png' },
        },
      }) as unknown as EventRecord;
    expect(
      liveMedia([record('a'), record('b'), record('b')], new Set(['a'])),
    ).toEqual([{ reference: 'b', mime: 'image/png' }]);
  });

  it('turns the runtime stop marker into a chip label', () => {
    const stopped = stoppedMarker([
      {
        id: 'block-1',
        type: 'markdown',
        text: 'Partial answer.\n\n⏹️ *[Stopped]*',
      },
    ]);
    expect(stopped.label).toBe('Stopped');
    expect(stopped.blocks).toEqual([
      { id: 'block-1', type: 'markdown', text: 'Partial answer.' },
    ]);
    expect(
      stoppedMarker([{ type: 'text', text: '*[Browser task stopped]*' }]),
    ).toEqual({ blocks: [], label: 'Browser task stopped' });
    expect(stoppedMarker([{ type: 'text', text: 'Ordinary.' }]).label).toBe('');
  });
});

describe('runtime error rows', () => {
  it('reads the cause and next steps from the runtime wording', () => {
    expect(
      turnError(
        '⚠️ An error occurred: ⚠️ Billing limit reached — please review your plan at the provider dashboard.',
      ),
    ).toEqual({
      cause: 'Billing limit reached',
      detail: 'Please review your plan at the provider dashboard.',
      actions: ['model', 'providers'],
    });
    expect(
      turnError('⚠️ Rate limit reached — please wait a moment and try again.')
        ?.actions,
    ).toEqual(['retry', 'model']);
    expect(
      turnError(
        '⚠️ qwen via Local does not support tool calling — switch to a compatible model in Settings → Models.',
      )?.actions,
    ).toEqual(['model']);
  });

  it('leaves ordinary answers that mention warnings alone', () => {
    expect(turnError('Billing limit reached for your plan.')).toBeNull();
    expect(
      turnError('⚠️ Heads up: this is a long answer.\n\nWith paragraphs.'),
    ).toBeNull();
    expect(turnError('⚠️ Remember to back up first.')).toBeNull();
  });
});

describe('model choices', () => {
  const model = (
    provider: string,
    ref: string,
    label: string,
    available = true,
  ): ModelChoice => ({
    provider_id: provider,
    model_ref: ref,
    label,
    available,
  });

  it('groups by provider with connected providers first', () => {
    const groups = groupModels([
      model('openrouter', 'or/a', 'Kimi K3 - OpenRouter', false),
      model('ollama', 'ollama/qwen', 'qwen3.8:27b - Ollama Local'),
      model('anthropic', 'a/opus', 'Claude Opus - Anthropic API'),
      model('ollama', 'ollama/llama', 'llama4 - Ollama Local'),
    ]);
    expect(groups.map((group) => [group.label, group.connected])).toEqual([
      ['Ollama Local', true],
      ['Anthropic API', true],
      ['OpenRouter', false],
    ]);
    expect(groups[0].local).toBe(true);
    expect(groups[0].models).toHaveLength(2);
  });

  it('splits labels, names bare references and matches every search term', () => {
    expect(splitModelLabel('Z.ai: GLM 5.2 - OpenRouter')).toEqual({
      name: 'Z.ai: GLM 5.2',
      provider: 'OpenRouter',
    });
    expect(splitModelLabel('solo')).toEqual({ name: 'solo', provider: '' });
    expect(modelRefName('model:ollama:qwen3.8:27b')).toBe('qwen3.8:27b');
    expect(modelRefName('fixture/legacy')).toBe('fixture/legacy');
    const qwen = model('ollama', 'ollama/qwen', 'qwen3.8:27b - Ollama Local');
    expect(matchesModel(qwen, 'qwen local')).toBe(true);
    expect(matchesModel(qwen, 'qwen cloud')).toBe(false);
    expect(isLocalProvider('lmstudio')).toBe(true);
    expect(isLocalProvider('ollama_cloud')).toBe(false);
  });
});

describe('chart embeds', () => {
  it("replaces the tool's template with the app's tokens and drops the duplicated title", () => {
    const styles = {
      getPropertyValue: (name: string) =>
        ({
          '--text-secondary': '#aaa',
          '--chart-series-1': '#111',
          '--chart-series-2': '#222',
        })[name] ?? '',
    } as CSSStyleDeclaration;
    const layout = chartLayout(
      {
        title: { text: 'Revenue' },
        template: { layout: { paper_bgcolor: '#000' } },
        xaxis: { title: 'Month' },
      },
      styles,
    );
    expect(layout.title).toBeUndefined();
    expect(layout.paper_bgcolor).toBe('rgba(0,0,0,0)');
    expect(layout.xaxis).toEqual({ title: 'Month' });
    const template = layout.template as {
      layout: { colorway: string[]; font: { color: string } };
    };
    expect(template.layout.colorway.slice(0, 2)).toEqual(['#111', '#222']);
    expect(template.layout.font.color).toBe('#aaa');
  });

  it('turns traces into one table with a shared x column', () => {
    expect(
      figureTable([
        { name: 'Revenue', x: ['Jan', 'Feb'], y: [120, 135] },
        { name: 'Costs', x: ['Jan', 'Feb'], y: [80, 82.25] },
      ]),
    ).toEqual({
      headers: ['x', 'Revenue', 'Costs'],
      rows: [
        ['Jan', '120', '80'],
        ['Feb', '135', '82.25'],
      ],
    });
    expect(
      figureTable([{ labels: ['A', 'B'], values: [1, 2], name: 'Share' }]),
    ).toEqual({
      headers: ['Label', 'Share'],
      rows: [
        ['A', '1'],
        ['B', '2'],
      ],
    });
    expect(figureTable([{ type: 'scatter3d' }])).toBeNull();
  });
});

describe('fence languages', () => {
  it('resolves aliases to bundled grammars and labels them', () => {
    expect(resolveLanguage('ts')).toBe('typescript');
    expect(resolveLanguage('Bash')).toBe('shellscript');
    expect(resolveLanguage('python {title="x"}')).toBe('python');
    expect(resolveLanguage('cobol')).toBeNull();
    expect(languageLabel('ts')).toBe('TypeScript');
    expect(languageLabel('cobol')).toBe('cobol');
    expect(languageLabel('')).toBe('Plain text');
    expect(languageForFile('notes.PY', 'text/plain')).toBe('py');
    expect(languageForFile('data', 'application/json')).toBe('json');
    expect(languageForFile('log.txt', 'text/plain')).toBe('');
  });
});
