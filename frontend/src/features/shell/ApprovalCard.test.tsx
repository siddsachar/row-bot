import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { clientError } from '../../api/errors';
import ApprovalCard from './ApprovalCard';
import { usePendingApprovals } from './pending-approvals';

const approval = vi.fn();
const intent = vi.fn();
const pendingApprovals = vi.fn();
const open = vi.fn();
const browserLive = vi.fn(async function* (
  _conversation: string,
  _view: string,
  _size: unknown,
  signal: AbortSignal,
) {
  yield {
    state: {
      schema_version: 1,
      conversation_id: 'conversation-a',
      status: 'running',
      control: 'agent',
      acting: '',
      waiting: 'Waiting for your OK in the chat',
      url: 'https://homecover.example/claim/5',
      title: 'Pay your excess',
      site: 'homecover.example',
      loading: false,
      sign_in: '',
      recording: false,
      tabs: [],
      revision: 1,
    },
  };
  await new Promise((resolve) => signal.addEventListener('abort', resolve));
});
vi.mock('../../runtime', () => ({
  useRuntime: () => ({
    controller: { approval, intent, pendingApprovals, browserLive },
  }),
}));
vi.mock('../../ui/overlays', () => ({
  useOverlay: () => ({ open, notify: vi.fn(), close: vi.fn() }),
}));

const view = {
  id: 'approval-a',
  status: 'pending',
  revision: '3',
  nonce: 'nonce-a',
  action_label: 'workspace_file_delete',
  reason: 'Delete a file in the workspace.',
  risk_class: 'unknown',
  scope: '',
  safe_argument_summary: '{"file_path": "notes.txt"}',
  policy_revision: '6964',
};

beforeEach(() => {
  approval.mockReset();
  intent.mockReset();
  open.mockReset();
  approval.mockResolvedValue(view);
  intent.mockResolvedValue({ status: 'completed' });
});

async function renderCard() {
  render(<ApprovalCard id="approval-a" />);
  // The buttons show at once, disabled until the approval has loaded.
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Approve' })).toBeEnabled(),
  );
}

it('answers a card left open past its five-minute nonce, with a fresh one, only while the approval is unchanged (#631)', async () => {
  await renderCard();
  // Read later: the card's nonce ran out, the approval still waits as shown.
  intent.mockRejectedValueOnce(clientError({ code: 'approval_expired' }));
  approval.mockResolvedValue({ ...view, nonce: 'nonce-b' });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
  });
  expect(intent.mock.calls.map((call) => call[2].nonce)).toEqual([
    'nonce-a',
    'nonce-b',
  ]);
  expect(intent.mock.calls[1][3]).toBe('3');
  expect(await screen.findByText('Approval submitted.')).toBeVisible();
});

it('never re-sends an answer to an approval that changed after the person read it (#631)', async () => {
  await renderCard();
  intent.mockRejectedValueOnce(clientError({ code: 'approval_expired' }));
  approval.mockResolvedValue({ ...view, revision: '4', nonce: 'nonce-b' });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
  });
  expect(intent).toHaveBeenCalledTimes(1);
  expect(
    screen.getByText(/This approval expired/, { exact: false }),
  ).toBeVisible();
});

it('ignores Ctrl+Enter typed in a text field (B134)', async () => {
  await renderCard();
  const composer = document.createElement('textarea');
  document.body.append(composer);
  composer.focus();
  await act(async () => {
    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true });
  });
  expect(intent).not.toHaveBeenCalled();
  const editable = document.createElement('div');
  editable.setAttribute('contenteditable', '');
  document.body.append(editable);
  await act(async () => {
    fireEvent.keyDown(editable, { key: 'Enter', metaKey: true });
  });
  expect(intent).not.toHaveBeenCalled();
  composer.remove();
  editable.remove();
});

it('shows the Ctrl ↵ hint only with a keyboard and a wide layout, never on a phone (B389)', async () => {
  const media = vi.mocked(window.matchMedia);
  // A phone: a coarse pointer, a narrow layout. The setup's default matches nothing.
  const { unmount } = render(<ApprovalCard id="approval-a" />);
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Approve' })).toBeEnabled(),
  );
  expect(screen.getByRole('button', { name: 'Approve' })).toHaveTextContent(
    /^Approve$/,
  );
  unmount();
  // A desktop with a mouse and keyboard.
  media.mockImplementation(
    (query: string) =>
      ({
        matches: query === '(pointer: fine) and (min-width: 641px)',
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
  try {
    await renderCard();
    expect(
      screen.getByRole('button', { name: 'Approve' }).textContent,
    ).not.toBe('Approve');
  } finally {
    media.mockImplementation(
      (query: string) =>
        ({
          matches: false,
          media: query,
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
        }) as unknown as MediaQueryList,
    );
  }
});

it('approves with Ctrl+Enter when no text field has focus', async () => {
  await renderCard();
  // The shortcut listens once the approval's details have loaded, which can
  // be a moment after the buttons enable.
  await waitFor(() => {
    fireEvent.keyDown(document.body, { key: 'Enter', ctrlKey: true });
    expect(intent).toHaveBeenCalled();
  });
  expect(intent).toHaveBeenCalledOnce();
  expect(intent).toHaveBeenCalledWith(
    'approval-a',
    'approval.resolve',
    { decision: 'approve', nonce: 'nonce-a' },
    '3',
  );
  expect(await screen.findByText('Approval submitted.')).toBeVisible();
});

it('explains the action in plain words, without request ids or unrated risk', async () => {
  await renderCard();
  expect(screen.queryByText(/Not classified|policy revision/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Details' }));
  const details = open.mock.calls[0][0];
  expect(details.description).toBe(
    'What Row-Bot wants to do, and what it affects.',
  );
  expect(details.title).toBe('Delete a file?');
  render(details.content);
  expect(screen.getByText('This approval covers')).toBeVisible();
  expect(screen.getByText('This action only.')).toBeVisible();
  expect(screen.getByText('Delete a file')).toBeVisible();
  expect(screen.getByText('File path: notes.txt')).toBeVisible();
  expect(document.body).not.toHaveTextContent(/workspace_file_delete|\{"/);
  expect(screen.queryByText(/Risk|Request approval-a|policy revision/)).toBe(
    null,
  );
  expect(screen.queryByText(/No server expiry/)).toBeNull();
});

it('names the folder a command runs in and what one approval covers (U7)', async () => {
  approval.mockResolvedValue({
    ...view,
    action_label: 'run_command',
    reason: 'Run shell command: npm test',
    safe_argument_summary: '',
    folder: 'row-bot',
  });
  await renderCard();
  fireEvent.click(screen.getByRole('button', { name: 'Details' }));
  render(open.mock.calls[0][0].content);
  const facts = Object.fromEntries(
    [...document.querySelectorAll('.approval-detail dt')].map((term) => [
      term.textContent,
      term.nextElementSibling?.textContent,
    ]),
  );
  expect(facts).toMatchObject({
    Action: 'Run a command',
    'Runs in': 'row-bot',
    'This approval covers': 'This command only.',
  });
  expect(screen.queryByText('Affects')).toBeNull();
});

it('lists structured arguments in words, never as code', async () => {
  approval.mockResolvedValue({
    ...view,
    safe_argument_summary:
      '{"orders":[{"sku":"A1","qty":2,"ship_to":{"city":"Leeds"}},{"sku":"B2","qty":1}],"rush":true}',
  });
  await renderCard();
  fireEvent.click(screen.getByRole('button', { name: 'Details' }));
  render(open.mock.calls[0][0].content);
  expect(
    screen.getByText(
      'Orders: sku A1, qty 2, ship to (city Leeds); sku B2, qty 1',
    ),
  ).toBeVisible();
  expect(screen.getByText('Rush: yes')).toBeVisible();
  expect(document.body).not.toHaveTextContent(/[{[]"/);
});

it('says since when the approval waits (B255)', async () => {
  approval.mockResolvedValue({
    ...view,
    requested_at: new Date(2026, 8, 30, 9, 0).toISOString(),
  });
  await renderCard();
  expect(screen.getByText(/^Waiting since/)).toBeVisible();
});

it('asks to turn on a tool the work needs as a setup card (decision 12)', async () => {
  approval.mockResolvedValue({
    ...view,
    action_label: 'row_bot_update_setting',
    reason: 'Row-Bot needs Web Search for this.',
    setup: { kind: 'tool', label: 'Web Search' },
  });
  render(<ApprovalCard id="approval-a" onAllowInChat={vi.fn()} />);
  const card = await screen.findByRole('complementary', {
    name: 'Turn on Web Search',
  });
  expect(card).toHaveTextContent('Turn on Web Search?');
  expect(card).toHaveTextContent('Row-Bot needs Web Search for this.');
  await screen.findByRole('button', { name: 'Turn on' });
  expect(
    screen.queryByRole('button', { name: 'Use Auto in this chat' }),
  ).toBeNull();
  expect(screen.queryByRole('button', { name: 'Details' })).toBeNull();
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Turn on' }));
  });
  expect(intent).toHaveBeenCalledWith(
    'approval-a',
    'approval.resolve',
    { decision: 'approve', nonce: 'nonce-a' },
    '3',
  );
  expect(await screen.findByRole('status')).toHaveTextContent(
    'Turning on Web Search…',
  );
});

it('keeps the tool off with Not now', async () => {
  approval.mockResolvedValue({
    ...view,
    setup: { kind: 'tool', label: 'Developer tools' },
  });
  render(<ApprovalCard id="approval-a" />);
  const notNow = await screen.findByRole('button', { name: 'Not now' });
  await screen.findByText('Delete a file in the workspace.');
  await act(async () => {
    fireEvent.click(notNow);
  });
  expect(intent).toHaveBeenCalledWith(
    'approval-a',
    'approval.resolve',
    { decision: 'reject', nonce: 'nonce-a' },
    '3',
  );
  expect(await screen.findByRole('status')).toHaveTextContent(
    'Developer tools stays off.',
  );
});

it('shows which app asks, with its logo and name, and still needs the person', async () => {
  approval.mockResolvedValue({
    ...view,
    action_label: 'mcp_notion_delete_page',
    reason: 'Delete a page for good.',
    app: { item_id: 'mcp:notion', name: 'Notion', icon: 'letter:N' },
  });
  await renderCard();
  const card = screen.getByRole('complementary', {
    name: /Approval required for .* in Notion$/,
  });
  expect(card).toHaveTextContent('Notion');
  expect(intent).not.toHaveBeenCalled();
});

it('reads an app tool’s call as labelled fields with their own line breaks, never its internal name (B332)', async () => {
  const reason =
    'Title: Daily summary crashes\nTeam: RBT\nDescription:\n## Failure\nThe riverside-shop summary crashed.';
  approval.mockResolvedValue({
    ...view,
    action_label: 'mcp_linear_mcp_save_issue',
    reason,
    safe_argument_summary: '{"name": "Daily summary crashes"}',
    app: {
      item_id: 'mcp:linear',
      name: 'Linear',
      icon: 'letter:L',
      tool: 'Save issue',
    },
  });
  await renderCard();
  const card = screen.getByRole('complementary', {
    name: 'Approval required for Save issue in Linear',
  });
  expect(
    within(card).getByText('Allow Linear to save an issue?'),
  ).toBeVisible();
  // Real line breaks, as the server wrote them: nothing re-read, escaped or repeated.
  expect(
    within(card).getByText(/^Title: Daily summary crashes/).textContent,
  ).toBe(reason);
  expect(within(card).getAllByText(/Daily summary crashes/)).toHaveLength(1);
  expect(card).not.toHaveTextContent(/mcp_|\\n/);
  fireEvent.click(within(card).getByRole('button', { name: 'Details' }));
  const details = open.mock.calls[0][0];
  expect(details.title).toBe('Allow Linear to save an issue?');
  render(details.content);
  expect(screen.getByText('Linear · Save issue')).toBeVisible();
  expect(
    screen
      .getAllByText(/^Title: Daily summary crashes/)
      .map((node) => node.textContent),
  ).toEqual([reason, reason]);
});

it('titles an app tool Row-Bot never named with the app and the action, keeping its raw name under Details (B388)', async () => {
  approval.mockResolvedValue({
    ...view,
    action_label: 'mcp_stripe_create_product',
    reason: 'Create a product.',
    safe_argument_summary: '{"name": "Gift card"}',
    // Named by the server from the one connection the name can come from.
    app: {
      item_id: 'mcp:stripe',
      name: 'Stripe',
      icon: 'letter:S',
      tool: 'Create product',
    },
  });
  await renderCard();
  const card = screen.getByRole('complementary', {
    name: 'Approval required for Create product in Stripe',
  });
  expect(
    within(card).getByText('Allow Stripe to create a product?'),
  ).toBeVisible();
  expect(card).not.toHaveTextContent(/mcp_|MCP/);
  fireEvent.click(within(card).getByRole('button', { name: 'Details' }));
  render(open.mock.calls[0][0].content);
  expect(screen.getByText('Stripe · Create product')).toBeVisible();
  expect(screen.getByText('mcp_stripe_create_product')).toBeVisible();
});

it('re-reads the waiting approvals at once after an answer (B302)', async () => {
  pendingApprovals.mockResolvedValue({ items: [], next_cursor: null });
  function Sidebar() {
    const { page } = usePendingApprovals(pendingApprovals);
    return <p>{page ? `${page.items.length} waiting` : 'reading'}</p>;
  }
  render(<Sidebar />);
  await waitFor(() => expect(pendingApprovals).toHaveBeenCalledTimes(1));
  await renderCard();
  fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
  expect(await screen.findByText('Approval submitted.')).toBeVisible();
  expect(pendingApprovals).toHaveBeenCalledTimes(2);
});

it('offers allowing an action in this chat only when it is repeatable (F21, 5.3.1 B2)', async () => {
  await renderCard();
  expect(
    screen.queryByRole('button', { name: 'Allow in this chat' }),
  ).not.toBeInTheDocument();
  approval.mockResolvedValue({
    ...view,
    action_label: 'developer_commit_changes',
    repeatable: true,
  });
  // Within the second card: the first may read its approval again meanwhile and offer it too.
  const second = render(<ApprovalCard id="approval-b" />);
  fireEvent.click(
    await within(second.container).findByRole('button', {
      name: 'Allow in this chat',
    }),
  );
  await waitFor(() =>
    expect(intent).toHaveBeenCalledWith(
      'approval-b',
      'approval.resolve',
      { decision: 'approve', nonce: 'nonce-a', scope: 'chat' },
      '3',
    ),
  );
});

it('says quietly why the Auto reviewer asked, on the card and in its details (A0)', async () => {
  approval.mockResolvedValue({
    ...view,
    reviewer_reason: 'You asked to tidy the folder, not to delete files.',
  });
  await renderCard();
  expect(
    screen.getByText(
      'The reviewer asked you: You asked to tidy the folder, not to delete files.',
    ),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Details' }));
  render(open.mock.calls[0][0].content);
  expect(
    screen.getByText(
      'Asked you: You asked to tidy the folder, not to delete files.',
    ),
  ).toBeVisible();
});

it('offers reviewed Auto for this chat under Ask, and approves', async () => {
  const onAllowInChat = vi.fn(async () => undefined);
  render(<ApprovalCard id="approval-a" onAllowInChat={onAllowInChat} />);
  const auto = await screen.findByRole('button', {
    name: 'Use Auto in this chat',
  });
  await waitFor(() => expect(auto).toBeEnabled());
  expect(screen.queryByText(/The reviewer asked you/)).toBeNull();
  await act(async () => fireEvent.click(auto));
  expect(onAllowInChat).toHaveBeenCalledOnce();
  expect(intent).toHaveBeenCalledWith(
    'approval-a',
    'approval.resolve',
    { decision: 'approve', nonce: 'nonce-a' },
    '3',
  );
});

it('asks the tool’s own question for a local address in one chat (F4)', async () => {
  approval.mockResolvedValue({
    ...view,
    action_label: 'Browser Navigate',
    question: "Allow Row-Bot's browser to open localhost:5173 in this chat?",
    reason:
      'localhost:5173 is on this computer. Allowing it lets Row-Bot’s browser open only this address, only in this chat, for up to an hour. You can remove it sooner in the browser panel.',
    safe_argument_summary: '{"address": "localhost:5173"}',
  });
  await renderCard();

  const card = screen.getByRole('complementary', {
    name: "Approval required: Allow Row-Bot's browser to open localhost:5173 in this chat?",
  });
  expect(
    within(card).getByText(
      "Allow Row-Bot's browser to open localhost:5173 in this chat?",
    ),
  ).toBeVisible();
  expect(card).toHaveTextContent('only this address, only in this chat');
  fireEvent.click(within(card).getByRole('button', { name: 'Details' }));
  expect(open.mock.calls[0][0].title).toBe(
    "Allow Row-Bot's browser to open localhost:5173 in this chat?",
  );
});

it('asks to download ffmpeg in plain words: how big, from where, and its licence (F3)', async () => {
  approval.mockResolvedValue({
    ...view,
    action_label: 'edit_video',
    question:
      'Video editing needs ffmpeg (about 30 MB) from PyPI. Download it now?',
    reason:
      "Row-Bot will download imageio-ffmpeg 0.6.0 for Windows (30 MB) from PyPI (files.pythonhosted.org), check it against the fingerprint saved in Row-Bot, and keep only its ffmpeg program in Row-Bot's data folder. ffmpeg is free software (GPL version 3 or later); Row-Bot runs it as a separate program. This happens once.",
    risk_class: 'medium',
    safe_argument_summary:
      '{"download": "imageio-ffmpeg 0.6.0 (Windows, 30 MB)", "from": "files.pythonhosted.org", "licence": "ffmpeg 7.1, GPL version 3 or later"}',
  });
  await renderCard();

  const card = screen.getByRole('complementary', {
    name: 'Approval required: Video editing needs ffmpeg (about 30 MB) from PyPI. Download it now?',
  });
  expect(card).toHaveTextContent('keep only its ffmpeg program');
  fireEvent.click(within(card).getByRole('button', { name: 'Details' }));
  render(open.mock.calls[0][0].content);
  expect(screen.getByText('From: files.pythonhosted.org')).toBeVisible();
  expect(
    screen.getByText('Licence: ffmpeg 7.1, GPL version 3 or later'),
  ).toBeVisible();
  expect(document.body).not.toHaveTextContent(/edit_video|\{"/);
});

it('asks to post on X as the named account with the full text, the reply link and each file (N7)', async () => {
  const text = `Row-Bot 5.3 is out. ${'Every post names its account. '.repeat(50)}`;
  approval.mockResolvedValue({
    ...view,
    action_label: 'x_post',
    reason: 'Reply as @rowbot_ai to https://x.com/i/status/42.',
    question: 'Reply on X as @rowbot_ai?',
    reviewer_reason: 'Only you can approve what goes out on X in your name.',
    x_post: {
      account_id: '111',
      username: 'rowbot_ai',
      name: 'Row-Bot',
      action: 'reply',
      text,
      target_id: '42',
      target_url: 'https://x.com/i/status/42',
      target_text: '',
      media: [
        {
          name: 'launch.png',
          size: 204800,
          sha256: '0123456789ab',
          kind: 'image',
          thumbnail: 'data:image/jpeg;base64,AAAA',
        },
        {
          name: 'demo.mp4',
          size: 3145728,
          sha256: 'ba9876543210',
          kind: 'video',
          thumbnail: '',
          silent: true,
          ref: 'conversation-a:00000000-0000-4000-8000-000000000001',
          mime: 'video/mp4',
        },
      ],
    },
  });
  await renderCard();
  const card = screen.getByRole('complementary', {
    name: 'Approval required: Reply on X as @rowbot_ai?',
  });
  expect(within(card).getByText('Reply on X as @rowbot_ai?')).toBeVisible();
  expect(within(card).getByText('@rowbot_ai')).toBeVisible();
  expect(within(card).getByText('Row-Bot')).toBeVisible();
  expect(within(card).getByText('ID 111')).toBeVisible();
  // The whole text, never cut.
  expect(
    within(card).getByText(text.trim(), {
      normalizer: (value) => value.trim(),
    }),
  ).toBeVisible();
  expect(
    within(card).getByRole('link', { name: 'x.com/i/status/42' }),
  ).toHaveAttribute('href', 'https://x.com/i/status/42');
  const files = within(card).getByRole('list', { name: '2 attached files' });
  expect(
    within(files).getByRole('img', { name: 'Preview of launch.png' }),
  ).toBeVisible();
  expect(within(files).getByText('launch.png')).toBeVisible();
  expect(within(files).getByText('0123456789ab')).toBeVisible();
  expect(within(files).getByText('ba9876543210')).toBeVisible();
  // A video with nothing to hear says so before it's approved; it plays silent on X too.
  expect(within(files).getAllByText('No sound')).toHaveLength(1);
  // A file from Library › Media opens full size to check before it goes; one
  // on this computer (no reference) shows only its facts.
  expect(
    within(files).queryByRole('button', { name: 'Open launch.png' }),
  ).toBeNull();
  fireEvent.click(within(files).getByRole('button', { name: 'Open demo.mp4' }));
  expect(open).toHaveBeenLastCalledWith(
    expect.objectContaining({
      title: 'demo.mp4',
      className: 'media-lightbox',
    }),
  );
  expect(
    within(card).getByText(/The reviewer asked you: Only you can approve/),
  ).toBeVisible();
  // The reason's sentence isn't repeated beside the card's own facts.
  expect(within(card).queryByText(/^Reply as @rowbot_ai to/)).toBeNull();
});

it('edits a post on its card: its text against the account’s limit and the files it keeps, then asks again (G2)', async () => {
  approval.mockResolvedValue({
    ...view,
    action_label: 'x_post',
    question: 'Post to X as @rowbot_ai?',
    x_post: {
      account_id: '111',
      username: 'rowbot_ai',
      name: 'Row-Bot',
      action: 'post',
      text: 'Draft launch post',
      limit: 280,
      media: [
        { name: 'a.png', size: 2048, sha256: '0123456789ab', kind: 'image' },
        { name: 'b.png', size: 2048, sha256: 'ba9876543210', kind: 'image' },
      ],
    },
  });
  await renderCard();
  fireEvent.click(screen.getByRole('button', { name: 'Edit the post' }));
  // Nothing is approved while it's edited.
  expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
  const text = screen.getByRole('textbox', {
    name: 'Edit the post as @rowbot_ai',
  });
  expect(text).toHaveValue('Draft launch post');
  expect(screen.getByText('17 of 280')).toBeVisible();
  // A link counts 23, whatever its length.
  fireEvent.change(text, {
    target: { value: `Row-Bot 5.3.1: https://row-bot.ai/${'x'.repeat(80)}` },
  });
  expect(screen.getByText('38 of 280')).toBeVisible();
  fireEvent.change(text, { target: { value: 'Long. '.repeat(50) } });
  expect(screen.getByText('20 over the 280 @rowbot_ai can post')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Save edits' })).toBeDisabled();
  fireEvent.change(text, { target: { value: 'Row-Bot 5.3.1 is out.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Remove a.png' }));
  expect(screen.getByRole('list', { name: 'Attached file' })).toBeVisible();
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Save edits' }));
  });
  expect(intent).toHaveBeenCalledOnce();
  expect(intent).toHaveBeenCalledWith(
    'approval-a',
    'approval.resolve',
    {
      decision: 'edit',
      nonce: 'nonce-a',
      edit: { text: 'Row-Bot 5.3.1 is out.', media: ['ba9876543210'] },
    },
    '3',
  );
  expect(
    screen.getByText('Edited. Row-Bot asks again with your version.'),
  ).toBeVisible();
});

it('an approval for a browser action shows the page and opens the Browser pane (N12-A)', async () => {
  const { onBrowserPaneRequest } = await import('../browser/open-pane');
  const opened: string[] = [];
  const stop = onBrowserPaneRequest((conversation) =>
    opened.push(conversation),
  );
  approval.mockResolvedValue({
    ...view,
    action_label: 'Browser Click',
    question: 'Pay on homecover.example?',
    reason:
      'Row-Bot will click "Confirm and pay £60" on homecover.example. This can charge you.',
    risk_class: 'high',
  });
  render(<ApprovalCard id="approval-a" conversationId="conversation-a" />);
  const card = await screen.findByRole('complementary', {
    name: 'Approval required: Pay on homecover.example?',
  });
  const picture = await within(card).findByRole('button', {
    name: 'Open the browser: homecover.example',
  });
  fireEvent.click(picture);
  expect(opened).toEqual(['conversation-a']);
  stop();
});

it('asks to save to memory from an incognito chat with the exact text, Save or Don’t save (N11)', async () => {
  approval.mockResolvedValue({
    ...view,
    action_label: 'Save Memory',
    question: 'Save to memory? This chat is incognito.',
    reason: 'Coffee: I take it black, no sugar.',
    memory_text: 'Coffee: I take it black, no sugar.',
  });
  const onAllowInChat = vi.fn(async () => undefined);
  render(<ApprovalCard id="approval-a" onAllowInChat={onAllowInChat} />);

  const card = await screen.findByRole('complementary', {
    name: 'Approval required: Save to memory? This chat is incognito.',
  });
  const save = within(card).getByRole('button', { name: /^Save/ });
  await waitFor(() => expect(save).toBeEnabled());
  expect(card).toHaveTextContent('Coffee: I take it black, no sugar.');
  // Only the person answers it: no Auto for this chat, nothing for the rest of the reply.
  expect(
    within(card).queryByRole('button', { name: 'Use Auto in this chat' }),
  ).toBeNull();
  expect(
    within(card).queryByRole('button', { name: 'Allow in this chat' }),
  ).toBeNull();

  await act(async () => fireEvent.click(save));
  expect(intent).toHaveBeenCalledWith(
    'approval-a',
    'approval.resolve',
    { decision: 'approve', nonce: 'nonce-a' },
    '3',
  );
  expect(within(card).getByRole('status')).toHaveTextContent(
    'Saving to memory…',
  );
});

it('keeps an incognito chat’s memory unsaved with Don’t save (N11)', async () => {
  approval.mockResolvedValue({
    ...view,
    question: 'Save to memory? This chat is incognito.',
    memory_text: 'Coffee: I take it black, no sugar.',
  });
  render(<ApprovalCard id="approval-a" />);
  const dont = await screen.findByRole('button', { name: 'Don’t save' });
  await waitFor(() => expect(dont).toBeEnabled());

  await act(async () => fireEvent.click(dont));

  expect(intent).toHaveBeenCalledWith(
    'approval-a',
    'approval.resolve',
    { decision: 'reject', nonce: 'nonce-a' },
    '3',
  );
  expect(screen.getByRole('status')).toHaveTextContent('Not saved to memory.');
});
