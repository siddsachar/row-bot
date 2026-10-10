/**
 * Numbered connect steps for the built-in channels (Phase 15, parity rows
 * 42 and 45): what to do where, with the link, and which saved field the
 * step fills. Start, the test message and WhatsApp's code are steps the
 * sheet adds itself. Plugin channels show their own setup notes instead.
 */
export type GuideStep = {
  text: string;
  link?: { href: string; label: string };
  /** The channel field this step fills (its key). */
  field?: string;
};

export const CHANNEL_GUIDES: Record<string, GuideStep[]> = {
  telegram: [
    {
      text: 'In Telegram, message @BotFather and send /newbot to make your bot.',
      link: { href: 'https://t.me/BotFather', label: 'Open @BotFather' },
    },
    { text: 'Paste the bot token it gives you.', field: 'bot_token' },
    {
      text: 'Message @userinfobot for your user ID and paste it, so the bot answers only you.',
      link: { href: 'https://t.me/userinfobot', label: 'Open @userinfobot' },
      field: 'user_id',
    },
  ],
  slack: [
    {
      text: 'Create a Slack app from scratch.',
      link: { href: 'https://api.slack.com/apps', label: 'Open Slack apps' },
    },
    {
      text: 'Under OAuth & Permissions add the bot scopes app_mentions:read, chat:write, im:history, im:read, files:read, files:write, reactions:write and users:read.',
    },
    {
      text: 'Turn on Socket Mode and create an app-level token with connections:write.',
    },
    {
      text: 'Under Event Subscriptions subscribe to app_mention and message.im; under App Home turn on the Messages tab and allow messages from it.',
    },
    {
      text: 'Install the app to your workspace and paste its bot token (xoxb-…).',
      field: 'bot_token',
    },
    { text: 'Paste the app-level token (xapp-…).', field: 'app_token' },
    {
      text: 'Optional: paste your Slack member ID so Row-Bot knows it is you.',
      field: 'user_id',
    },
  ],
  discord: [
    {
      text: 'Create an application in the Discord developer portal and add a bot to it.',
      link: {
        href: 'https://discord.com/developers/applications',
        label: 'Open the developer portal',
      },
    },
    {
      text: 'Under Bot, turn on the Message Content intent and copy the bot token.',
    },
    {
      text: 'Under OAuth2 › URL Generator choose the bot scope with Send Messages, Attach Files, Read Message History and Add Reactions, then open the URL to invite the bot.',
    },
    { text: 'Paste the bot token.', field: 'bot_token' },
    {
      text: 'Optional: paste your Discord user ID so Row-Bot knows it is you.',
      field: 'user_id',
    },
  ],
  sms: [
    {
      text: 'Sign in to Twilio and copy the Account SID.',
      link: { href: 'https://console.twilio.com/', label: 'Open Twilio' },
      field: 'account_sid',
    },
    { text: 'Paste the auth token.', field: 'auth_token' },
    {
      text: 'Paste the Twilio phone number Row-Bot answers on (+15551234567).',
      field: 'phone_number',
    },
    {
      text: 'Paste your own phone number so Row-Bot answers you.',
      field: 'user_phone',
    },
  ],
  whatsapp: [
    {
      text: 'Optional: paste your phone number (+15551234567) so Row-Bot answers you without pairing.',
      field: 'user_phone',
    },
  ],
};

/** What starting a channel does, per channel, in one sentence. */
export const START_NOTES: Record<string, string> = {
  sms: 'Start SMS. Twilio needs a public address, so Row-Bot opens your tunnel and points your number at it.',
  whatsapp:
    'Start WhatsApp. The first start installs its bridge (about 30 seconds), then shows a code to scan.',
};

/** Where Google, X and GitHub are set up (parity row 45). */
export const ACCOUNT_LINKS = {
  githubToken: 'https://github.com/settings/personal-access-tokens/new',
  githubCli: 'https://cli.github.com/',
  // Google's own setup pages, in the order the guide uses them (checked 2026-10-05).
  googleProject: 'https://console.cloud.google.com/projectcreate',
  googleApis:
    'https://console.cloud.google.com/flows/enableapi?apiid=gmail.googleapis.com,calendar-json.googleapis.com',
  googleBranding: 'https://console.cloud.google.com/auth/branding',
  googleAudience: 'https://console.cloud.google.com/auth/audience',
  googleClient: 'https://console.cloud.google.com/auth/clients/create',
  xPortal: 'https://developer.x.com/en/portal/dashboard',
};
