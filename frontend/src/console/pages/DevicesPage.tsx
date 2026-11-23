import { useState } from 'react';
import { Laptop, Unplug } from 'lucide-react';
import {
  EmptyState,
  EntityList,
  EntityRow,
  Field,
  IconButton,
  InlineEmpty,
  Segmented,
  Select,
} from '../../ui/primitives';
import { When } from '../../ui/When';
import { useNotify, useOverlay } from '../../ui/overlays';
import { query, request } from '../api';
import { useDirectory } from '../directory';
import { Loaded, useLoad } from '../load';
import { Page } from '../page';
import type { Device } from '../types';
import { PLATFORMS, problem } from '../words';

type Shown = 'active' | 'revoked' | 'all';

export default function DevicesPage() {
  const { people, nameOf } = useDirectory();
  const overlay = useOverlay();
  const notify = useNotify();
  const [person, setPerson] = useState('');
  const [shown, setShown] = useState<Shown>('active');
  const filters = query({
    person: person || undefined,
    revoked: shown === 'all' ? undefined : String(shown === 'revoked'),
  });
  const devices = useLoad(
    () =>
      request<{ devices: Device[] }>(`/api/admin/devices${filters}`).then(
        (answer) => answer.devices,
      ),
    filters,
  );
  const filtered = Boolean(person) || shown === 'revoked';

  function revoke(device: Device) {
    const owner = nameOf(device.principal_id);
    overlay.open({
      kind: 'alert',
      title: `Revoke ${owner}'s ${device.name}?`,
      description: `Row-Bot on this device can no longer reach the company server. ${owner} can still sign in on their other devices.`,
      confirmLabel: 'Revoke',
      onConfirm: () => {
        request<Device>(`/api/admin/devices/${device.id}/revoke`, {
          method: 'POST',
        }).then(
          (revoked) => {
            devices.update((list) =>
              shown === 'active'
                ? list.filter((item) => item.id !== revoked.id)
                : list.map((item) => (item.id === revoked.id ? revoked : item)),
            );
            notify(`${device.name} is revoked`);
          },
          (error: unknown) => notify(problem(error), 'danger'),
        );
      },
    });
  }

  return (
    <Page
      title="Devices"
      description="The Row-Bots people have joined to the company. Revoke one that is lost or no longer used."
    >
      <div className="console-filters">
        <Field label="Person">
          <Select
            value={person}
            onChange={(event) => setPerson(event.target.value)}
          >
            <option value="">Everyone</option>
            {people.map((item) => (
              <option key={item.id} value={item.id}>
                {item.display_name || item.email}
              </option>
            ))}
          </Select>
        </Field>
        <div className="field">
          <span className="field-label">Show</span>
          <Segmented<Shown>
            label="Show"
            value={shown}
            onChange={setShown}
            options={[
              { value: 'active', label: 'Active' },
              { value: 'revoked', label: 'Revoked' },
              { value: 'all', label: 'All' },
            ]}
          />
        </div>
      </div>
      <Loaded load={devices} label="Loading devices" what="devices">
        {(list) =>
          list.length === 0 ? (
            filtered ? (
              <InlineEmpty icon={<Laptop size={16} />}>
                No devices match these filters.
              </InlineEmpty>
            ) : (
              <EmptyState
                title={
                  shown === 'active' ? 'No active devices' : 'No devices yet'
                }
              >
                A device appears here when someone joins their Row-Bot to the
                company.
              </EmptyState>
            )
          ) : (
            <EntityList label="Devices">
              {list.map((device) => (
                <EntityRow
                  key={device.id}
                  title={device.name}
                  icon={<Laptop size={18} />}
                  status={
                    device.revoked_at
                      ? { tone: 'danger', label: 'Revoked' }
                      : undefined
                  }
                  meta={
                    <>
                      {[
                        nameOf(device.principal_id),
                        PLATFORMS[device.platform] ?? device.platform,
                        `Row-Bot ${device.app_version}`,
                      ].join(' · ')}
                      {' · Last seen '}
                      <When value={device.last_seen_at} />
                    </>
                  }
                  action={
                    device.revoked_at ? undefined : (
                      <IconButton
                        size="sm"
                        label={`Revoke ${device.name}`}
                        onClick={() => revoke(device)}
                      >
                        <Unplug size={16} aria-hidden />
                      </IconButton>
                    )
                  }
                />
              ))}
            </EntityList>
          )
        }
      </Loaded>
    </Page>
  );
}
