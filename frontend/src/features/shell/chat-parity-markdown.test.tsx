import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import SafeMarkdown from './chat-parity-markdown';

it('renders useful Markdown structure without interpreting raw HTML', () => {
  const { container } = render(
    <SafeMarkdown
      text={[
        '# Result',
        '',
        '**Strong** and *emphasized* with `inline()`.',
        '',
        '- first',
        '- second',
        '',
        '> quoted',
        '',
        '| Item | State |',
        '| --- | --- |',
        '| Build | Ready |',
        '',
        '```ts',
        '<script>not markup</script>',
        '```',
      ].join('\n')}
    />,
  );
  expect(screen.getByRole('heading', { name: 'Result' })).toBeVisible();
  expect(screen.getByText('Strong')).toHaveProperty('tagName', 'STRONG');
  expect(screen.getByText('emphasized')).toHaveProperty('tagName', 'EM');
  expect(screen.getByText('inline()')).toHaveProperty('tagName', 'CODE');
  expect(screen.getByRole('list')).toHaveTextContent('firstsecond');
  expect(screen.getByText('quoted').closest('blockquote')).not.toBeNull();
  expect(within(screen.getByRole('table')).getByText('Ready')).toBeVisible();
  expect(screen.getByText('<script>not markup</script>')).toBeVisible();
  expect(container.querySelector('script')).toBeNull();
});

it('allows ordinary web links and leaves dangerous protocols as visible text', () => {
  const { container } = render(
    <SafeMarkdown text="[Docs](https://example.test/docs) [Bad](javascript:alert(1))" />,
  );
  expect(screen.getByRole('link', { name: 'Docs' })).toHaveAttribute(
    'href',
    'https://example.test/docs',
  );
  expect(screen.getByRole('link', { name: 'Docs' })).toHaveAttribute(
    'rel',
    'noreferrer noopener',
  );
  expect(screen.queryByRole('link', { name: 'Bad' })).toBeNull();
  expect(container).toHaveTextContent('[Bad](javascript:alert(1))');
});

it('starts a pipe table immediately after prose and handles GFM forms safely', () => {
  const { container } = render(
    <SafeMarkdown
      text={[
        'Build results follow:',
        'Item | State',
        ':--- | ---:',
        'Build | Ready',
        'Escaped \\| name | **Done**',
        '',
        '| Name | Link |',
        '| --- | --- |',
        '| <img src=x onerror=alert(1)> | [safe](https://example.test) |',
      ].join('\n')}
    />,
  );
  expect(screen.getByText('Build results follow:').tagName).toBe('P');
  const tables = screen.getAllByRole('table');
  expect(tables).toHaveLength(2);
  expect(within(tables[0]).getByText('Escaped | name')).toBeVisible();
  expect(within(tables[0]).getByText('Done').tagName).toBe('STRONG');
  expect(within(tables[1]).getByRole('link', { name: 'safe' })).toHaveAttribute(
    'href',
    'https://example.test',
  );
  expect(container.querySelector('img')).toBeNull();
  expect(tables[0].parentElement).toHaveClass('markdown-table-scroll');
});

it('labels fenced code and provides truthful copy and local download actions', async () => {
  const copyText = vi.fn().mockResolvedValue(true);
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, 'click')
    .mockImplementation(() => undefined);
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:code');
  const revoke = vi
    .spyOn(URL, 'revokeObjectURL')
    .mockImplementation(() => undefined);
  render(
    <SafeMarkdown
      text={'```ts\nconst ready = true;\n```'}
      copyText={copyText}
    />,
  );

  expect(screen.getByText('ts')).toBeVisible();
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Copy code' })),
  );
  expect(copyText).toHaveBeenCalledExactlyOnceWith('const ready = true;');
  expect(screen.getByRole('status')).toHaveTextContent('Code copied.');
  await act(async () =>
    fireEvent.click(screen.getByRole('button', { name: 'Download code' })),
  );
  expect(click).toHaveBeenCalledOnce();
  expect(revoke).toHaveBeenCalledExactlyOnceWith('blob:code');
  expect(screen.getByRole('status')).toHaveTextContent('download prepared');
});
