import { useState } from 'react';
import { Pencil, Plus, Trash2, User, Users } from 'lucide-react';
import {
  Button,
  EmptyState,
  EntityList,
  EntityRow,
  Field,
  IconButton,
  Input,
  Segmented,
  Select,
} from '../../../ui/primitives';
import { useNotify, useOverlay } from '../../../ui/overlays';
import { request } from '../../api';
import { useDirectory } from '../../directory';
import { FormDialog } from '../../form-dialog';
import { Loaded, useLoad } from '../../load';
import type { Budget } from '../../types';
import { amount, problem } from '../../words';

function limits(budget: Budget) {
  return [
    budget.soft_limit !== null && `Warns at ${amount(budget.soft_limit)}`,
    budget.hard_limit !== null && `Stops at ${amount(budget.hard_limit)}`,
  ]
    .filter(Boolean)
    .join(' · ');
}

const limitValue = (text: string) => (text.trim() === '' ? null : Number(text));

function BudgetForm({
  budget,
  onClose,
  onSaved,
}: {
  budget: Budget | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { people, groups } = useDirectory();
  const [scope, setScope] = useState<Budget['scope']>(budget?.scope ?? 'group');
  const [subject, setSubject] = useState(budget?.subject ?? '');
  const [soft, setSoft] = useState(
    budget?.soft_limit == null ? '' : String(budget.soft_limit),
  );
  const [hard, setHard] = useState(
    budget?.hard_limit == null ? '' : String(budget.hard_limit),
  );

  async function save() {
    await request('/api/admin/budgets', {
      method: 'PUT',
      body: {
        scope,
        subject: subject.trim(),
        soft_limit: limitValue(soft),
        hard_limit: limitValue(hard),
      },
    });
    onSaved();
  }

  return (
    <FormDialog
      title={budget ? 'Edit budget' : 'Add a budget'}
      description="A monthly limit on what model calls may cost, for one person or a group. It starts again on the first of each month."
      submitLabel={budget ? 'Save' : 'Add budget'}
      onClose={onClose}
      onSubmit={save}
    >
      {!budget && (
        <div className="field">
          <span className="field-label">For</span>
          <Segmented<Budget['scope']>
            label="For"
            value={scope}
            onChange={(value) => {
              setScope(value);
              setSubject('');
            }}
            options={[
              { value: 'group', label: 'A group' },
              { value: 'person', label: 'A person' },
            ]}
          />
        </div>
      )}
      {scope === 'person' ? (
        <Field label="Person">
          <Select
            required
            disabled={Boolean(budget)}
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
          >
            <option value="" disabled>
              Choose a person
            </option>
            {people.map((person) => (
              <option key={person.id} value={person.id}>
                {person.display_name || person.email}
              </option>
            ))}
          </Select>
        </Field>
      ) : (
        <Field label="Group" hint="A group from your identity provider.">
          <Input
            required
            list="console-budget-groups"
            readOnly={Boolean(budget)}
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
          />
        </Field>
      )}
      <datalist id="console-budget-groups">
        {groups.map((group) => (
          <option key={group} value={group} />
        ))}
      </datalist>
      <div className="console-form-pair">
        <Field label="Warn at" hint="Row-Bot tells them; calls go on.">
          <Input
            type="number"
            min={0}
            step="0.01"
            value={soft}
            onChange={(event) => setSoft(event.target.value)}
          />
        </Field>
        <Field label="Stop at" hint="Calls are refused until next month.">
          <Input
            type="number"
            min={0}
            step="0.01"
            value={hard}
            onChange={(event) => setHard(event.target.value)}
          />
        </Field>
      </div>
    </FormDialog>
  );
}

export function Budgets() {
  const { nameOf } = useDirectory();
  const overlay = useOverlay();
  const notify = useNotify();
  const budgets = useLoad(() =>
    request<{ budgets: Budget[] }>('/api/admin/budgets').then(
      (answer) => answer.budgets,
    ),
  );
  const [editing, setEditing] = useState<Budget | 'new' | null>(null);
  const subjectName = (budget: Budget) =>
    budget.scope === 'person' ? nameOf(budget.subject) : budget.subject;

  function remove(budget: Budget) {
    overlay.open({
      kind: 'alert',
      title: `Remove the budget for ${subjectName(budget)}?`,
      description: 'Their model calls are no longer limited by it.',
      confirmLabel: 'Remove',
      onConfirm: () => {
        request(`/api/admin/budgets/${budget.id}`, { method: 'DELETE' }).then(
          () => {
            budgets.update((list) =>
              list.filter((item) => item.id !== budget.id),
            );
            notify('The budget is removed');
          },
          (error: unknown) => notify(problem(error), 'danger'),
        );
      },
    });
  }

  const add = (
    <Button variant="primary" onClick={() => setEditing('new')}>
      <Plus size={16} aria-hidden />
      Add budget
    </Button>
  );
  return (
    <section className="console-section" aria-label="Budgets">
      <Loaded load={budgets} label="Loading budgets" what="the budgets">
        {(list) =>
          list.length === 0 ? (
            <EmptyState title="No budgets" action={add}>
              Model calls aren't limited. Add a monthly budget for a person or a
              group to warn them or stop their calls.
            </EmptyState>
          ) : (
            <>
              <div className="console-section-actions">{add}</div>
              <EntityList label="Budgets">
                {list.map((budget) => (
                  <EntityRow
                    key={budget.id}
                    title={subjectName(budget)}
                    icon={
                      budget.scope === 'group' ? (
                        <Users size={18} />
                      ) : (
                        <User size={18} />
                      )
                    }
                    meta={`${budget.scope === 'group' ? 'Group' : 'Person'} · ${limits(budget)} a month`}
                    action={
                      <>
                        <IconButton
                          size="sm"
                          label={`Edit the budget for ${subjectName(budget)}`}
                          onClick={() => setEditing(budget)}
                        >
                          <Pencil size={16} aria-hidden />
                        </IconButton>
                        <IconButton
                          size="sm"
                          label={`Remove the budget for ${subjectName(budget)}`}
                          onClick={() => remove(budget)}
                        >
                          <Trash2 size={16} aria-hidden />
                        </IconButton>
                      </>
                    }
                  />
                ))}
              </EntityList>
            </>
          )
        }
      </Loaded>
      {editing && (
        <BudgetForm
          budget={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            budgets.reload();
            notify('The budget is saved');
          }}
        />
      )}
    </section>
  );
}
