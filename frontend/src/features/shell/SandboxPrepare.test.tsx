import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { SandboxJobView, SandboxStatusView } from '../../api/types';
import { SandboxPrepareOffer, SandboxReadiness } from './SandboxPrepare';
import { cardOf, tracedCards } from './TranscriptCards';

const controller = {
  conversationSandbox: vi.fn(),
  prepareSandbox: vi.fn(),
  pullSandboxImage: vi.fn(),
  sandboxJob: vi.fn(),
  stopSandboxJob: vi.fn(),
};
const runtime = { controller };
vi.mock('../../runtime', () => ({ useRuntime: () => runtime }));

const plan = {
  summary: 'pytest and the project with its test extra',
  sources: ['pyproject.toml'],
  commands: ["python -m pip install --user -e '.[test]' pytest"],
};
const status = (patch: Partial<SandboxStatusView> = {}): SandboxStatusView => ({
  workspace_name: 'tide-app',
  execution_mode: 'docker',
  network: 'off',
  image: 'nikolaik/python-nodejs:python3.11-nodejs20',
  image_download: 'about 570 MB',
  runtime: 'ready',
  runtime_detail: '',
  image_present: true,
  prepare: plan,
  job: null,
  ...patch,
});
const job = (patch: Partial<SandboxJobView> = {}): SandboxJobView => ({
  job_id: 'prep-0a1b2c3d',
  conversation_id: 'conversation-a',
  workspace_name: 'tide-app',
  kind: 'prepare',
  status: 'installing',
  summary: plan.summary,
  sources: plan.sources,
  commands: plan.commands,
  image: 'nikolaik/python-nodejs:python3.11-nodejs20',
  started_at: new Date().toISOString(),
  ended_at: null,
  seconds: 3,
  network: 'on',
  network_on_at: new Date().toISOString(),
  network_off_at: null,
  network_off_confirmed: false,
  process_id: 'proc-0000abcd',
  progress: 'Collecting pytest',
  by: 'you',
  stopped_by: '',
  problem: '',
  detail: '',
  ...patch,
});

beforeEach(() => {
  for (const mock of Object.values(controller)) mock.mockReset();
  controller.conversationSandbox.mockResolvedValue(status());
});

async function click(name: string) {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name }));
  });
}

it('after a test run that lacked pytest, Prepare sandbox asks first, then shows the install with the network on', async () => {
  controller.prepareSandbox.mockResolvedValue(job());
  controller.sandboxJob.mockResolvedValue(job());
  await act(async () => {
    render(
      <SandboxPrepareOffer
        conversationId="conversation-a"
        reason="pytest_missing"
      />,
    );
  });
  const offer = screen.getByRole('group', { name: "Tests couldn't run" });
  expect(offer).toHaveTextContent(
    "Tests couldn't run: pytest isn't installed in the sandbox",
  );
  await click('Prepare sandbox');
  // Asked first: what happens to the network, and what it installs.
  expect(
    screen.getByRole('group', { name: 'Confirm Prepare sandbox' }),
  ).toHaveTextContent(
    'Prepare the sandbox? Row-Bot turns the sandbox network on for the install only, installs pytest and the project with its test extra (from pyproject.toml), then turns the network off again.',
  );
  expect(controller.prepareSandbox).not.toHaveBeenCalled();
  await click('Prepare sandbox');
  expect(controller.prepareSandbox).toHaveBeenCalledWith('conversation-a');
  const card = await screen.findByRole('group', {
    name: 'Preparing the sandbox: Installing',
  });
  expect(card).toHaveTextContent('Network on for this install');
  expect(card).toHaveTextContent('Collecting pytest');
  controller.stopSandboxJob.mockResolvedValue(
    job({
      status: 'stopped',
      stopped_by: 'you',
      network: 'off',
      network_off_at: new Date().toISOString(),
      network_off_confirmed: true,
    }),
  );
  await click('Stop');
  expect(controller.stopSandboxJob).toHaveBeenCalledWith('prep-0a1b2c3d');
  expect(
    screen.getByRole('group', { name: 'Prepare stopped: Stopped' }),
  ).toHaveTextContent('Network off again');
});

it('says why when approvals block it', async () => {
  controller.prepareSandbox.mockRejectedValue({
    code: 'sandbox_prepare_blocked',
  });
  await act(async () => {
    render(
      <SandboxPrepareOffer
        conversationId="conversation-a"
        reason="packages_missing"
      />,
    );
  });
  expect(
    screen.getByRole('group', { name: "Tests couldn't run" }),
  ).toHaveTextContent(
    "Tests couldn't run: the project's packages aren't installed in the sandbox",
  );
  await click('Prepare sandbox');
  await click('Prepare sandbox');
  expect(screen.getByRole('alert')).toHaveTextContent(
    "Approvals are set to Block in this chat, so the sandbox can't be prepared. Change Approvals to prepare it.",
  );
});

it('once the sandbox was prepared, the offer shows that instead', async () => {
  const done = job({
    status: 'ready',
    network: 'off',
    network_off_at: new Date().toISOString(),
    network_off_confirmed: true,
    seconds: 41,
  });
  controller.conversationSandbox.mockResolvedValue(status({ job: done }));
  controller.sandboxJob.mockResolvedValue(done);
  await act(async () => {
    render(
      <SandboxPrepareOffer
        conversationId="conversation-a"
        reason="pytest_missing"
      />,
    );
  });
  const card = screen.getByRole('group', {
    name: 'Sandbox ready for tests: Ready',
  });
  expect(card).toHaveTextContent(
    'Installed pytest and the project with its test extra. The tests can run now.',
  );
  expect(card).toHaveTextContent('Network off again');
  expect(screen.queryByRole('button', { name: 'Prepare sandbox' })).toBeNull();
});

it('in Docker mode without the image, the sandbox offers to download it, with its size', async () => {
  controller.conversationSandbox.mockResolvedValue(
    status({ image_present: false }),
  );
  controller.pullSandboxImage.mockResolvedValue(
    job({
      kind: 'pull',
      status: 'pulling',
      network: 'off',
      network_on_at: null,
      network_off_confirmed: true,
      summary: 'the sandbox image',
      progress: '',
    }),
  );
  controller.sandboxJob.mockImplementation(async () =>
    job({
      kind: 'pull',
      status: 'pulling',
      network: 'off',
      network_on_at: null,
      network_off_confirmed: true,
      progress: '',
    }),
  );
  await act(async () => {
    render(<SandboxReadiness conversationId="conversation-a" />);
  });
  const offer = screen.getByRole('group', { name: 'Sandbox image' });
  expect(offer).toHaveTextContent('Download the sandbox image?');
  expect(offer).toHaveTextContent('about 570 MB from Docker Hub');
  await click('Download image');
  expect(controller.pullSandboxImage).toHaveBeenCalledWith('conversation-a');
  const card = await screen.findByRole('group', {
    name: 'Downloading the sandbox image: Downloading',
  });
  expect(card).toHaveTextContent(
    'Docker downloads it; the sandbox network stays off',
  );
});

it('with the image here, the sandbox offers Prepare only, and nothing for a code folder on this computer', async () => {
  await act(async () => {
    render(<SandboxReadiness conversationId="conversation-a" />);
  });
  expect(screen.queryByRole('group', { name: 'Sandbox image' })).toBeNull();
  expect(
    screen.getByRole('group', { name: 'Sandbox readiness' }),
  ).toHaveTextContent(
    'Prepare for tests. Installs pytest and the project with its test extra with the network on only for the install, then off again.',
  );
  await click('Prepare sandbox');
  expect(
    screen.getByRole('group', { name: 'Confirm Prepare sandbox' }),
  ).toBeVisible();
});

it('says when Docker is not running', async () => {
  controller.conversationSandbox.mockResolvedValue(
    status({
      runtime: 'unavailable',
      image_present: null,
      runtime_detail:
        "Docker isn't running on this computer. Start Docker Desktop (or Docker), then check again.",
    }),
  );
  await act(async () => {
    render(<SandboxReadiness conversationId="conversation-a" />);
  });
  expect(
    screen.getByText(/Docker isn't running on this computer/),
  ).toBeVisible();
  await click('Check again');
  expect(controller.conversationSandbox).toHaveBeenCalledTimes(2);
});

it('the chat shows the offer under a run that lacked its tools, unless the step went on to prepare', () => {
  expect(
    cardOf({
      kind: 'sandbox_unprepared',
      error_code: 'pytest_missing',
    } as never),
  ).toEqual({
    kind: 'sandbox_offer',
    reason: 'pytest_missing',
  });
  const item = (specialization: object) => ({ specialization }) as never;
  const offerOnly = tracedCards([
    {
      items: [
        item({ kind: 'sandbox_unprepared', error_code: 'pytest_missing' }),
      ],
    },
  ] as never);
  expect(offerOnly.map((card) => card.kind)).toEqual(['sandbox_offer']);
  const prepared = tracedCards([
    {
      items: [
        item({ kind: 'sandbox_unprepared', error_code: 'pytest_missing' }),
        item({ kind: 'sandbox_job', job_id: 'prep-0a1b2c3d' }),
      ],
    },
  ] as never);
  expect(prepared.map((card) => card.kind)).toEqual(['sandbox_job']);
});
