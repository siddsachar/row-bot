import { LayoutList, PenLine, Sparkles } from 'lucide-react';
import type { PaletteCommand } from '../shell/CommandPalette';
import { showWhatBuddyHas } from './BuddyMoment';
import { FOR_YOU_PATH } from './moment-actions';
import type { ProactiveStore } from './proactive-state';

/** Where the palette's Drafts goes: Home, with the Drafts drawer open. */
export const DRAFTS_PATH = '/?tab=overview&draft=all';

/**
 * The command palette's Proactive entries while Proactive is on: For you,
 * Drafts, and Show what Buddy has for you (the last bubble again while its
 * moment is still open, else For you). No keyboard shortcut of their own.
 */
export function proactivePaletteCommands(
  store: ProactiveStore | null,
  go: (to: string) => void,
  close: () => void,
): PaletteCommand[] {
  if (!store) return [];
  return [
    {
      id: 'proactive-for-you',
      label: 'For you',
      keywords: 'proactive moments today brief follow-ups meetings',
      icon: <LayoutList size={16} />,
      run: () => go(FOR_YOU_PATH),
    },
    {
      id: 'proactive-drafts',
      label: 'Drafts',
      keywords: 'proactive replies drafted emails send',
      icon: <PenLine size={16} />,
      run: () => go(DRAFTS_PATH),
    },
    {
      id: 'proactive-buddy',
      label: 'Show what Buddy has for you',
      keywords: 'proactive bubble moment buddy again',
      icon: <Sparkles size={16} />,
      run: () => {
        close();
        if (!showWhatBuddyHas(store.getView().moments ?? [])) go(FOR_YOU_PATH);
      },
    },
  ];
}
