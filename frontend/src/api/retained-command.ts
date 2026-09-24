/** Store only an opaque command ID so a lost response can be checked after reload. */
export function readRetainedCommand(key: string): string {
  try {
    const value = sessionStorage.getItem(`row-bot:command:${key}`) ?? '';
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      value,
    )
      ? value
      : '';
  } catch {
    return '';
  }
}

export function retainCommand(key: string, commandId: string): void {
  try {
    const name = `row-bot:command:${key}`;
    if (commandId) sessionStorage.setItem(name, commandId);
    else sessionStorage.removeItem(name);
  } catch {
    // Storage may be blocked; the current mounted session still retains it.
  }
}
