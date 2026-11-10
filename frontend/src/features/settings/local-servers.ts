export type LocalServer = {
  profile: string;
  name: string;
  url: string;
  /** Why the address isn't the server's usual one, when it was moved. */
  note?: string;
};

/** Model servers people run on this computer, at the address each listens on unless it was moved. */
const USUAL: readonly LocalServer[] = [
  { profile: 'lmstudio', name: 'LM Studio', url: 'http://localhost:1234/v1' },
  { profile: 'vllm', name: 'vLLM', url: 'http://localhost:8000/v1' },
  { profile: 'llama_cpp', name: 'llama.cpp', url: 'http://localhost:8080/v1' },
];

/** The port this Row-Bot answers on: the page's own. */
function ownPort(origin: Pick<Location, 'port' | 'protocol'>) {
  return origin.port || (origin.protocol === 'https:' ? '443' : '80');
}

/**
 * The servers at their usual addresses, except one whose usual port is
 * Row-Bot's own (llama.cpp's 8080 is Row-Bot's default too): that address is
 * Row-Bot itself, so the next port is suggested and its note says why.
 */
export function localServers(
  origin: Pick<Location, 'port' | 'protocol'> = window.location,
): readonly LocalServer[] {
  const own = ownPort(origin);
  return USUAL.map((server) => {
    const url = new URL(server.url);
    if (url.port !== own) return server;
    url.port = String(Number(own) + 1);
    return {
      ...server,
      url: url.href,
      note: `Row-Bot itself uses port ${own}, so start ${server.name} on another port, such as ${url.port}, and enter its address here.`,
    };
  });
}

export const localServer = (profile: string | null | undefined) =>
  localServers().find((server) => server.profile === profile);
