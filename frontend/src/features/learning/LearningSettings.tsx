import { useState } from 'react';
import { Link } from 'react-router-dom';
import { GraduationCap } from 'lucide-react';
import { clientError } from '../../api/errors';
import type { LearningHardware, SettingsSnapshot } from '../../api/types';
import { Button, Disclosure, Select, Toggle } from '../../ui/primitives';
import { ModalTask, useNotify } from '../../ui/overlays';
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
import type { LearningIO } from './learning-state';
import { DownloadAsk, LearningSetupSheet } from './LearningSetup';
import {
  limitMeaning,
  limitOptions,
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
  const phone = usePhone();
  const [setupOpen, setSetupOpen] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [askHardware, setAskHardware] = useState<LearningHardware | null>(null);
  const [confirmOff, setConfirmOff] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const deleteData = useDeleteLearningData(io, onSettingsChanged);
  const snapshot = io.snapshot;
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
      <DownloadAsk
        open={askOpen}
        onOpenChange={setAskOpen}
        io={io}
        hardware={askHardware}
        onAnswered={(download) => {
          setAskOpen(false);
          notify(
            download ? 'Waiting to download.' : 'Not downloading for now.',
          );
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
  const waiting = model.download === 'waiting';
  const downloadStatus = waiting ? 'Waiting to download' : 'Not downloaded';
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
        <SettingsItem
          label="Training engine"
          help={`About ${sizeWords(model.engine_download_bytes)} to download.`}
          status={<StatusLine>{downloadStatus}</StatusLine>}
        />
        <SettingsItem
          label="Starting model"
          help={`${model.starting_model.name} · ${model.starting_model.licence} · ${sizeWords(model.starting_model.download_bytes)}`}
          status={<StatusLine>{downloadStatus}</StatusLine>}
          trailing={
            !waiting && learning.train_where === 'this_computer' ? (
              <Button
                className="small"
                onClick={() => {
                  setAskHardware(null);
                  void io.hardware().then(setAskHardware, () => undefined);
                  setAskOpen(true);
                }}
              >
                Download…
              </Button>
            ) : undefined
          }
        />
        {waiting && (
          <p className="learning-muted learning-group-note">
            The download starts once this preview can install the training
            engine. Nothing has been downloaded yet.
          </p>
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
              ? `${sizeWords(snapshot.data.bytes)}. Turning Learning off keeps it.`
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
