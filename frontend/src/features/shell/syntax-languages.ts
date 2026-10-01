/** Fence languages the lazy highlighter bundles, with aliases and labels. */
export const LANGUAGES = [
  'javascript',
  'typescript',
  'jsx',
  'tsx',
  'json',
  'python',
  'shellscript',
  'powershell',
  'sql',
  'html',
  'css',
  'scss',
  'xml',
  'yaml',
  'toml',
  'ini',
  'markdown',
  'diff',
  'dockerfile',
  'go',
  'rust',
  'java',
  'kotlin',
  'swift',
  'c',
  'cpp',
  'csharp',
  'php',
  'ruby',
  'lua',
  'r',
  'graphql',
] as const;

const ALIASES: Record<string, string> = {
  js: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  node: 'javascript',
  ts: 'typescript',
  mts: 'typescript',
  cts: 'typescript',
  py: 'python',
  python3: 'python',
  sh: 'shellscript',
  bash: 'shellscript',
  zsh: 'shellscript',
  shell: 'shellscript',
  console: 'shellscript',
  ps: 'powershell',
  ps1: 'powershell',
  pwsh: 'powershell',
  htm: 'html',
  svg: 'xml',
  yml: 'yaml',
  md: 'markdown',
  patch: 'diff',
  docker: 'dockerfile',
  golang: 'go',
  rs: 'rust',
  kt: 'kotlin',
  'c++': 'cpp',
  cc: 'cpp',
  hpp: 'cpp',
  h: 'c',
  cs: 'csharp',
  'c#': 'csharp',
  rb: 'ruby',
  gql: 'graphql',
  jsonc: 'json',
  json5: 'json',
};

const LABELS: Record<string, string> = {
  javascript: 'JavaScript',
  typescript: 'TypeScript',
  jsx: 'JSX',
  tsx: 'TSX',
  json: 'JSON',
  python: 'Python',
  shellscript: 'Shell',
  powershell: 'PowerShell',
  sql: 'SQL',
  html: 'HTML',
  css: 'CSS',
  scss: 'SCSS',
  xml: 'XML',
  yaml: 'YAML',
  toml: 'TOML',
  ini: 'INI',
  markdown: 'Markdown',
  diff: 'Diff',
  dockerfile: 'Dockerfile',
  go: 'Go',
  rust: 'Rust',
  java: 'Java',
  kotlin: 'Kotlin',
  swift: 'Swift',
  c: 'C',
  cpp: 'C++',
  csharp: 'C#',
  php: 'PHP',
  ruby: 'Ruby',
  lua: 'Lua',
  r: 'R',
  graphql: 'GraphQL',
};

/** The bundled grammar for a fence info string, or null for plain text. */
export function resolveLanguage(language: string): string | null {
  const key = language.trim().toLowerCase().split(/[\s{]/, 1)[0];
  const id = ALIASES[key] ?? key;
  return (LANGUAGES as readonly string[]).includes(id) ? id : null;
}

export function languageLabel(language: string): string {
  const id = resolveLanguage(language);
  if (id) return LABELS[id] ?? id;
  const trimmed = language.trim();
  return trimmed ? trimmed : 'Plain text';
}

const EXTENSIONS: Record<string, string> = {
  dockerfile: 'dockerfile',
  makefile: 'shellscript',
  txt: '',
  log: '',
  csv: '',
};

/** A fence language for an attached file, from its name or media type. */
export function languageForFile(name: string, mime: string): string {
  const base = name.trim().toLowerCase();
  const extension = base.includes('.') ? base.split('.').pop()! : base;
  if (Object.hasOwn(EXTENSIONS, extension)) return EXTENSIONS[extension];
  if (resolveLanguage(extension)) return extension;
  const type = mime.split(';', 1)[0].trim().toLowerCase();
  const subtype = type.split('/').pop() ?? '';
  const guess = subtype.replace(/^x-/, '');
  return resolveLanguage(guess) ? guess : '';
}
