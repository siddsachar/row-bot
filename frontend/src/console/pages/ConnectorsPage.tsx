import { useState } from 'react';
import {
  Building2,
  HardDrive,
  KeyRound,
  Link2,
  Pause,
  Pencil,
  Play,
  PlugZap,
  Plus,
  RefreshCw,
  Trash2,
} from 'lucide-react';
import {
  Button,
  EmptyState,
  EntityList,
  EntityRow,
  IconButton,
  StatusDot,
  type MenuAction,
} from '../../ui/primitives';
import { When } from '../../ui/When';
import { useNotify, useOverlay } from '../../ui/overlays';
import { request } from '../api';
import { Loaded, useLoad } from '../load';
import { Page } from '../page';
import { problem } from '../words';
import { AddConnector } from './connectors/AddConnector';
import { Details, type TestShown } from './connectors/Details';
import {
  CredentialsDialog,
  PrincipalMapDialog,
  RenameDialog,
} from './connectors/dialogs';
import type {
  ConnectionTest,
  Connector,
  ConnectorSpace,
} from './connectors/types';
import { KINDS, SOURCE, STATES, needsALook, plural } from './connectors/words';
import './connectors/connectors.css';

type PageData = { connectors: Connector[]; spaces: ConnectorSpace[] };
type Dialog =
  | { kind: 'add' }
  | { kind: 'rename' | 'credentials'; connector: Connector }
  | { kind: 'map'; connector: Connector; focus: string | null };

const byName = (a: Connector, b: Connector) =>
  a.name.toLowerCase().localeCompare(b.name.toLowerCase()) ||
  a.id.localeCompare(b.id);

async function loadAll(): Promise<PageData> {
  const [{ connectors }, { spaces }] = await Promise.all([
    request<{ connectors: Connector[] }>('/api/admin/connectors'),
    request<{ spaces: ConnectorSpace[] }>('/api/admin/brain/spaces'),
  ]);
  return { connectors, spaces };
}

function KindIcon({ connector }: { connector: Connector }) {
  return connector.kind === 'microsoft' ? (
    <Building2 size={18} />
  ) : (
    <HardDrive size={18} />
  );
}

/** Why a connector can't sync now, as its Sync now button's name. */
function syncLabel(connector: Connector) {
  if (connector.state === 'paused')
    return `${connector.name} is paused: resume it to sync`;
  if (connector.state === 'error')
    return `${connector.name} stopped: resume it, or replace its credentials, to sync`;
  return `Sync ${connector.name} now`;
}

export default function ConnectorsPage() {
  const overlay = useOverlay();
  const notify = useNotify();
  const load = useLoad(loadAll);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [tests, setTests] = useState<Record<string, TestShown>>({});

  function put(connector: Connector) {
    load.update((data) => ({
      ...data,
      connectors: [
        ...data.connectors.filter((item) => item.id !== connector.id),
        connector,
      ].sort(byName),
    }));
  }

  function removed(id: string) {
    load.update((data) => ({
      ...data,
      connectors: data.connectors.filter((item) => item.id !== id),
    }));
  }

  async function test(connector: Connector) {
    setExpanded((current) => ({ ...current, [connector.id]: true }));
    setTests((current) => ({ ...current, [connector.id]: 'testing' }));
    let result: ConnectionTest;
    try {
      result = await request<ConnectionTest>(
        `/api/admin/connectors/${connector.id}/test`,
        { method: 'POST' },
      );
    } catch (error) {
      result = { ok: false, problem: problem(error) };
    }
    setTests((current) => ({ ...current, [connector.id]: result }));
  }

  function act(
    connector: Connector,
    path: 'pause' | 'resume' | 'sync',
    done: string,
  ) {
    request<Connector>(`/api/admin/connectors/${connector.id}/${path}`, {
      method: 'POST',
    }).then(
      (answer) => {
        put(answer);
        notify(done);
      },
      (error: unknown) => notify(problem(error), 'danger'),
    );
  }

  function confirmPause(connector: Connector) {
    overlay.open({
      kind: 'alert',
      title: `Pause ${connector.name}?`,
      description:
        'Nothing syncs until you resume it. Its files keep their last permissions meanwhile.',
      confirmLabel: 'Pause',
      onConfirm: () => act(connector, 'pause', `${connector.name} is paused`),
    });
  }

  function confirmResume(connector: Connector) {
    overlay.open({
      kind: 'alert',
      title: `Resume ${connector.name}?`,
      description:
        'It syncs again now, as you: the files it reads are added in your name, and their embeddings count as your model use.',
      confirmLabel: 'Resume',
      onConfirm: () =>
        act(connector, 'resume', `${connector.name} is syncing again`),
    });
  }

  function confirmDelete(connector: Connector, space: string) {
    overlay.open({
      kind: 'alert',
      title: `Delete ${connector.name}?`,
      description: `Removes its ${plural(connector.documents, 'document')} from ${space}. The files stay in ${SOURCE[connector.kind]}. This can’t be undone.`,
      confirmLabel: 'Delete',
      onConfirm: () => {
        request(`/api/admin/connectors/${connector.id}`, {
          method: 'DELETE',
        }).then(
          () => {
            removed(connector.id);
            notify(`${connector.name} is deleted`);
          },
          (error: unknown) => notify(problem(error), 'danger'),
        );
      },
    });
  }

  const add = (
    <Button variant="primary" onClick={() => setDialog({ kind: 'add' })}>
      <Plus size={16} aria-hidden />
      Add connector
    </Button>
  );

  return (
    <Page
      title="Connectors"
      description="SharePoint, OneDrive and Google Drive folders synced into knowledge spaces. Each file keeps its permissions: only the people who can open it at its source can find it in Row-Bot."
      action={
        load.status === 'ready' && load.data.connectors.length ? add : undefined
      }
    >
      <Loaded load={load} label="Loading connectors" what="connectors">
        {({ connectors, spaces }) => {
          const spaceName = (id: string) =>
            spaces.find((space) => space.id === id)?.name ??
            'A space no longer listed';
          if (!connectors.length)
            return (
              <EmptyState title="No connectors yet" action={add}>
                Connect SharePoint or Google Drive. Its files sync into a
                knowledge space and keep the permissions they have there.
              </EmptyState>
            );
          return (
            <EntityList label="Connectors">
              {connectors.map((connector) => {
                const space = spaceName(connector.space_id);
                const active = connector.state === 'active';
                const mappable =
                  connector.unmapped.length > 0 ||
                  Object.keys(connector.principal_map).length > 0;
                const menu: MenuAction[] = [
                  {
                    label: 'Test connection',
                    icon: <PlugZap size={16} />,
                    onSelect: () => void test(connector),
                  },
                  {
                    label: 'Rename',
                    icon: <Pencil size={16} />,
                    onSelect: () => undefined,
                    afterClose: () => setDialog({ kind: 'rename', connector }),
                  },
                  {
                    label: 'Replace credentials',
                    icon: <KeyRound size={16} />,
                    onSelect: () => undefined,
                    afterClose: () =>
                      setDialog({ kind: 'credentials', connector }),
                  },
                  ...(mappable
                    ? [
                        {
                          label: 'Map people and groups',
                          icon: <Link2 size={16} />,
                          onSelect: () => undefined,
                          afterClose: () =>
                            setDialog({ kind: 'map', connector, focus: null }),
                        },
                      ]
                    : []),
                  {
                    label: 'Delete',
                    icon: <Trash2 size={16} />,
                    danger: true,
                    onSelect: () => undefined,
                    afterClose: () => confirmDelete(connector, space),
                  },
                ];
                return (
                  <EntityRow
                    key={connector.id}
                    title={connector.name}
                    icon={<KindIcon connector={connector} />}
                    status={STATES[connector.state]}
                    meta={
                      <>
                        {`${KINDS[connector.kind]} · ${space} · `}
                        {connector.last_sync_at ? (
                          <>
                            {'Synced '}
                            <When value={connector.last_sync_at} />
                          </>
                        ) : (
                          'Not synced yet'
                        )}
                        {` · ${plural(connector.files, 'file')} · ${plural(
                          connector.documents,
                          'document',
                        )}`}
                        {needsALook(connector) && (
                          <>
                            {' '}
                            <StatusDot
                              tone="warning"
                              label="Needs a look"
                              showLabel
                              className="connector-badge"
                            />
                          </>
                        )}
                        {!active && connector.state_reason && (
                          <>
                            {' '}
                            <span className="connector-reason">
                              {connector.state_reason}
                            </span>
                          </>
                        )}
                      </>
                    }
                    action={
                      <>
                        <IconButton
                          size="sm"
                          label={syncLabel(connector)}
                          aria-disabled={!active || undefined}
                          onClick={() => {
                            if (active)
                              act(
                                connector,
                                'sync',
                                `${connector.name} is syncing now`,
                              );
                          }}
                        >
                          <RefreshCw size={16} aria-hidden />
                        </IconButton>
                        {active ? (
                          <IconButton
                            size="sm"
                            label={`Pause ${connector.name}`}
                            onClick={() => confirmPause(connector)}
                          >
                            <Pause size={16} aria-hidden />
                          </IconButton>
                        ) : (
                          <IconButton
                            size="sm"
                            label={`Resume ${connector.name}`}
                            onClick={() => confirmResume(connector)}
                          >
                            <Play size={16} aria-hidden />
                          </IconButton>
                        )}
                      </>
                    }
                    menu={menu}
                    menuLabel={`More for ${connector.name}`}
                    details={
                      <Details
                        connector={connector}
                        space={space}
                        test={tests[connector.id]}
                        onMap={(focus) =>
                          setDialog({ kind: 'map', connector, focus })
                        }
                      />
                    }
                    expanded={Boolean(expanded[connector.id])}
                    onExpandedChange={(open) =>
                      setExpanded((current) => ({
                        ...current,
                        [connector.id]: open,
                      }))
                    }
                  />
                );
              })}
            </EntityList>
          );
        }}
      </Loaded>
      {dialog?.kind === 'add' && load.status === 'ready' && (
        <AddConnector
          spaces={load.data.spaces}
          onClose={() => setDialog(null)}
          onSaved={put}
          onRemoved={removed}
        />
      )}
      {dialog?.kind === 'rename' && (
        <RenameDialog
          connector={dialog.connector}
          onClose={() => setDialog(null)}
          onSaved={(connector) => {
            put(connector);
            notify(`${connector.name} is saved`);
          }}
        />
      )}
      {dialog?.kind === 'credentials' && (
        <CredentialsDialog
          connector={dialog.connector}
          onClose={() => setDialog(null)}
          onSaved={(connector) => {
            put(connector);
            notify(`New credentials for ${connector.name} are saved`);
            void test(connector);
          }}
        />
      )}
      {dialog?.kind === 'map' && (
        <PrincipalMapDialog
          connector={dialog.connector}
          focus={dialog.focus}
          onClose={() => setDialog(null)}
          onSaved={(connector) => {
            put(connector);
            notify(
              `The map is saved. Row-Bot reads ${connector.name}’s permissions again.`,
            );
          }}
        />
      )}
    </Page>
  );
}
