/** Model servers people run on this computer, at the address each listens on unless it was moved. */
export const LOCAL_SERVERS: readonly {
  profile: string;
  name: string;
  url: string;
}[] = [
  { profile: 'lmstudio', name: 'LM Studio', url: 'http://localhost:1234/v1' },
  { profile: 'vllm', name: 'vLLM', url: 'http://localhost:8000/v1' },
  { profile: 'llama_cpp', name: 'llama.cpp', url: 'http://localhost:8080/v1' },
];

export const localServer = (profile: string | null | undefined) =>
  LOCAL_SERVERS.find((server) => server.profile === profile);
