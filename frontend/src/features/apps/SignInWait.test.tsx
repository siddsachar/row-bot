import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { PlanSignIn } from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { Button } from '../../ui/primitives';
import SignInWait, { pageName } from './SignInWait';

const LINK = 'https://auth.example.test/authorize?state=s';

afterEach(() => {
  vi.useRealTimers();
});

function show(
  signIn: Partial<PlanSignIn>,
  onFinish: (address: string) => Promise<void> = vi.fn(async () => undefined),
) {
  const platform = {
    openExternal: vi.fn(async () => ({ status: 'ok' })),
    writeClipboard: vi.fn(async () => ({ status: 'ok' })),
  } as unknown as ClientPlatform;
  const stop = vi.fn();
  render(
    <RuntimeContext.Provider
      value={{ controller: {} as ClientController, platform }}
    >
      <SignInWait
        name="Linear"
        signIn={{ method: 'oauth_dcr', authorization_url: LINK, ...signIn }}
        busy={false}
        stop={<Button onClick={stop}>Stop</Button>}
        onFinish={onFinish}
      />
    </RuntimeContext.Provider>,
  );
  return { platform, stop, onFinish };
}

function problem(code: string) {
  return Object.assign(new Error(code), { code, status: 409 });
}

it('another device pastes back the address its browser ends on, in three steps', async () => {
  const { platform, onFinish } = show({ delivery: 'paste', expires_in: 300 });
  expect(
    screen.getByText(/Row-Bot is running on another computer/),
  ).toBeVisible();
  const steps = screen.getAllByRole('listitem');
  expect(steps).toHaveLength(3);
  expect(steps[1]).toHaveTextContent(
    "You'll end on a page that doesn't load. That's expected: copy its whole address from the address bar. It starts with http://127.0.0.1.",
  );
  expect(screen.getByRole('timer')).toHaveTextContent(
    'The sign-in link works for another 5:00.',
  );
  // Nothing opens by itself: the page that won't load is explained first.
  expect(platform.openExternal).not.toHaveBeenCalled();
  const open = screen.getByRole('button', { name: 'Open sign-in page' });
  const finish = screen.getByRole('button', { name: 'Finish' });
  expect(open).toHaveClass('primary');
  expect(finish).toBeDisabled(); // Nothing pasted yet: one obvious next step.
  fireEvent.click(open);
  expect(platform.openExternal).toHaveBeenCalledWith(LINK);
  expect(open).not.toHaveClass('primary');
  const field = screen.getByLabelText('Paste that address here');
  const pasted =
    'http://127.0.0.1/api/v1/settings/mcp/auth/callback?code=c&state=s';
  fireEvent.change(field, { target: { value: `  ${pasted}\n` } });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Finish' }));
  });
  expect(onFinish).toHaveBeenCalledWith(pasted);
  expect(field).toHaveValue('');
});

it.each([
  [
    'mcp_auth_address_invalid',
    "That doesn't look like the address. After you sign in, copy the whole address from your browser's address bar. It starts with http://127.0.0.1.",
  ],
  [
    'mcp_auth_address_other',
    'That address is from a different sign-in. Open the sign-in page from here, then paste the address you land on.',
  ],
  [
    'mcp_auth_address_used',
    "That address has already been used. If signing in didn't finish, start again.",
  ],
  [
    'mcp_auth_address_expired',
    'This sign-in has ended. Start again to get a new link.',
  ],
])(
  'a refused address (%s) says why in plain words, beside the field',
  async (code, words) => {
    show(
      { delivery: 'paste', expires_in: 200 },
      vi.fn(async () => {
        throw problem(code);
      }),
    );
    const field = screen.getByLabelText('Paste that address here');
    fireEvent.change(field, { target: { value: 'http://127.0.0.1/x' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Finish' }));
    });
    expect(screen.getByRole('alert')).toHaveTextContent(words);
    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(field).toHaveAccessibleDescription(words);
    expect(field).toHaveValue('http://127.0.0.1/x'); // Kept, to fix rather than retype.
    expect(document.body).not.toHaveTextContent(/mcp_auth/);
    fireEvent.change(field, { target: { value: 'http://127.0.0.1/y' } });
    expect(screen.queryByRole('alert')).toBeNull(); // Editing clears it.
  },
);

it('once the address is accepted it says the sign-in is finishing', () => {
  show({ delivery: 'paste', expires_in: 120, received: true });
  expect(screen.getByRole('status')).toHaveTextContent(
    'Finishing signing in to Linear…',
  );
  expect(screen.queryByLabelText('Paste that address here')).toBeNull();
});

it('a code is shown with its page, copied in one tap, and counted down', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: false });
  const { platform } = show({
    delivery: 'device',
    authorization_url: null,
    expires_in: 125,
    device: {
      user_code: 'WDJB-MJHT',
      verification_uri: 'https://github.com/login/device',
      verification_uri_complete: '',
    },
  });
  expect(screen.getByText(/Sign in to Linear with a code/)).toBeVisible();
  expect(screen.getAllByRole('listitem')[0]).toHaveTextContent(
    'Go to github.com/login/device and sign in.',
  );
  expect(
    screen.getByRole('status', { name: 'Sign-in code' }),
  ).toHaveTextContent('WDJB-MJHT');
  expect(
    screen.getByText(
      'Waiting for you to allow access. This page updates by itself.',
    ),
  ).toHaveAttribute('role', 'status');
  expect(screen.getByRole('timer')).toHaveTextContent(
    'The sign-in code works for another 2:05.',
  );
  await act(async () => {
    vi.advanceTimersByTime(6000);
  });
  expect(screen.getByRole('timer')).toHaveTextContent('1:59');
  fireEvent.click(
    screen.getByRole('button', { name: 'Open github.com/login/device' }),
  );
  expect(platform.openExternal).toHaveBeenCalledWith(
    'https://github.com/login/device',
  );
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Copy the code' }));
  });
  expect(platform.writeClipboard).toHaveBeenCalledWith('WDJB-MJHT');
  expect(
    screen.getByRole('button', { name: 'Copy the code' }),
  ).toHaveTextContent('Copied');
  await act(async () => {
    vi.advanceTimersByTime(125000);
  });
  expect(screen.getByRole('timer')).toHaveTextContent(
    'The sign-in code has expired.',
  );
});

it('while the code is on its way it says so, never the browser steps', () => {
  show({ delivery: 'device', authorization_url: null, device: null });
  expect(screen.getByRole('status')).toHaveTextContent(
    'Getting a sign-in code from Linear…',
  );
  expect(screen.queryByText(/in your browser/)).toBeNull();
  expect(screen.getByRole('button', { name: 'Stop' })).toBeVisible();
});

it('a code with its page prefilled opens that page', () => {
  const { platform } = show({
    delivery: 'device',
    device: {
      user_code: 'ABCD-EFGH',
      verification_uri: 'https://auth.example.test/device',
      verification_uri_complete:
        'https://auth.example.test/device?user_code=ABCD-EFGH',
    },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Open auth.example.test/device' }),
  );
  expect(platform.openExternal).toHaveBeenCalledWith(
    'https://auth.example.test/device?user_code=ABCD-EFGH',
  );
});

it('through the public address the person just signs in', () => {
  const { platform } = show({ delivery: 'public', expires_in: 290 });
  expect(
    screen.getByText(
      'Sign in to Linear on the page that opens, then come back here. This page updates by itself.',
    ),
  ).toBeVisible();
  const open = screen.getByRole('button', { name: 'Open sign-in page' });
  expect(open).toHaveClass('primary');
  fireEvent.click(open);
  expect(platform.openExternal).toHaveBeenCalledWith(LINK);
  expect(
    screen.getByRole('button', { name: 'Open the sign-in page again' }),
  ).toBeVisible();
  expect(screen.queryByLabelText('Paste that address here')).toBeNull();
});

it('on this computer the browser sign-in is as before, with its time left', () => {
  show({ expires_in: 61 });
  expect(
    screen.getByText(
      'Finish signing in to Linear in your browser. This page updates by itself.',
    ),
  ).toBeVisible();
  expect(screen.getByRole('timer')).toHaveTextContent('1:01');
  expect(
    screen.getByRole('button', { name: 'Open the sign-in page again' }),
  ).toBeVisible();
});

it('names a page without its scheme', () => {
  expect(pageName('https://github.com/login/device/')).toBe(
    'github.com/login/device',
  );
  expect(pageName('not a page')).toBe('not a page');
});
