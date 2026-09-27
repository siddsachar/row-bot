import { createRoot } from 'react-dom/client';
import { createClientController } from '../api';
import { selectClientPlatform } from '../platform';
import { bindDraftSync } from '../draft-sync';
import { keepNativeLease, reloadWhenLeaseLapses } from '../native-lease';
import { ThemeProvider } from '../ui/theme';
import { installInputModality } from '../ui/input-modality';
import BuddyOverlay from './BuddyOverlay';
import './overlay.css';

/**
 * The desktop Buddy: a separate small entry served at /app-v2/buddy-overlay,
 * so the 380×230 window never loads the workspace shell. It shares the
 * typed controller, the attested native platform and the design tokens.
 */
async function start() {
  installInputModality();
  const params = new URLSearchParams(location.search);
  const query = params.get('fixture');
  const fixture = [
    'normal',
    'incompatible',
    'unauthorized',
    'disconnected',
  ].includes(query ?? '')
    ? (query as 'normal' | 'incompatible' | 'unauthorized' | 'disconnected')
    : undefined;
  const controller = await createClientController({ fixture });
  await controller.start();
  let platform = await selectClientPlatform(
    controller,
    controller.getSnapshot().handshake,
    window,
    () => controller.nativeAttestation(),
  );
  if (
    import.meta.env.VITE_ENABLE_FIXTURES === '1' &&
    params.get('fixturePlatform') === 'fake'
  ) {
    const { createFakePlatform } = await import('../platform/fake');
    platform = createFakePlatform();
  }
  const unbindDrafts = bindDraftSync(controller);
  const releaseLease = keepNativeLease(controller, platform);
  platform = reloadWhenLeaseLapses(platform, () => location.reload());
  createRoot(document.getElementById('root')!).render(
    <ThemeProvider>
      <BuddyOverlay
        controller={controller}
        platform={platform}
        explicitConversation={params.get('conversation')}
      />
    </ThemeProvider>,
  );
  // No unsaved-draft prompt here: drafts save as they are typed, and a prompt
  // would block Dock (the host closes this window).
  window.addEventListener('pagehide', (event) => {
    if (event.persisted) return;
    unbindDrafts();
    releaseLease();
    controller.dispose();
  });
  window.addEventListener('online', () => void controller.setOnline(true));
  window.addEventListener('offline', () => void controller.setOnline(false));
}
void start().catch(() => {
  const root = document.getElementById('root')!;
  root.replaceChildren();
  const message = document.createElement('p');
  message.className = 'buddy-overlay-startup-error';
  message.textContent = 'Buddy could not start. Open Row-Bot to continue.';
  root.append(message);
  // Reveal anyway so the person sees why; the host also force-shows after 2s.
  document.documentElement.dataset.buddyOverlayReady = 'error';
});
