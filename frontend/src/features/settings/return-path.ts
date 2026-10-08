/** Where Close settings returns: the last place outside Settings (Home when Settings opened first). */
let path = '/';

export function settingsReturnPath(): string {
  return path;
}

export function rememberOutsideSettings(next: string): void {
  path = next || '/';
}
