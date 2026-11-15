// @vitest-environment-options { "url": "http://127.0.0.1:8080/app-v2/" }
import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ProviderConfiguration from './ProviderConfiguration';
import { localServers } from './local-servers';
import type { ProviderConfigurationPage } from '../../api/types';

const page: ProviderConfigurationPage = {
  schema_version: 1,
  revision: 'a'.repeat(64),
  items: [],
  total: 0,
  next_cursor: null,
  profiles: [
    { id: 'generic_openai', name: 'OpenAI-compatible' },
    { id: 'llama_cpp', name: 'llama.cpp' },
    { id: 'vllm', name: 'vLLM' },
  ],
};

it('never suggests Row-Bot’s own address for a server on this computer', () => {
  expect(
    localServers({ port: '8080', protocol: 'http:' }).map(({ url }) => url),
  ).toEqual([
    'http://localhost:1234/v1',
    'http://localhost:8000/v1',
    'http://localhost:8081/v1',
  ]);
  // Row-Bot moved off 8080: llama.cpp keeps its usual address.
  expect(localServers({ port: '8090', protocol: 'http:' })).toContainEqual({
    profile: 'llama_cpp',
    name: 'llama.cpp',
    url: 'http://localhost:8080/v1',
  });
});

it('opens llama.cpp on another port when Row-Bot is on 8080, and says why', async () => {
  render(
    <ProviderConfiguration
      load={vi.fn().mockResolvedValue(page)}
      review={vi.fn()}
      apply={vi.fn()}
      receipt={vi.fn()}
      onSaved={vi.fn()}
      onCredentials={vi.fn()}
      compact
      autoAdd
      addProfile="llama_cpp"
    />,
  );
  expect(await screen.findByLabelText('Server type')).toHaveValue('llama_cpp');
  expect(screen.getByLabelText(/^Base URL/)).toHaveValue(
    'http://localhost:8081/v1',
  );
  expect(
    screen.getByText(
      'Row-Bot itself uses port 8080, so start llama.cpp on another port, such as 8081, and enter its address here.',
    ),
  ).toBeVisible();
});
