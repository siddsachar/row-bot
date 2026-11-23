import { useState } from 'react';
import {
  Search,
  ShieldMinus,
  ShieldPlus,
  UserCheck,
  UserX,
} from 'lucide-react';
import {
  EntityList,
  EntityRow,
  IconButton,
  InlineEmpty,
  Input,
} from '../../ui/primitives';
import { When } from '../../ui/When';
import { useNotify, useOverlay } from '../../ui/overlays';
import { request } from '../api';
import { useDirectory } from '../directory';
import { Page } from '../page';
import type { Person } from '../types';
import { problem } from '../words';

type Change = {
  path: string;
  method: 'PUT' | 'POST';
  body?: unknown;
  title: string;
  description: string;
  confirmLabel: string;
  /** The action's name on its button: "Suspend Ada". */
  label: string;
  done: string;
};

function nameOf(person: Person) {
  return person.display_name || person.email || 'This person';
}

/** The change an action makes, with the words its confirmation uses. */
function adminChange(person: Person): Change {
  const name = nameOf(person);
  return person.role === 'admin'
    ? {
        path: `/api/admin/people/${person.id}/role`,
        method: 'PUT',
        body: { role: 'member' },
        title: `Remove ${name}'s admin role?`,
        description: `${name} keeps using Row-Bot but can no longer open this console.`,
        confirmLabel: 'Remove admin',
        label: `Remove ${name}'s admin role`,
        done: `${name} is no longer an admin`,
      }
    : {
        path: `/api/admin/people/${person.id}/role`,
        method: 'PUT',
        body: { role: 'admin' },
        title: `Make ${name} an admin?`,
        description: `${name} can then manage people, devices, models, policy and the audit log here.`,
        confirmLabel: 'Make admin',
        label: `Make ${name} an admin`,
        done: `${name} is now an admin`,
      };
}

function suspendChange(person: Person): Change {
  const name = nameOf(person);
  return person.suspended
    ? {
        path: `/api/admin/people/${person.id}/resume`,
        method: 'POST',
        title: `Resume ${name}?`,
        description: `${name} can sign in and use company models again.`,
        confirmLabel: 'Resume',
        label: `Resume ${name}`,
        done: `${name} can sign in again`,
      }
    : {
        path: `/api/admin/people/${person.id}/suspend`,
        method: 'POST',
        title: `Suspend ${name}?`,
        description: `Suspending signs ${name} out everywhere. They can't sign in or use company models until you resume them.`,
        confirmLabel: 'Suspend',
        label: `Suspend ${name}`,
        done: `${name} is suspended`,
      };
}

function matches(person: Person, search: string) {
  const text = search.trim().toLowerCase();
  return (
    !text ||
    [person.display_name, person.email ?? '', ...person.groups].some((value) =>
      value.toLowerCase().includes(text),
    )
  );
}

export default function PeoplePage() {
  const { me, people, replace } = useDirectory();
  const overlay = useOverlay();
  const notify = useNotify();
  const [search, setSearch] = useState('');
  const shown = people.filter((person) => matches(person, search));

  function confirm(change: Change) {
    overlay.open({
      kind: 'alert',
      title: change.title,
      description: change.description,
      confirmLabel: change.confirmLabel,
      onConfirm: () => {
        request<Person>(change.path, {
          method: change.method,
          body: change.body,
        }).then(
          (person) => {
            replace(person);
            notify(change.done);
          },
          (error: unknown) => notify(problem(error), 'danger'),
        );
      },
    });
  }

  return (
    <Page
      title="People"
      description="Everyone who has signed in to your company's Row-Bot server, with their groups from your identity provider."
      action={
        <label className="console-search">
          <Search size={16} aria-hidden />
          <Input
            type="search"
            aria-label="Search people"
            placeholder="Name, email or group"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
      }
    >
      {shown.length === 0 ? (
        <InlineEmpty>No one matches “{search.trim()}”.</InlineEmpty>
      ) : (
        <EntityList label="People">
          {shown.map((person) => {
            const self = person.id === me.id;
            const viaGroup = person.admin && person.role !== 'admin';
            const admin = adminChange(person);
            const suspend = suspendChange(person);
            const adminLabel = self
              ? "You can't change your own admin role"
              : viaGroup
                ? 'Admin through a provider group: change it in your identity provider'
                : admin.label;
            const suspendLabel = self
              ? "You can't suspend yourself"
              : suspend.label;
            return (
              <EntityRow
                key={person.id}
                title={nameOf(person)}
                status={
                  person.suspended
                    ? { tone: 'warning', label: 'Suspended' }
                    : person.admin
                      ? { tone: 'accent', label: 'Admin' }
                      : undefined
                }
                meta={
                  <>
                    {[
                      person.email,
                      person.groups.length
                        ? person.groups.join(', ')
                        : 'No groups',
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                    {' · Signed in '}
                    <When value={person.last_sign_in_at} />
                  </>
                }
                action={
                  <>
                    <IconButton
                      size="sm"
                      label={adminLabel}
                      aria-disabled={self || viaGroup || undefined}
                      onClick={() => {
                        if (!self && !viaGroup) confirm(admin);
                      }}
                    >
                      {person.role === 'admin' ? (
                        <ShieldMinus size={16} aria-hidden />
                      ) : (
                        <ShieldPlus size={16} aria-hidden />
                      )}
                    </IconButton>
                    <IconButton
                      size="sm"
                      label={suspendLabel}
                      aria-disabled={self || undefined}
                      onClick={() => {
                        if (!self) confirm(suspend);
                      }}
                    >
                      {person.suspended ? (
                        <UserCheck size={16} aria-hidden />
                      ) : (
                        <UserX size={16} aria-hidden />
                      )}
                    </IconButton>
                  </>
                }
              />
            );
          })}
        </EntityList>
      )}
    </Page>
  );
}
