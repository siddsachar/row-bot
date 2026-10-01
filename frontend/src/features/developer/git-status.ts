export type Tracking = {
  upstream: string;
  ahead: number;
  behind: number;
  /** A branch with no commits yet ("No commits yet on main"). */
  unborn: boolean;
};

/** Reads `git status -sb`'s first line ("## main...origin/main [ahead 1]"). */
export function parseTracking(summary: string): Tracking {
  const line = summary.replace(/^##\s*/, '').trim();
  const unborn = /^(?:No commits yet on|Initial commit on)\s/i.test(line);
  const upstream = /\.\.\.(\S+)/.exec(line)?.[1] ?? '';
  const ahead = Number(/\bahead (\d+)/.exec(line)?.[1] ?? 0);
  const behind = Number(/\bbehind (\d+)/.exec(line)?.[1] ?? 0);
  return { upstream, ahead, behind, unborn };
}
