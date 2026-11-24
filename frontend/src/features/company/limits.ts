import { useEffect, useState } from 'react';
import type { ClientController } from '../../api/controller';
import type { CompanyView } from '../../api/types';

/**
 * What the company's policy limits beyond models (Row-Bot for Teams, C3b):
 * tools switched off, the approval ceiling, chat export and which apps may
 * be added. None with no company joined, and then nothing changes.
 */
export type CompanyLimits = NonNullable<CompanyView['limits']>;

/** The words where a setting the company controls would be. */
export const SET_BY_COMPANY = 'Set by your company';

const LEVELS = ['block', 'approve', 'auto', 'full'] as const;
type Level = (typeof LEVELS)[number];

/** Whether a level may be chosen under the company's ceiling (always, with none). */
export function levelAllowed(
  level: string,
  ceiling: string | null | undefined,
): boolean {
  if (!ceiling) return true;
  const at = LEVELS.indexOf(level as Level);
  const highest = LEVELS.indexOf(ceiling as Level);
  return at < 0 || highest < 0 || at <= highest;
}

/** A level as it runs: the person's choice, at most the company's ceiling. */
export function cappedLevel<T extends string>(
  level: T,
  ceiling: string | null | undefined,
): T {
  return levelAllowed(level, ceiling) ? level : (ceiling as T);
}

/**
 * Settings' read of the company's limits: reading the company's state
 * contacts nothing; with no company (or a server without it) there are none.
 */
export function useCompanyLimits(
  controller: Pick<ClientController, 'company'> | null | undefined,
): CompanyLimits | null {
  const [limits, setLimits] = useState<CompanyLimits | null>(null);
  useEffect(() => {
    let current = true;
    const read = async () => {
      try {
        const view = await controller?.company?.('status');
        if (current) setLimits(view?.limits ?? null);
      } catch {
        if (current) setLimits(null);
      }
    };
    void read();
    return () => {
      current = false;
    };
  }, [controller]);
  return limits;
}
