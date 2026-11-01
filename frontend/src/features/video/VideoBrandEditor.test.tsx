import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import type { ClientController } from '../../api/controller';
import type { VideoBrand } from '../../api/types';
import type { ClientPlatform } from '../../platform';
import { RuntimeContext } from '../../runtime';
import { VideoBrandEditor } from './VideoBrandEditor';

const KIT: VideoBrand = {
  colour: '#1f2937',
  captions: 'box',
  logo: '',
  logo_corner: 'top-left',
  intro: '',
  outro: '',
  music_mood: 'calm',
  framing: 'framed',
  camera: 'follow',
  voice: '',
  voice_speed: 1,
};

it('edits the brand kit with pictures from Media as logos, and saves it only when something changed', async () => {
  const controller = {
    videoBrand: vi.fn(async () => KIT),
    saveVideoBrand: vi.fn(async (brand: VideoBrand) => brand),
    settingsSnapshot: vi.fn(async () => ({
      voice: {
        tts_voice_options: [{ value: 'bm_george', label: 'George (British)' }],
      },
    })),
    media: vi.fn(async () => ({
      items: [
        {
          ref: 'conversation-a:logo',
          name: 'logo.png',
          kind: 'image',
          mime_type: 'image/png',
          size: 2048,
          added_at: new Date().toISOString(),
          conversation_id: 'conversation-a',
        },
      ],
    })),
  } as unknown as ClientController & Record<string, ReturnType<typeof vi.fn>>;
  const saved = vi.fn();
  render(
    <RuntimeContext.Provider
      value={{ controller, platform: {} as ClientPlatform }}
    >
      <VideoBrandEditor onSaved={saved} />
    </RuntimeContext.Provider>,
  );
  const save = await screen.findByRole('button', { name: 'Save brand kit' });
  expect(save).toBeDisabled();
  expect(controller.media).toHaveBeenCalledWith(
    { kind: 'image' },
    expect.anything(),
  );
  expect(screen.queryByLabelText('Logo corner')).not.toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText('Logo'), 'logo.png');
  await userEvent.selectOptions(
    screen.getByLabelText('Logo corner'),
    'Bottom right',
  );
  await userEvent.selectOptions(
    screen.getByLabelText('Caption style'),
    'Bold, outlined',
  );
  await userEvent.type(screen.getByLabelText('Outro'), 'row-bot.ai');
  // How recordings show and who says the lines are the person's choice too.
  await userEvent.selectOptions(
    screen.getByLabelText('Footage'),
    'Edge to edge',
  );
  await userEvent.selectOptions(screen.getByLabelText('Camera'), 'Stays still');
  await userEvent.selectOptions(
    await screen
      .findByRole('option', { name: 'George (British)' })
      .then(() => screen.getByLabelText('Voice')),
    'George (British)',
  );
  await userEvent.selectOptions(screen.getByLabelText('Voice speed'), 'Slower');
  await userEvent.click(save);
  expect(controller.saveVideoBrand).toHaveBeenCalledWith({
    ...KIT,
    logo: 'conversation-a:logo',
    logo_corner: 'bottom-right',
    captions: 'bold',
    outro: 'row-bot.ai',
    framing: 'full',
    camera: 'still',
    voice: 'bm_george',
    voice_speed: 0.9,
  });
  expect(saved).toHaveBeenCalled();
  expect(save).toBeDisabled();
});
