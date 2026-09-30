import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it } from 'vitest';
import { CodeBlock } from './CodeBlock';

afterEach(() => {
  Reflect.deleteProperty(window, 'chrome');
});

it('points to Copy when the desktop window cannot download the code (B275)', async () => {
  // A pywebview (WebView2) window: its downloads are always cancelled.
  Object.assign(window, { chrome: { webview: {} } });
  render(<CodeBlock language="python" text="print('tides')" />);

  await userEvent.click(screen.getByRole('button', { name: 'Download code' }));

  expect(await screen.findByRole('status')).toHaveTextContent(
    'Downloads don’t work in the desktop window. Copy the code instead.',
  );
});
