import type { CSSProperties, ReactNode } from 'react';

/** A small filled eye, so faces stay readable at 16 px. */
function Eye({ x, y, r = 1.15 }: { x: number; y: number; r?: number }) {
  return <circle cx={x} cy={y} r={r} fill="currentColor" stroke="none" />;
}

/**
 * The 12 agent mini-avatars (B240): original glyphs in the app's icon style
 * (24-unit grid, round caps and joins, one stroke weight). Each keeps one
 * tint from the palette, so an agent always looks the same.
 */
const AVATARS: { id: string; tint: string; glyph: ReactNode }[] = [
  {
    id: 'bolt',
    tint: 'var(--chart-series-1)',
    glyph: (
      <>
        <rect x="4.5" y="8.5" width="15" height="11" rx="3.5" />
        <path d="M12 8.5V6" />
        <circle cx="12" cy="4.25" r="1.5" />
        <path d="M2.5 13v2.5M21.5 13v2.5M10 16.5h4" />
        <Eye x={9.25} y={12.75} />
        <Eye x={14.75} y={12.75} />
      </>
    ),
  },
  {
    id: 'hoot',
    tint: 'var(--chart-series-3)',
    glyph: (
      <>
        <path d="M4.5 4.5 8 7.5h8l3.5-3v9a7.5 7.5 0 0 1-15 0z" />
        <circle cx="9.25" cy="12" r="2.25" />
        <circle cx="14.75" cy="12" r="2.25" />
        <path d="m11 16 1 1.25 1-1.25" />
        <Eye x={9.25} y={12} r={0.9} />
        <Eye x={14.75} y={12} r={0.9} />
      </>
    ),
  },
  {
    id: 'fennec',
    tint: 'var(--chart-series-4)',
    glyph: (
      <>
        <path d="M3 3.5 8 8h8l5-4.5-1.25 8.25L12 20.5l-7.75-8.75z" />
        <path d="m11 15.5 1 1 1-1" />
        <Eye x={9} y={12} />
        <Eye x={15} y={12} />
      </>
    ),
  },
  {
    id: 'whisk',
    tint: 'var(--chart-series-2)',
    glyph: (
      <>
        <path d="M5 4.5 8.75 8a8 8 0 0 1 6.5 0L19 4.5v9a7 7 0 0 1-14 0z" />
        <path d="M1.75 13.5h3.5M1.75 16.75l3.5-1M22.25 13.5h-3.5M22.25 16.75l-3.5-1" />
        <path d="m11.25 15.5.75.75.75-.75" />
        <Eye x={9.5} y={12.5} />
        <Eye x={14.5} y={12.5} />
      </>
    ),
  },
  {
    id: 'bruin',
    tint: 'var(--chart-series-5)',
    glyph: (
      <>
        <circle cx="12" cy="13.5" r="7" />
        <path d="M5.94 10A2.4 2.4 0 1 1 9.04 7.16M14.96 7.16A2.4 2.4 0 1 1 18.06 10" />
        <ellipse cx="12" cy="16.25" rx="2.5" ry="1.75" />
        <Eye x={9.25} y={12.5} />
        <Eye x={14.75} y={12.5} />
        <Eye x={12} y={15.9} r={0.8} />
      </>
    ),
  },
  {
    id: 'boo',
    tint: 'var(--chart-series-2)',
    glyph: (
      <>
        <path d="M5.5 20.5V11a6.5 6.5 0 0 1 13 0v9.5l-2.17-1.5-2.16 1.5L12 19l-2.17 1.5-2.16-1.5z" />
        <circle cx="12" cy="15" r="1.1" />
        <Eye x={9.75} y={11.5} r={1.25} />
        <Eye x={14.25} y={11.5} r={1.25} />
      </>
    ),
  },
  {
    id: 'inky',
    tint: 'var(--chart-series-5)',
    glyph: (
      <>
        <path d="M5.5 13.5a6.5 6.5 0 0 1 13 0" />
        <path d="M5.5 13.5c0 2.75-1.25 4.25-3 5M9.25 14.25V17c0 1.5-.75 2.75-2 3.5M14.75 14.25V17c0 1.5.75 2.75 2 3.5M18.5 13.5c0 2.75 1.25 4.25 3 5M5.5 13.5h13" />
        <Eye x={9.5} y={10.75} />
        <Eye x={14.5} y={10.75} />
      </>
    ),
  },
  {
    id: 'chirp',
    tint: 'var(--status-success-text)',
    glyph: (
      <>
        <circle cx="11" cy="13" r="7" />
        <path d="m18 11.25 3.5 1.5-3.5 1.5" />
        <path d="M7 13.5c1.25 2.25 3.75 2.75 5.75 1.25" />
        <path d="M9.5 6.25C9.25 4.75 10 3.5 11.5 3" />
        <Eye x={14} y={11} />
      </>
    ),
  },
  {
    id: 'sprout',
    tint: 'var(--status-success-text)',
    glyph: (
      <>
        <circle cx="12" cy="15" r="5.75" />
        <path d="M12 9.25V7" />
        <path d="M12 7C11.75 4.5 9.75 3 7 3c.25 2.5 2.25 4 5 4zM12 7c.5-2 2.25-3.25 4.75-3.25-.25 2-2 3.25-4.75 3.25z" />
        <path d="M10.25 17.25a2.25 2.25 0 0 0 3.5 0" />
        <Eye x={10} y={14.25} r={1} />
        <Eye x={14} y={14.25} r={1} />
      </>
    ),
  },
  {
    id: 'twinkle',
    tint: 'var(--chart-series-4)',
    glyph: (
      <>
        <path d="M12 3.25 14.82 8.9l6.1.94-4.4 4.36 1.03 6.12L12 17.5l-5.55 2.82 1.03-6.12-4.4-4.36 6.1-.94z" />
        <Eye x={10.4} y={12.4} r={1} />
        <Eye x={13.6} y={12.4} r={1} />
      </>
    ),
  },
  {
    id: 'puff',
    tint: 'var(--chart-series-1)',
    glyph: (
      <>
        <path d="M7 19a4.5 4.5 0 0 1-.9-8.9A6 6 0 0 1 17.6 9.4 4.8 4.8 0 0 1 17.25 19z" />
        <path d="M11 16a1.5 1.5 0 0 0 2 0" />
        <Eye x={9.75} y={13.5} r={1} />
        <Eye x={14.25} y={13.5} r={1} />
      </>
    ),
  },
  {
    id: 'luna',
    tint: 'var(--chart-series-3)',
    glyph: (
      <>
        <path d="M20.5 14.5A8.75 8.75 0 1 1 9.5 3.5a7 7 0 0 0 11 11z" />
        <path d="M6.75 13.25q1.25 1.25 2.5 0M9.5 16.75a2.5 2.5 0 0 0 2.5.25" />
      </>
    ),
  },
];

/**
 * An agent's icon seed: its profile when it has one, otherwise its run, so
 * one agent looks the same in Agents, the transcript and its own header.
 */
export function agentSeed(profileId: string | null | undefined, runId: string) {
  return profileId || runId;
}

function avatarFor(seed: string) {
  let hash = 0;
  for (const character of seed)
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return AVATARS[hash % AVATARS.length];
}

/** An agent's mini-avatar: its glyph on a disc of its tint. Decorative. */
export function AgentAvatar({
  seed,
  size = 20,
  className = '',
}: {
  /** `agentSeed(profile, run)`: the same seed always draws the same icon. */
  seed: string;
  size?: number;
  className?: string;
}) {
  const avatar = avatarFor(seed);
  const style = {
    '--agent-tint': avatar.tint,
    '--agent-size': `${size}px`,
    // One visual stroke weight from 16 to 32 px.
    '--agent-stroke': `${(0.85 + size * 0.03).toFixed(2)}px`,
  } as CSSProperties;
  return (
    <span
      className={`agent-avatar ${className}`}
      data-avatar={avatar.id}
      style={style}
      aria-hidden="true"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {avatar.glyph}
      </svg>
    </span>
  );
}
