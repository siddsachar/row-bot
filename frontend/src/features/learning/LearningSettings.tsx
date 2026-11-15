import { useState } from 'react';
import { Link } from 'react-router-dom';
import { GraduationCap } from 'lucide-react';
import { clientError } from '../../api/errors';
import type {
  LearningHardware,
  LearningHuggingFace,
  SettingsSnapshot,
} from '../../api/types';
import { Button, Disclosure, Select, Toggle } from '../../ui/primitives';
import { ModalTask, useNotify, useOverlay } from '../../ui/overlays';
import {
  SettingsGroup,
  SettingsItem,
  SettingsPageMenu,
  SettingsStatus,
  StatusLine,
} from '../settings/anatomy';
import {
  SavedSetting,
  SegmentedSetting,
  SelectSetting,
  SwitchSetting,
  type SettingsMutationIO,
} from '../settings/SettingsSnapshotPanels';
import { useDeleteLearningData } from './DeleteLearningData';
import {
  useDownloadWatch,
  type LearningAction,
  type LearningFields,
  type LearningIO,
} from './learning-state';
import { DownloadAsk, LearningSetupSheet } from './LearningSetup';
import { CancelDownload, downloadView } from './PersonalModelDownload';
import { HuggingFaceSignIn } from './HuggingFace';
import { StartingModelSheet } from './StartingModelSheet';
import {
  HF_ACCESS,
  limitMeaning,
  limitOptions,
  moneyWords,
  percentWords,
  pictureWords,
  quietHoursWords,
  sizeWords,
  tokenWords,
} from './words';
import { usePhone } from './use-phone';
import './learning.css';

const TURN_OFF_WORDS =
  'Row-Bot stops recording, testing and training. Changes and models in use stay in use (undo them in Learning). Your learning data is kept until you delete it.';

/** "Create skills while chatting": the same setting as Preferences' old "Learn new skills" (D29). */
function SkillsWhileChatting({
  preferences,
  mutation,
}: {
  preferences: SettingsSnapshot['preferences'];
  mutation: SettingsMutationIO;
}) {
  return (
    <SettingsGroup
      title="While chatting"
      anchor="identity.self_improvement_enabled"
    >
      <SwitchSetting
        mutation={mutation}
        field="identity.self_improvement_enabled"
        label="Create skills while chatting"
        hint={`${preferences.identity.name || 'Row-Bot'} writes and improves its own skills as it works. This works with Learning on or off.`}
        value={preferences.identity.self_improvement_enabled}
      />
    </SettingsGroup>
  );
}

/** How it works, behind a disclosure on the Off card. */
function HowItWorks() {
  return (
    <Disclosure summary="How it works" className="learning-how">
      <ul className="learning-list">
        <li>
          Row-Bot keeps a private record of how it handled your requests, on
          this computer.
        </li>
        <li>
          During quiet hours it looks for things to do better and tests each
          idea on tasks it hasn’t seen. Nothing changes without a test, and it
          asks you first.
        </li>
        <li>
          It can also train a personal model of your own that runs on this
          computer. Downloads always ask first.
        </li>
        <li>
          Everything can be undone, and you can delete the data at any time.
        </li>
      </ul>
    </Disclosure>
  );
}

/**
 * Settings › Learning (designs/learning-ux.md §1, §8). Off: the card with Turn on
 * Learning. On: its settings. "Create skills while chatting" lives here either way.
 */
/** Settings › Learning › Personal model while it trains on Hugging Face (C1): account, model, monthly cap. */
function HuggingFaceSettings({
  io,
  hugging,
}: {
  io: LearningIO;
  hugging: LearningHuggingFace;
}) {
  const notify = useNotify();
  const [busy, setBusy] = useState(false);
  const signedIn = hugging.sign_in.state === 'signed_in';
  const choices = hugging.cap_choices ?? [10, 20, 50, 100];
  const caps =
    hugging.cap_usd && !choices.includes(hugging.cap_usd)
      ? [...choices, hugging.cap_usd].sort((a, b) => a - b)
      : choices;

  async function save(
    action: 'hf_choose_model' | 'hf_set_cap',
    fields: Partial<LearningFields>,
  ) {
    setBusy(true);
    try {
      notify((await io.run(action, fields)).summary);
    } catch (cause) {
      notify(clientError(cause).message, 'danger');
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <SettingsItem
        label="Hugging Face account"
        anchor="learning-hugging-face"
        help={
          signedIn
            ? HF_ACCESS
            : 'Training there runs in your own account, priced before each run.'
        }
        control={<HuggingFaceSignIn io={io} hugging={hugging} compact />}
        bind={false}
      />
      {signedIn && (hugging.models ?? []).length > 0 && (
        <SettingsItem
          label="Model on Hugging Face"
          anchor="learning-hugging-face-model"
          help="Only models this computer can run afterwards are offered."
          control={
            <Select
              aria-label="Model on Hugging Face"
              value={hugging.model}
              disabled={busy}
              onChange={(event) =>
                void save('hf_choose_model', { model: event.target.value })
              }
            >
              {(hugging.models ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                  {item.recommended ? ' (recommended)' : ''}
                </option>
              ))}
            </Select>
          }
          bind={false}
        />
      )}
      {signedIn && (
        <SettingsItem
          label="Monthly spending cap"
          anchor="learning-hugging-face-cap"
          help={`${moneyWords(hugging.month_spent_usd)} spent this month. Runs in quiet hours start only while they fit the cap.`}
          control={
            hugging.cap_usd ? (
              <Select
                aria-label="Monthly spending cap"
                value={String(hugging.cap_usd)}
                disabled={busy}
                onChange={(event) =>
                  void save('hf_set_cap', {
                    cap_usd: Number(event.target.value),
                  })
                }
              >
                {caps.map((value) => (
                  <option key={value} value={value}>
                    {moneyWords(value)} a month
                  </option>
                ))}
              </Select>
            ) : (
              <span className="learning-inline-value">
                Chosen with your first run
              </span>
            )
          }
          bind={false}
        />
      )}
    </>
  );
}

export default function LearningSettings({
  io,
  preferences,
  preferencesMutation,
  learning,
  learningMutation,
  onSettingsChanged,
  onOpenLearning,
}: {
  io: LearningIO;
  preferences: SettingsSnapshot['preferences'];
  preferencesMutation: SettingsMutationIO;
  learning: SettingsSnapshot['learning'];
  learningMutation: SettingsMutationIO;
  /** Learning's commands changed saved settings: read them again. */
  onSettingsChanged: () => void;
  onOpenLearning: () => void;
}) {
  const notify = useNotify();
  const overlay = useOverlay();
  const phone = usePhone();
  const [setupOpen, setSetupOpen] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [askHardware, setAskHardware] = useState<LearningHardware | null>(null);
  const [modelOpen, setModelOpen] = useState(false);
  const [confirmOff, setConfirmOff] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const deleteData = useDeleteLearningData(io, onSettingsChanged);
  const snapshot = io.snapshot;
  // The download's progress stays fresh while Settings › Learning is open.
  useDownloadWatch(io);
  // A saved Learning setting also changes Learning's own state.
  const mutation: SettingsMutationIO = {
    ...learningMutation,
    onSnapshot: (next) => {
      learningMutation.onSnapshot(next);
      void io.reload();
    },
  };

  const announceOn = () =>
    notify('Learning is on.', undefined, {
      label: 'Open Learning',
      onAction: onOpenLearning,
    });

  function turnedOn() {
    setSetupOpen(false);
    onSettingsChanged();
    announceOn();
  }

  async function download(action: LearningAction) {
    setBusy(true);
    setError('');
    try {
      notify((await io.run(action)).summary);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  async function command(action: 'turn_off' | 'pause' | 'resume') {
    setBusy(true);
    setError('');
    try {
      const receipt = await io.run(action);
      notify(receipt.summary);
      onSettingsChanged();
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }

  if (!snapshot)
    return io.error ? (
      <p role="alert">{io.error}</p>
    ) : (
      <p role="status">Loading Learning…</p>
    );

  const sheets = (
    <>
      <LearningSetupSheet
        open={setupOpen}
        onOpenChange={setSetupOpen}
        io={io}
        onTurnedOn={turnedOn}
        onLeave={() => setSetupOpen(false)}
      />
      <StartingModelSheet
        open={modelOpen}
        onOpenChange={setModelOpen}
        io={io}
      />
      <DownloadAsk
        open={askOpen}
        onOpenChange={setAskOpen}
        io={io}
        hardware={askHardware}
        onAnswered={(_download, summary) => {
          setAskOpen(false);
          notify(summary);
        }}
      />
    </>
  );

  if (snapshot.state === 'off')
    return (
      <div className="stack settings-snapshot-page learning-settings">
        {sheets}
        <SettingsStatus tone="neutral">Learning off</SettingsStatus>
        <section
          className="learning-off-card"
          aria-labelledby="learning-off-title"
        >
          <span className="learning-off-icon" aria-hidden>
            <GraduationCap size={20} />
          </span>
          <div className="learning-off-text">
            <h3 id="learning-off-title">Learning</h3>
            <p>
              Row-Bot learns from your work, tests ideas for doing better, and
              can train a personal model that runs on this computer.
            </p>
            {!snapshot.readable ? (
              <p role="alert" className="learning-error">
                Learning’s saved settings can’t be read, so Learning stays off.
              </p>
            ) : (
              <div className="learning-actions-inline">
                <Button variant="primary" onClick={() => setSetupOpen(true)}>
                  Turn on Learning
                </Button>
              </div>
            )}
            <HowItWorks />
            {snapshot.data.exists && (
              <p className="learning-muted">
                Learning data from before is kept:{' '}
                {sizeWords(snapshot.data.bytes)} on this computer.{' '}
                <Button
                  variant="ghost"
                  className="small"
                  onClick={(event) => deleteData(event.currentTarget)}
                >
                  Delete learning data…
                </Button>
              </p>
            )}
          </div>
        </section>
        <SkillsWhileChatting
          preferences={preferences}
          mutation={preferencesMutation}
        />
      </div>
    );

  const paused = snapshot.state === 'paused';
  const spending = snapshot.spending;
  const model = snapshot.personal_model;
  const transfer = model.download_state;
  const view = downloadView(model);
  const running = transfer.state === 'downloading';
  const here = learning.train_where === 'this_computer';
  const openAsk = () => {
    setAskHardware(null);
    void io.hardware().then(setAskHardware, () => undefined);
    setAskOpen(true);
  };
  const progress = running
    ? `Downloading · ${percentWords(transfer.done_bytes, transfer.total_bytes)}`
    : '';
  const engineStatus =
    model.engine === 'installed'
      ? `Installed, ${sizeWords(model.engine_bytes)}`
      : running && transfer.phase !== 'model'
        ? transfer.phase === 'engine'
          ? progress
          : 'Installing…'
        : model.engine === 'damaged'
          ? 'Damaged'
          : model.engine === 'outdated'
            ? 'Needs an update'
            : transfer.state === 'paused'
              ? 'Paused'
              : 'Not downloaded';
  const modelStatus =
    model.starting_model_state === 'downloaded'
      ? `On this computer, ${sizeWords(model.starting_model_bytes)}`
      : running && transfer.phase === 'model'
        ? progress
        : model.starting_model_state === 'damaged'
          ? 'Damaged'
          : transfer.state === 'paused'
            ? 'Paused'
            : 'Not downloaded';
  const showDownload = !running && !view && here;
  const removeEngine = (opener: HTMLElement) =>
    overlay.open({
      kind: 'alert',
      title: 'Remove the training engine?',
      description: `It frees ${sizeWords(model.engine_bytes)}. Training needs it, so nothing trains until you download it again (${sizeWords(model.engine_download_bytes)}).`,
      confirmLabel: 'Remove engine',
      returnFocusTo: opener,
      onConfirm: () => void download('remove_engine'),
    });
  const deleteModel = (opener: HTMLElement) =>
    overlay.open({
      kind: 'alert',
      title: `Delete ${model.starting_model.name}?`,
      description: `It frees ${sizeWords(model.starting_model_bytes)}. Training starts from it, so nothing trains until you download it again.`,
      confirmLabel: 'Delete',
      returnFocusTo: opener,
      onConfirm: () => void download('delete_starting_model'),
    });
  return (
    <div className="stack settings-snapshot-page learning-settings">
      {sheets}
      <SettingsStatus
        tone={paused ? 'neutral' : 'success'}
        more={[
          `${tokenWords(spending.used_tokens)} of ${tokenWords(spending.limit_tokens)} tokens this month`,
        ]}
      >
        {paused ? 'Learning paused' : 'Learning on'}
      </SettingsStatus>
      <SettingsPageMenu
        label="More for Learning"
        actions={[
          { label: 'Open Learning', onSelect: onOpenLearning },
          paused
            ? {
                label: 'Resume Learning',
                onSelect: () => void command('resume'),
              }
            : {
                label: 'Pause Learning',
                onSelect: () => void command('pause'),
              },
        ]}
      />
      <SettingsGroup label="Learning">
        <SettingsItem
          label="Learning"
          help="Records how Row-Bot handles your requests and looks for ways to do better."
          layout="inline"
          status={
            paused ? (
              <StatusLine
                tone="neutral"
                action={
                  <Button
                    variant="ghost"
                    className="small"
                    disabled={busy}
                    onClick={() => void command('resume')}
                  >
                    Resume
                  </Button>
                }
              >
                Paused: nothing new is recorded, tested or trained
              </StatusLine>
            ) : undefined
          }
          control={
            <Toggle
              label="Learning"
              checked
              disabled={busy}
              onChange={() => setConfirmOff(true)}
            />
          }
        />
        {error && (
          <p role="alert" className="learning-error">
            {error}
          </p>
        )}
      </SettingsGroup>
      <SettingsGroup
        title="Learn from"
        anchor="learning-sources"
        note="Chats with other people are never used. Switch any chat off from its menu."
      >
        <SwitchSetting
          mutation={mutation}
          field="sources.my_chats"
          label="My chats"
          hint="Includes your own direct chats on Telegram, Slack and other apps."
          value={learning.sources.my_chats}
        />
        <SwitchSetting
          mutation={mutation}
          field="sources.workflows_and_goals"
          label="Workflows and goals"
          value={learning.sources.workflows_and_goals}
        />
        <SwitchSetting
          mutation={mutation}
          field="sources.past_chats"
          label="Also learn from the last 30 days of chats"
          value={learning.sources.past_chats}
          row={{
            status:
              snapshot.past_chats.state === 'reading' ||
              snapshot.past_chats.state === 'waiting' ? (
                <StatusLine tone="info" pulse>
                  Reading {snapshot.past_chats.total.toLocaleString()} past
                  chats · {snapshot.past_chats.done.toLocaleString()} done
                </StatusLine>
              ) : undefined,
          }}
        />
        {snapshot.owner_unset_channels.map((item) => (
          <SettingsItem key={item.channel} label={item.name} help={item.note} />
        ))}
      </SettingsGroup>
      <SettingsGroup title="Changes and spending" anchor="learning-apply">
        <SelectSetting
          mutation={mutation}
          field="apply_mode"
          label="Using changes"
          hint="New personal-model versions always ask."
          value={learning.apply_mode}
          options={[
            { value: 'ask_first', label: 'Ask me first' },
            { value: 'automatic', label: 'Use changes that pass their tests' },
          ]}
        />
        <SavedSetting
          mutation={mutation}
          field="monthly_token_limit"
          label="Monthly limit"
          value={learning.monthly_token_limit}
          hint={`Learning uses ${spending.model_name ? `${spending.model_name} (your default model)` : 'your default model'} to find and test improvements. ${limitMeaning(learning.monthly_token_limit)}`}
          row={{
            status: (
              <StatusLine tone={spending.limit_reached ? 'warning' : undefined}>
                {tokenWords(spending.used_tokens)} of{' '}
                {tokenWords(spending.limit_tokens)} tokens used this month
              </StatusLine>
            ),
          }}
          savedMessage={(value) =>
            Number(value) === 0
              ? 'Learning makes no model calls now'
              : `Monthly limit set to ${tokenWords(Number(value))} tokens`
          }
        >
          {({ value, setValue, disabled }) => (
            <Select
              value={String(value)}
              disabled={disabled}
              onChange={(event) => setValue(Number(event.target.value))}
            >
              {limitOptions(Number(value)).map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          )}
        </SavedSetting>
        <SettingsItem
          label="Quiet hours"
          anchor="learning-quiet-hours"
          help="When Learning does its work. Shared with Dream Cycle."
          control={
            <span className="learning-inline-value">
              {quietHoursWords(
                snapshot.quiet_hours.window_start,
                snapshot.quiet_hours.window_end,
              )}
            </span>
          }
          bind={false}
          trailing={<Link to="/settings/preferences#dream-cycle">Change</Link>}
        />
      </SettingsGroup>
      <SettingsGroup title="Personal model" anchor="personal-model">
        <SwitchSetting
          mutation={mutation}
          field="train_in_quiet_hours"
          label="Train during quiet hours"
          hint="Trains a model of your own on your work while you’re away."
          value={learning.train_in_quiet_hours}
        />
        <SegmentedSetting
          mutation={mutation}
          field="train_where"
          label="Where to train"
          hint="Hugging Face uses your own account, priced before each run."
          value={learning.train_where}
          options={[
            { value: 'this_computer', label: 'This computer' },
            { value: 'hugging_face', label: 'Hugging Face' },
          ]}
        />
        {learning.train_where === 'hugging_face' && model.hugging_face && (
          <HuggingFaceSettings io={io} hugging={model.hugging_face} />
        )}
        <SettingsItem
          label="Training engine"
          anchor="learning-engine"
          help={
            model.engine === 'installed'
              ? 'Trains your personal model on this computer.'
              : `About ${sizeWords(model.engine_download_bytes)} to download.`
          }
          status={
            <StatusLine
              tone={model.engine === 'damaged' ? 'warning' : undefined}
              pulse={running && transfer.phase !== 'model'}
            >
              {engineStatus}
            </StatusLine>
          }
          trailing={
            model.engine === 'installed' ? (
              <Button
                className="small"
                disabled={busy || running}
                onClick={(event) => removeEngine(event.currentTarget)}
              >
                Remove
              </Button>
            ) : showDownload && model.engine === 'not_downloaded' ? (
              <Button className="small" onClick={openAsk}>
                Download…
              </Button>
            ) : undefined
          }
        />
        <SettingsItem
          label="Starting model"
          anchor="learning-starting-model"
          help={`${model.starting_model.name} · ${model.starting_model.licence} · from Hugging Face${
            model.starting_model_state === 'downloaded'
              ? ''
              : ` · ${sizeWords(model.starting_model.download_bytes)}`
          }`}
          status={
            <StatusLine
              tone={
                model.starting_model_state === 'damaged' ? 'warning' : undefined
              }
              pulse={running && transfer.phase === 'model'}
            >
              {modelStatus}
            </StatusLine>
          }
          trailing={
            <span className="learning-actions-inline">
              {here && (
                <Button
                  className="small"
                  disabled={busy || running}
                  aria-label="Change the starting model"
                  onClick={() => setModelOpen(true)}
                >
                  Change…
                </Button>
              )}
              {model.starting_model_state === 'downloaded' ? (
                <Button
                  className="small"
                  disabled={busy || running}
                  onClick={(event) => deleteModel(event.currentTarget)}
                >
                  Delete
                </Button>
              ) : showDownload &&
                model.engine === 'installed' &&
                model.starting_model_state === 'not_downloaded' ? (
                <Button className="small" onClick={openAsk}>
                  Download…
                </Button>
              ) : null}
            </span>
          }
        />
        {view && here && (
          <SettingsItem
            label="Download"
            anchor="learning-download"
            help={[view.detail, view.note].filter(Boolean).join(' ')}
            status={
              <StatusLine
                tone={
                  transfer.state === 'failed' ||
                  transfer.state === 'not_enough_disk'
                    ? 'warning'
                    : undefined
                }
                pulse={running}
              >
                {view.title}
              </StatusLine>
            }
            trailing={
              <span className="learning-actions-inline">
                {view.actions.map((item) => (
                  <Button
                    key={`${item.action}-${item.label}`}
                    className="small"
                    variant={item.primary ? 'primary' : undefined}
                    disabled={busy}
                    onClick={() =>
                      item.action === 'cancel_download'
                        ? setCancelling(true)
                        : void download(item.action)
                    }
                  >
                    {item.label}
                  </Button>
                ))}
              </span>
            }
          />
        )}
      </SettingsGroup>
      <SkillsWhileChatting
        preferences={preferences}
        mutation={preferencesMutation}
      />
      <SettingsGroup title="Learning data" anchor="learning-data">
        <SettingsItem
          label="Kept on this computer"
          help={
            snapshot.data.exists
              ? `${sizeWords(snapshot.data.bytes)}${pictureWords(snapshot.data)}. Turning Learning off keeps it.`
              : 'Nothing yet.'
          }
          trailing={
            snapshot.data.exists ? (
              <Button
                className="small"
                onClick={(event) => deleteData(event.currentTarget)}
              >
                Delete learning data…
              </Button>
            ) : undefined
          }
        />
      </SettingsGroup>
      <CancelDownload
        open={cancelling}
        onOpenChange={setCancelling}
        bytes={transfer.done_bytes}
        onConfirm={() => void download('cancel_download')}
      />
      <ModalTask
        open={confirmOff}
        onOpenChange={setConfirmOff}
        title="Turn off Learning?"
        description={TURN_OFF_WORDS}
        kind={phone ? 'sheet' : 'dialog'}
      >
        <div className="learning-sheet-actions">
          <Button onClick={() => setConfirmOff(false)} data-initial-focus>
            Keep it on
          </Button>
          <Button
            variant="primary"
            disabled={busy}
            onClick={() => {
              setConfirmOff(false);
              void command('turn_off');
            }}
          >
            Turn off
          </Button>
        </div>
      </ModalTask>
    </div>
  );
}
