import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Activity, Brain, GitBranch } from 'lucide-react';
import { useClientState } from '../../runtime';
import { Button, Tabs } from '../../ui/primitives';
import TaskLibrary from '../tasks/TaskLibrary';

const homeTabs = ['workflows', 'knowledge', 'monitor'];

export default function Home() {
  const state = useClientState();
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const requestedTab = search.get('tab')?.toLowerCase() ?? '';
  const [tab, setTab] = useState(
    homeTabs.includes(requestedTab) ? requestedTab : 'workflows',
  );
  const identity =
    state.status === 'ready' && state.handshake
      ? `${state.handshake.instance_id}:${state.handshake.client_session_id}`
      : null;
  return (
    <div className="home-view">
      <h1 className="visually-hidden">Home</h1>
      {identity ? (
        <p role="status" className="home-connection-status">
          Connected · local workspace
        </p>
      ) : (
        <p role="status" className="home-connection-status">
          {state.status === 'loading' || state.status === 'reconnecting'
            ? 'Connecting to your workspace…'
            : 'Connect to open your workflows.'}
        </p>
      )}
      <Tabs
        label="Home capabilities"
        value={tab}
        onChange={setTab}
        items={[
          {
            id: 'workflows',
            label: (
              <>
                <GitBranch size={17} aria-hidden />
                Workflows
              </>
            ),
            content: (
              <div className="home-workflows">
                <TaskLibrary />
              </div>
            ),
          },
          {
            id: 'knowledge',
            label: (
              <>
                <Brain size={17} aria-hidden />
                Knowledge
              </>
            ),
            content: (
              <section className="home-domain-boundary" aria-label="Knowledge">
                <Brain size={28} aria-hidden />
                <div>
                  <h2>Knowledge</h2>
                  <p>
                    Browse saved knowledge, documents, and the local Wiki Vault
                    through their migrated Settings owners.
                  </p>
                </div>
                <Button
                  variant="primary"
                  onClick={() => navigate('/settings/knowledge')}
                >
                  Open Knowledge settings
                </Button>
              </section>
            ),
          },
          {
            id: 'monitor',
            label: (
              <>
                <Activity size={17} aria-hidden />
                Monitor
              </>
            ),
            content: (
              <section className="home-domain-boundary" aria-label="Monitor">
                <Activity size={28} aria-hidden />
                <div>
                  <h2>Monitor</h2>
                  <p>
                    Activity monitoring remains in the current local
                    application. Opening this tab does not start or stop work.
                  </p>
                </div>
                <a className="button primary" href="/">
                  Open Monitor in current application
                </a>
              </section>
            ),
          },
        ]}
      />
    </div>
  );
}
