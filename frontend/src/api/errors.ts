import type { ClientError, ClientStatus, ErrorAction } from './types';
import { isNetworkFailure } from './network-failure';

/**
 * The error catalog. Every code the server can put in a problem response
 * (`contracts/client-platform/v1/error-codes.json`) and every code the client
 * raises itself maps to one plain sentence and, where one helps, one fix:
 * Retry, Reconnect, Choose a model, Send now or Open <the exact setting>.
 * A fix is left out only when the sentence already says what to change.
 *
 * The words stay human: no "receipt", "replayed", "admission" or "owned".
 * Receipts still exist; they are how a check reads an earlier outcome
 * without running anything twice. They are just never named.
 */
type Recovery = ClientError['recovery'];
type Entry = readonly [
  message: string,
  recovery?: Recovery,
  action?: ErrorAction,
];

const RETRY: ErrorAction = { kind: 'retry' };
const RECONNECT: ErrorAction = { kind: 'reconnect' };
const CHOOSE_MODEL: ErrorAction = { kind: 'choose_model' };
const SEND_NOW: ErrorAction = { kind: 'send_now' };
function open(leaf: string, label: string, anchor?: string): ErrorAction {
  return {
    kind: 'open_setting',
    href: `/settings/${leaf}${anchor ? `#${anchor}` : ''}`,
    label: `Open ${label}`,
  };
}
const PROVIDERS = open('providers', 'Providers');
const CUSTOM_ENDPOINTS = open(
  'providers',
  'Custom endpoints',
  'custom-endpoints',
);
const IMAGE_MODEL = open('models', 'Image model', 'image-model');
const VOICE = open('voice', 'Voice settings', 'dictation');
// Accounts and channels are apps: their fixes open the app.
const ACCOUNTS = open('apps', 'Apps');
const GITHUB = open('apps/github', 'GitHub');
const GOOGLE = open('apps/google', 'Google');
const CHANNELS = open('apps?category=communication', 'Apps');
const PLUGINS = open('apps', 'Apps');
const MCP = open('apps', 'Apps');
const RUNTIMES = open('apps?view=advanced', 'Runtimes', 'chats');
const SKILLS = open('skills', 'Skills');
const TOOLS = open('tools', 'Built-in tools', 'built-in-tools');
const WIKI = open('knowledge', 'Wiki vault', 'wiki-vault');
const DREAM = open('preferences', 'Dream Cycle', 'dream-cycle');
const DOCUMENTS = open('documents', 'Documents', 'document-upload');
const BUDDY = open('buddy', 'Buddy', 'buddy-look');
const MIGRATION = open('data', 'Import', 'migration');
const UPDATES = open('updates', 'Updates', 'updates.channel');
const TUNNEL = open('access', 'Tunnel', 'tunnel');

const CANT_DO_FROM_HERE: Entry = [
  "Row-Bot can't do that from here yet. Ask for it in the conversation instead.",
  'none',
];

const CATALOG: Record<string, Entry> = {
  plan_changed: [
    'Something changed since you agreed. Review it again before continuing.',
    'review',
    MCP,
  ],
  plan_unsupported: [
    "Row-Bot can't set this up yet. Open its details to see why.",
    'review',
    MCP,
  ],
  plan_not_resumable: [
    'This setup already finished or was stopped. Start again if you still need it.',
    'review',
    MCP,
  ],
  invalid_access_preset: [
    'Choose Read only, Ask before changes or Full access.',
    'review',
    MCP,
  ],
  invalid_integration_query: [
    'Search again with a shorter phrase.',
    'review',
    MCP,
  ],
  integration_link_unsupported: [
    'Paste an https link to an app, a skill page or a GitHub repository.',
    'review',
  ],
  invalid_upload: [
    'Choose a .zip or .skill file up to 20 MB, or a .mcpb bundle up to 128 MB.',
    'review',
  ],
  bundle_signature_invalid: [
    "This bundle's signature doesn't match its contents, so Row-Bot won't add it. Get a fresh copy from its publisher.",
    'review',
  ],
  bundle_unsupported: [
    "Row-Bot can't run this bundle on this computer: it's for another system or needs something Row-Bot doesn't set up.",
    'review',
  ],
  unsupported_upload: [
    "That file doesn't hold a skill or a package Row-Bot can add.",
    'review',
  ],
  unsafe_upload: [
    "That file has paths Row-Bot won't unpack. Ask its publisher for a clean copy.",
    'review',
  ],
  upload_too_large: [
    'That file is too large. Choose one under 20 MB with at most 200 files.',
    'review',
  ],
  invalid_mcp_auth: [
    'Check the authentication method, secret binding, and account label.',
    'review',
    MCP,
  ],
  invalid_mcp_target: [
    'This connection is unavailable. Open the app again.',
    'review',
    MCP,
  ],
  mcp_auth_busy: [
    'Several sign-ins are pending. Cancel an unused sign-in before starting another.',
    'review',
    MCP,
  ],
  mcp_auth_callback_invalid: [
    'This sign-in is no longer valid. Return to Apps and start again.',
    'review',
    MCP,
  ],
  mcp_auth_callback_unavailable: [
    'Sign-in needs the local desktop callback or one approved HTTPS server origin. Configure access before retrying.',
    'review',
    MCP,
  ],
  mcp_auth_configuration_changed: [
    'Connection settings changed. Cancel the old sign-in and review the current connection.',
    'review',
    MCP,
  ],
  mcp_auth_connection_unavailable: [
    'The connection was removed or changed. Open Apps again.',
    'review',
    MCP,
  ],
  mcp_auth_denied: [
    'Sign-in was declined. Start again when you want to authorize this connection.',
    'review',
    MCP,
  ],
  mcp_auth_expired: [
    'Sign-in expired. Cancel the old step and start again.',
    'review',
    MCP,
  ],
  mcp_auth_flow_unavailable: [
    'The original sign-in is unavailable. Check the connection before starting again.',
    'review',
    MCP,
  ],
  mcp_auth_not_completed: [
    'The service did not complete sign-in. Check its MCP authentication requirements.',
    'review',
    MCP,
  ],
  mcp_auth_state_invalid: [
    'The service returned an invalid sign-in request. Review its authentication setup.',
    'review',
    MCP,
  ],
  mcp_oauth_http_required: [
    'Browser OAuth requires an HTTP MCP endpoint. Use an explicit environment binding for this process.',
    'review',
    MCP,
  ],
  mcp_auth_endpoint_invalid: [
    'The sign-in endpoint must use public HTTPS. Check the service documentation.',
    'review',
    MCP,
  ],
  mcp_auth_issuer_mismatch: [
    'The sign-in issuer did not match the service metadata. No credentials were granted.',
    'review',
    MCP,
  ],
  mcp_auth_origin_mismatch: [
    'The sign-in request changed destination. Review the service endpoint before retrying.',
    'review',
    MCP,
  ],
  mcp_auth_redirect_refused: [
    'The service redirected an authenticated request. Use its documented MCP endpoint.',
    'review',
    MCP,
  ],
  mcp_credentials_endpoint_changed: [
    'The endpoint changed after credentials were saved. Sign in again for the new destination.',
    'review',
    MCP,
  ],
  mcp_credentials_too_large: [
    'These credentials exceed protected storage limits. Check the service setup.',
    'review',
    MCP,
  ],
  mcp_credentials_unavailable: [
    'Protected credentials are unavailable. Unlock local secret storage or sign in again.',
    'review',
    MCP,
  ],
  invalid_credential_reference: [
    'This saved credential reference is invalid. Sign in again.',
    'review',
    MCP,
  ],
  mcp_durable_storage_required: [
    'Enable a protected local secret store before saving MCP credentials.',
    'review',
    MCP,
  ],
  mcp_sign_in_required: [
    'Sign in to this connection before testing it.',
    'review',
    MCP,
  ],
  mcp_package_recipe_unsupported: [
    'This command needs manual setup. Managed preparation supports self-contained npm archives or complete shrinkwraps without install scripts.',
    'review',
    MCP,
  ],
  mcp_package_install_scripts_unsupported: [
    'This package runs installation scripts. It needs a separate dependency review and cannot use automatic preparation.',
    'review',
    MCP,
  ],
  mcp_package_locked_dependencies_required: [
    'The package has unpinned dependencies. Use a self-contained release or a complete npm shrinkwrap.',
    'review',
    MCP,
  ],
  mcp_package_integrity_required: [
    'The package does not provide the required SHA-512 integrity pin.',
    'review',
    MCP,
  ],
  mcp_package_integrity_changed: [
    'The reviewed package bytes changed. Inspect the package again.',
    'review',
    MCP,
  ],
  mcp_package_invalid: [
    'The package metadata or archive is invalid. Check the publisher release.',
    'review',
    MCP,
  ],
  mcp_package_node_required: [
    'Prepare the Node runtime before connecting this package.',
    'review',
    MCP,
  ],
  mcp_package_preparation_required: [
    'Inspect and prepare this exact npm package before testing it.',
    'review',
    MCP,
  ],
  mcp_package_preview_capacity: [
    'Too many package inspections are open. Finish a pending setup or retry after it expires.',
    'review',
    MCP,
  ],
  mcp_package_preview_expired: [
    'The package inspection expired. Inspect its requirements again.',
    'review',
    MCP,
  ],
  mcp_package_source_invalid: [
    'Managed npm packages must come from the public npm registry with an integrity pin.',
    'review',
    MCP,
  ],
  mcp_package_too_large: [
    'This package exceeds the bounded installation size.',
    'review',
    MCP,
  ],
  package_link_or_collision: [
    'The archive contains linked or conflicting paths and cannot be imported.',
    'review',
    MCP,
  ],
  unsafe_package_path: [
    'A package path escapes its allowed folder. Choose a valid package.',
    'review',
    MCP,
  ],
  hermes_recipe_unsupported: [
    'This Hermes recipe requires unsupported bootstrap, authentication, or launch behavior. Nothing was executed.',
    'review',
    MCP,
  ],
  package_download_too_large: [
    'This package exceeds the download size limit.',
    'review',
    MCP,
  ],
  package_preview_changed: [
    'The inspected package changed. Inspect it again before adding it.',
    'review',
    MCP,
  ],
  package_preview_expired: [
    'This package inspection expired. Inspect the source again.',
    'review',
    MCP,
  ],
  package_source_not_supported: [
    'Choose a public GitHub package, a supported catalog entry, or an authorized local package.',
    'review',
    MCP,
  ],
  package_source_removed: [
    'The publisher removed this source. Existing local files are preserved.',
    'review',
    MCP,
  ],
  plugin_child_owned: [
    'Manage this connection through its parent package.',
    'review',
    MCP,
  ],
  plugin_child_parent_owned: [
    'Turn the parent package on or off in its details. Child setup cannot change the parent.',
    'review',
    MCP,
  ],
  plugin_child_source_immutable: [
    'Package launch details belong to the publisher. Import a separate custom connection to change them.',
    'review',
    MCP,
  ],
  plugin_mcp_state_unavailable: [
    'The package connection settings are unavailable. Inspect the parent package and its recovery actions.',
    'review',
    MCP,
  ],
  plugin_package_state_unavailable: [
    'The package state is unavailable. Inspect recovery before making another change.',
    'review',
    MCP,
  ],

  // Accounts and sign-in
  account_busy: [
    'Another account step is still running. Wait for it to finish, then try again.',
    'retry',
    RETRY,
  ],
  account_changed: [
    'The account changed while you were looking at it. Try again with its current state.',
    'retry',
    RETRY,
  ],
  account_command_conflict: [
    'This sign-in step no longer matches the one that started it. Start it again.',
    'review',
    ACCOUNTS,
  ],
  account_confirmation_required: [
    'Confirm this account change before it goes ahead.',
    'review',
    ACCOUNTS,
  ],
  account_credentials_invalid: [
    'Choose a valid Google OAuth client file from Google Cloud Console.',
    'review',
    GOOGLE,
  ],
  account_credentials_required: [
    'Add the account credentials before signing in.',
    'review',
    ACCOUNTS,
  ],
  invalid_account_command: [
    "That account step isn't valid. Start it again from Accounts.",
    'review',
    ACCOUNTS,
  ],
  account_receipt_missing: [
    "Row-Bot can't find the earlier account step. Check the account before trying again.",
    'review',
    ACCOUNTS,
  ],
  github_cli_host_terminal_required: [
    'Sign in to GitHub from your own terminal with "gh auth login", then check again.',
    'review',
    GITHUB,
  ],
  github_cli_missing: [
    "The GitHub command-line tool (gh) isn't installed. Install it, then check again.",
    'review',
    GITHUB,
  ],
  github_cli_unauthenticated: [
    'Sign in to GitHub on this computer to open pull requests.',
    'review',
    GITHUB,
  ],

  // Connection and access
  action_denied: ["This device isn't allowed to do that.", 'none'],
  agent_run_finished: [
    'This agent has already finished, so it can’t take a message.',
    'none',
  ],
  agent_work_not_resumable: [
    'Nothing is left to resume. Dismiss it to clear the notice.',
    'none',
  ],
  agent_resume_unavailable: [
    'This agent work can’t resume: turn on the Agents tool and check that its model is ready, or dismiss it.',
    'review',
  ],
  action_unavailable: ["That isn't available here.", 'none'],
  authentication_required: [
    'Connect to Row-Bot to continue.',
    'authenticate',
    RECONNECT,
  ],
  capability_revoked: [
    'Access to that item was withdrawn. Choose it again.',
    'review',
  ],
  capability_unavailable: ["This isn't available on this device.", 'none'],
  command_metadata_unavailable: [
    "Row-Bot couldn't read its list of recent actions. Try again.",
    'retry',
    RETRY,
  ],
  dependency_unavailable: [
    "Part of Row-Bot isn't responding. Try again in a moment.",
    'retry',
    RETRY,
  ],
  externally_managed: [
    "This server's deployment settings manage its addresses, so they can't be changed here.",
    'none',
  ],
  idempotency_expired: [
    'That action is too old to check. Look at the current state before doing it again.',
    'review',
  ],
  idempotency_mismatch: [
    'That action was already used for something else. Start it again.',
    'review',
  ],
  invalid_device_name: [
    'Name the device with 1 to 80 characters on one line.',
    'review',
  ],
  invalid_origin: [
    'Use one exact address, such as https://row-bot.example.com.',
    'review',
  ],
  network_unavailable: [
    'Disconnected. What you last saw is kept.',
    'retry',
    RECONNECT,
  ],
  not_found: ['That is no longer available.', 'none'],
  views_off: [
    'Views from this app are off. Turn them on from its page in Apps.',
    'none',
  ],
  app_not_connected: [
    'Connect the app this template uses first, then try again.',
    'none',
  ],
  view_unavailable: ["This view isn't available. Try again later.", 'retry'],
  view_tool_refused: ["This view can't do that here.", 'none'],
  view_tool_denied: ["You didn't allow this action.", 'none'],
  view_busy: ['This view is already waiting for your answer.', 'none'],
  view_rate_limited: [
    'Too many requests from this view. Wait a moment.',
    'retry',
  ],
  view_tool_failed: ["The app couldn't do that just now.", 'retry'],
  operation_pending: [
    'An earlier change is still running. Check again in a moment.',
    'retry',
    RETRY,
  ],
  operation_uncertain: [
    "Row-Bot couldn't confirm whether that finished. Check again before doing anything new.",
    'review',
    RETRY,
  ],
  origin_rejected: [
    "This address isn't allowed to use Row-Bot. Open Row-Bot from its usual address.",
    'none',
  ],
  owner_local_only: ['Only the computer running Row-Bot can do this.', 'none'],
  path_denied: ["Row-Bot isn't allowed to use that location.", 'review'],
  payload_too_large: ['That is larger than Row-Bot accepts.', 'none'],
  protocol_incompatible: [
    'This window is out of date. Reload it to continue.',
    'update',
  ],
  rate_limited: [
    'Too many requests at once. Wait a moment, then try again.',
    'retry',
    RETRY,
  ],
  receipt_missing: [
    "Row-Bot can't find the earlier Tailscale step. Check Tailscale's status before trying again.",
    'review',
    RETRY,
  ],
  receipt_unavailable: [
    "Row-Bot can't find the earlier step. Check the current state before trying again.",
    'review',
  ],
  revision_conflict: [
    'This changed somewhere else. Load it again, then make your change.',
    'retry',
    RETRY,
  ],
  route_changed: [
    'The connection address changed. Refresh the addresses, then try again.',
    'retry',
    RETRY,
  ],
  session_expired: [
    'Your session ended. Reconnect to continue.',
    'authenticate',
    RECONNECT,
  ],
  snapshot_revision_conflict: [
    'This changed while it was loading. Load it again.',
    'retry',
    RETRY,
  ],
  subscription_in_use: [
    'This window is already receiving live updates. Reconnect if they stop.',
    'retry',
    RECONNECT,
  ],
  cursor_expired: ['This list is out of date. Load it again.', 'retry', RETRY],
  cursor_revision_conflict: [
    'This list changed. Load it again.',
    'retry',
    RETRY,
  ],
  invalid_cursor: ['This list is out of date. Load it again.', 'retry', RETRY],
  invalid_limit: ["That page size isn't valid.", 'review'],
  invalid_query: [
    "That search isn't valid. Change it and try again.",
    'review',
  ],
  invalid_catalog_query: [
    "That catalog search isn't valid. Change it and search again.",
    'review',
  ],
  invalid_command: [
    "That request isn't valid. Check what you entered and try again.",
    'review',
  ],
  invalid_fields: ['Check the fields you filled in and try again.', 'review'],

  endpoint_unreachable: [
    "Row-Bot couldn't reach that endpoint. Check its address and that its server is running, then refresh it.",
    'review',
  ],
  native_mcp_unavailable: [
    'External MCP tools are not available in this Row-Bot. Restart it and try again.',
    'review',
  ],
  plugin_environment_ready: [
    'This plugin is already prepared. Refresh the list.',
    'review',
  ],

  // Approvals
  approval_already_resolved: [
    'This approval was already answered. Close this and check the conversation.',
    'review',
  ],
  approval_expired: [
    'This approval expired. Close this and answer the current approval.',
    'review',
  ],
  approval_required: [
    'An approval is waiting in this conversation. Answer it first.',
    'review',
  ],

  // Conversations, sending and waiting messages
  checkpoint_unavailable: [
    "The conversation couldn't be saved just now. Try again.",
    'retry',
    RETRY,
  ],
  conversation_action_unconfirmed: [
    "Row-Bot couldn't confirm the change to this conversation. Check it before trying again.",
    'review',
    RETRY,
  ],
  conversation_archive_unavailable: [
    "Archiving conversations isn't available yet.",
    'none',
  ],
  conversation_deleting: ['This conversation is being deleted.', 'none'],
  conversation_export_too_large: [
    'This conversation is too large to export as one file.',
    'none',
  ],
  conversation_export_pdf_unavailable: [
    "PDF export isn't installed with this copy of Row-Bot. Export as Markdown instead.",
    'none',
  ],
  conversation_export_unconfirmed: [
    "Row-Bot couldn't confirm the export. Try again.",
    'retry',
    RETRY,
  ],
  conversation_review_changed: [
    'The conversation changed. Try again.',
    'retry',
    RETRY,
  ],
  conversation_state_unavailable: [
    "The conversation couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  conversation_transcript_changed: [
    'The conversation changed while it was being read. Try again.',
    'retry',
    RETRY,
  ],
  conversation_transcript_unavailable: [
    "The conversation's messages couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  conversation_unavailable: [
    'This conversation is no longer available.',
    'none',
  ],
  draft_revision_conflict: [
    'This draft changed in another window. Check the saved draft before replacing it.',
    'review',
  ],
  draft_save_failed: [
    "Your draft couldn't be saved. Try again.",
    'retry',
    RETRY,
  ],
  generation_active: [
    'Row-Bot is still answering. Wait for it to finish, or stop it first.',
    'review',
  ],
  generation_not_steerable: [
    'That answer already finished, so this could not be added to it. Send it as a new message.',
    'review',
  ],
  invalid_conversation_action: [
    "That conversation action isn't valid.",
    'review',
  ],
  invalid_queue_cursor: [
    'The waiting messages changed. Load them again.',
    'retry',
    RETRY,
  ],
  invalid_queue_page: [
    'The waiting messages changed. Load them again.',
    'retry',
    RETRY,
  ],
  invalid_queue_text: [
    'A waiting message needs between 1 and 16,000 characters.',
    'review',
  ],
  invalid_submission_id: [
    "This message couldn't be matched. Send it again.",
    'review',
  ],
  queue_capacity: [
    'Too many messages are waiting. Send or discard some first.',
    'review',
  ],
  queue_content_unavailable: [
    "The waiting message's text couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  queue_pending: [
    'A message is waiting to be sent. Send it now, or discard it first.',
    'review',
    SEND_NOW,
  ],
  queue_requires_resume: [
    'Nothing is waiting to be sent. Resume the conversation to continue.',
    'review',
  ],
  queue_revision_conflict: [
    'The waiting messages changed. Check them, then try again.',
    'retry',
    RETRY,
  ],
  origin_repair_required: [
    'The original conversation is gone. Check the repair before continuing.',
    'review',
  ],

  // Models and thinking
  invalid_model_selection: [
    "That model isn't available. Choose another model.",
    'review',
    CHOOSE_MODEL,
  ],
  invalid_reasoning_selection: [
    "This model doesn't support that thinking level. Choose another level.",
    'review',
  ],
  model_configuration_required: [
    'Choose a model before sending. Your message is kept.',
    'review',
    CHOOSE_MODEL,
  ],
  model_configuration_unavailable: [
    "The chosen model isn't available. Choose another model.",
    'review',
    CHOOSE_MODEL,
  ],
  model_selection_mismatch: [
    'The model changed while you were sending. Check the model, then send again.',
    'review',
    CHOOSE_MODEL,
  ],
  model_selection_required: ['Choose a model first.', 'review', CHOOSE_MODEL],
  model_settings_unavailable: [
    "Model settings couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  reasoning_capabilities_changed: [
    "This model's thinking levels changed. Reopen the conversation and choose again.",
    'review',
  ],
  reasoning_model_mismatch: [
    'The model changed. Check its thinking levels, then try again.',
    'review',
    CHOOSE_MODEL,
  ],
  reasoning_snapshot_unavailable: [
    "The thinking level for the waiting message couldn't be recovered. Check the message before continuing.",
    'review',
  ],

  // Providers and subscriptions
  invalid_api_key: [
    "That API key doesn't look right. Check it and paste it again.",
    'review',
    PROVIDERS,
  ],
  invalid_provider_configuration: [
    "One of the provider's settings isn't valid. Check them and try again.",
    'review',
    PROVIDERS,
  ],
  invalid_provider_endpoint_url: [
    'Use a full address that starts with http:// or https://.',
    'review',
    CUSTOM_ENDPOINTS,
  ],
  invalid_provider_extra_body: [
    "The extra request settings aren't valid JSON.",
    'review',
    CUSTOM_ENDPOINTS,
  ],
  invalid_subscription_options: [
    'Check the subscription options and try again.',
    'review',
    PROVIDERS,
  ],
  invalid_subscription_probe: [
    "That subscription check isn't valid.",
    'review',
    PROVIDERS,
  ],
  provider_configuration_unavailable: [
    "Provider settings couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  provider_credential_unconfirmed: [
    "Row-Bot couldn't confirm the key was saved. Check the provider before trying again.",
    'review',
    PROVIDERS,
  ],
  provider_endpoint_identity_conflict: [
    'An endpoint with that name already exists. Choose another name.',
    'review',
    CUSTOM_ENDPOINTS,
  ],
  provider_recovery_unavailable: [
    "The provider's settings can't be repaired automatically. Check them in Providers.",
    'review',
    PROVIDERS,
  ],
  provider_settings_unavailable: [
    "Provider settings couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  provider_unavailable: ["That provider isn't available.", 'review', PROVIDERS],
  subscription_busy: [
    'Another sign-in is still running. Wait for it, then try again.',
    'retry',
    RETRY,
  ],
  subscription_cancelled: [
    'The sign-in was cancelled. Start it again.',
    'retry',
    RETRY,
  ],
  subscription_capacity: [
    'Too many sign-ins are in progress. Wait for one to finish.',
    'retry',
    RETRY,
  ],
  subscription_endpoint_invalid: [
    "The provider's sign-in service answered unexpectedly. Try again later.",
    'retry',
    RETRY,
  ],
  subscription_expired: [
    'The sign-in code expired. Start again.',
    'retry',
    RETRY,
  ],
  subscription_flow_unavailable: [
    'That sign-in is no longer running. Start again.',
    'retry',
    RETRY,
  ],
  subscription_listener_active: [
    'A sign-in is already waiting for you in the browser. Finish or cancel it first.',
    'review',
    PROVIDERS,
  ],
  subscription_listener_unavailable: [
    "Row-Bot couldn't start listening for the sign-in. Try again.",
    'retry',
    RETRY,
  ],
  subscription_options_review_invalid: [
    'The subscription settings changed. Try again.',
    'retry',
    RETRY,
  ],
  subscription_options_unconfirmed: [
    "Row-Bot couldn't confirm the change. Check the subscription before trying again.",
    'review',
    PROVIDERS,
  ],
  subscription_probe_limit: [
    'Too many checks are running. Wait for one to finish.',
    'retry',
    RETRY,
  ],
  subscription_probe_review_invalid: [
    'The subscription changed. Try the check again.',
    'retry',
    RETRY,
  ],
  subscription_probe_unconfirmed: [
    "Row-Bot couldn't confirm the check. Look at the subscription before trying again.",
    'review',
    PROVIDERS,
  ],
  subscription_recovery_unavailable: [
    "That sign-in can't be recovered. Start again.",
    'retry',
    RETRY,
  ],
  subscription_reference_changed: [
    'The subscription account changed. Refresh Providers, then try again.',
    'retry',
    PROVIDERS,
  ],
  subscription_reference_unavailable: [
    'That subscription account is no longer connected.',
    'review',
    PROVIDERS,
  ],
  subscription_response_invalid: [
    "The provider's answer wasn't understood. Try again later.",
    'retry',
    RETRY,
  ],
  subscription_review_invalid: [
    'The subscription changed. Try again.',
    'retry',
    RETRY,
  ],
  subscription_uncertain: [
    "Row-Bot couldn't confirm the sign-in. Check the subscription before trying again.",
    'review',
    PROVIDERS,
  ],

  // Setup
  invalid_onboarding_command: ["That setup choice isn't valid.", 'review'],
  invalid_setup: ['Check the setup details and try again.', 'review'],
  onboarding_changed: [
    'Setup changed. Refresh it before making another choice.',
    'retry',
    RETRY,
  ],
  onboarding_command_conflict: [
    'This setup step changed. Refresh Setup, then try again.',
    'retry',
    RETRY,
  ],
  onboarding_config_unavailable: [
    "Saved setup couldn't be read. Its file was kept as it was.",
    'retry',
    RETRY,
  ],
  onboarding_model_required: [
    'Choose a model that is available, then try this step again.',
    'review',
    CHOOSE_MODEL,
  ],

  // Settings
  invalid_settings_command: [
    'Check this setting and choose a supported value.',
    'review',
  ],
  settings_action_unavailable: [
    'This uses its own setup or account steps.',
    'review',
  ],
  settings_changed: [
    'Settings changed after you opened them. Load them again, then make your change.',
    'retry',
    RETRY,
  ],
  settings_review_changed: [
    'The setting changed before it was saved. Try again.',
    'retry',
    RETRY,
  ],
  settings_save_unconfirmed: [
    "Row-Bot couldn't confirm the save. Check the setting before saving again.",
    'retry',
    RETRY,
  ],
  settings_unavailable: [
    "Saved settings couldn't be read. Your values were kept.",
    'retry',
    RETRY,
  ],

  // Updates and import
  invalid_migration_command: [
    "That import step isn't valid.",
    'review',
    MIGRATION,
  ],
  migration_source_not_found: [
    "That app isn't in its usual folder. Enter the folder it uses.",
    'review',
  ],
  invalid_migration_selection: [
    'Choose a source, a target and the items to import, then scan again.',
    'review',
  ],
  invalid_update_command: [
    'Choose a supported update action and try again.',
    'review',
  ],
  migration_apply_busy: [
    'An import is already running. Check its progress before starting another.',
    'retry',
    RETRY,
  ],
  migration_changed: [
    'The source or target changed. Scan the folders again before importing.',
    'retry',
    RETRY,
  ],
  migration_command_conflict: [
    'This import no longer matches the preview. Scan again.',
    'retry',
    RETRY,
  ],
  migration_confirmation_required: [
    'Confirm the import before it starts.',
    'review',
  ],
  migration_path_escaped: [
    'That path is outside the folder you chose.',
    'review',
  ],
  migration_plan_missing: [
    'This import preview expired. Scan the folders again.',
    'retry',
    RETRY,
  ],
  migration_plan_too_large: [
    'There is too much to import at once. Choose fewer items.',
    'review',
  ],
  migration_receipt_missing: [
    "Row-Bot can't find the earlier import. Check the target folder before scanning again.",
    'review',
    MIGRATION,
  ],
  update_changed: [
    'Update status changed. Refresh it before choosing an action.',
    'retry',
    RETRY,
  ],
  update_command_conflict: [
    'This update action no longer matches the one you started. Start it again.',
    'review',
    UPDATES,
  ],
  update_install_busy: ['An update is already installing.', 'retry', RETRY],
  update_job_missing: [
    "Row-Bot can't find that installation. Check the installed version before trying again.",
    'review',
    UPDATES,
  ],
  update_unavailable: [
    "Updates aren't available when Row-Bot runs from source.",
    'none',
  ],

  // Workflows
  invalid_task_approval: ["That workflow approval isn't valid.", 'review'],
  invalid_task_fields: ["Check the workflow's fields before saving.", 'review'],
  invalid_task_graph: [
    'Check the workflow steps and their fields before saving.',
    'review',
  ],
  invalid_task_identity: [
    "That workflow couldn't be matched. Open it again.",
    'review',
  ],
  invalid_task_query: ["That workflow search isn't valid.", 'review'],
  invalid_task_revision: [
    'The workflow changed. Open it again.',
    'retry',
    RETRY,
  ],
  invalid_task_schedule: [
    'Check the schedule and time zone before saving.',
    'review',
  ],
  invalid_task_settings: [
    "Check the workflow's settings before saving.",
    'review',
  ],
  invalid_template: ["That template isn't available.", 'review'],
  task_advanced_edit_required: [
    'This workflow uses advanced steps. Edit it in the step editor so they are kept.',
    'review',
  ],
  task_approval_expired: [
    'This workflow approval expired. Refresh the run.',
    'retry',
    RETRY,
  ],
  task_approval_revision_conflict: [
    'This approval changed. Refresh and answer the current one.',
    'retry',
    RETRY,
  ],
  task_approval_unconfirmed: [
    "The approval was recorded, but Row-Bot couldn't confirm the run continued. Check the run; the decision won't be repeated.",
    'review',
    RETRY,
  ],
  task_creation_conflict: [
    'A workflow was already created from this. Check Workflows before creating another.',
    'review',
  ],
  task_delete_review_required: [
    'Review the workflow before deleting it.',
    'review',
  ],
  task_delete_revision_conflict: [
    'The workflow changed. Look at it again before deleting.',
    'retry',
    RETRY,
  ],
  task_delete_schedule_unconfirmed: [
    "The workflow was deleted, but Row-Bot couldn't confirm its schedule was removed. Check Workflows.",
    'review',
    RETRY,
  ],
  task_delivery_review_required: [
    'This workflow delivers somewhere that needs a review before it changes.',
    'review',
  ],
  task_delivery_revision_conflict: [
    'Delivery settings changed. Try again.',
    'retry',
    RETRY,
  ],
  task_delivery_unconfirmed: [
    "Row-Bot couldn't confirm the delivery settings were saved. Check them before saving again.",
    'review',
    RETRY,
  ],
  task_graph_cycle: [
    'These workflows would run each other in a loop. Change the workflow steps.',
    'review',
  ],
  task_graph_invalid_reference: [
    "A step refers to a step that isn't there. Fix the reference before saving.",
    'review',
  ],
  task_graph_missing_subtask: [
    'A step runs a workflow that no longer exists. Choose a saved workflow.',
    'review',
  ],
  task_graph_read_unconfirmed: [
    "The steps were saved, but couldn't be read back. Try the save again.",
    'retry',
    RETRY,
  ],
  task_graph_too_large: [
    'These steps are too large to save. The saved steps were kept.',
    'review',
  ],
  task_graph_unavailable: [
    "These steps can't be edited here. They were kept as they were.",
    'review',
  ],
  task_metadata_too_large: ['This workflow is too large to save.', 'none'],
  task_metadata_unavailable: [
    "Workflows couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  task_not_found: ['This workflow is no longer available.', 'none'],
  task_policy_revision_conflict: [
    "The workflow's rules changed. Refresh and check them before running.",
    'retry',
    RETRY,
  ],
  task_policy_unavailable: [
    "The workflow's rules couldn't be read. Check its agent profile before running.",
    'review',
  ],
  task_review_unsupported_fields: [
    "This workflow has settings this editor can't change.",
    'review',
  ],
  task_time_passed: [
    'That time has passed. Pick a later time, or switch the workflow off.',
    'review',
  ],
  backup_not_row_bot: ["That file isn't a Row-Bot backup.", 'none'],
  backup_newer: [
    'That backup comes from a newer Row-Bot. Update Row-Bot, then restore it.',
    'update',
  ],
  backup_invalid: [
    "That backup is damaged or has files Row-Bot won't restore.",
    'none',
  ],
  backup_too_large: ['That backup is larger than Row-Bot can restore.', 'none'],
  backup_review_expired: [
    'That backup changed or the check is too old. Choose it again.',
    'review',
  ],
  backup_unavailable: [
    "That backup isn't in the Backups folder any more.",
    'none',
  ],
  backup_storage_unavailable: [
    "Row-Bot can't write to its Backups folder.",
    'none',
  ],
  data_job_running: [
    'A backup or restore is already running. Wait for it to finish.',
    'retry',
    RETRY,
  ],
  task_revision_conflict: [
    'This workflow changed. Refresh to see it; your edits are kept.',
    'retry',
    RETRY,
  ],
  task_run_draining: [
    'The previous step is still finishing. Refresh before answering.',
    'retry',
    RETRY,
  ],
  task_run_metadata_unavailable: [
    "Run history couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  task_run_not_found: ['That run is no longer available.', 'none'],
  task_run_unconfirmed: [
    "Row-Bot couldn't confirm the run started. Check its history; it won't be started twice.",
    'review',
    RETRY,
  ],
  task_saved_read_unconfirmed: [
    "The workflow was saved, but couldn't be read back. Check the workflow before saving again.",
    'review',
    RETRY,
  ],
  task_schedule_unconfirmed: [
    "The workflow was saved, but Row-Bot couldn't confirm its schedule. Check the workflow.",
    'review',
    RETRY,
  ],
  task_settings_model_unavailable: [
    "The chosen model isn't in the model list. Choose another model.",
    'review',
    CHOOSE_MODEL,
  ],
  task_settings_profile_conflict: [
    'The agent profile changed. Check it before saving; your edits are kept.',
    'review',
  ],
  task_settings_profile_unavailable: [
    "The chosen agent profile isn't available. Choose another.",
    'review',
  ],
  task_settings_read_unconfirmed: [
    "The settings were saved, but couldn't be read back. Try the save again.",
    'retry',
    RETRY,
  ],
  task_settings_too_large: [
    'These settings are too large to save. The saved settings were kept.',
    'review',
  ],
  task_settings_unavailable: [
    "These settings can't be read. The saved values were kept.",
    'review',
  ],
  task_webhook_unavailable: [
    "This workflow's webhook isn't available.",
    'none',
    TUNNEL,
  ],

  // Designs
  artifact_busy: [
    'The design is still saving another change. Try again in a moment.',
    'retry',
    RETRY,
  ],
  artifact_design_unconfirmed: [
    "Row-Bot couldn't confirm the design change. Check the design before trying again.",
    'review',
    RETRY,
  ],
  artifact_type_unavailable: [
    "This kind of design can't be opened here.",
    'none',
  ],
  asset_already_on_page: ['That image is already on this page.', 'review'],
  asset_content_unsafe: [
    "That file can't be used: its content isn't a safe image.",
    'review',
  ],
  asset_identity_conflict: [
    'Another file in this design already has that name. Rename it and try again.',
    'review',
  ],
  asset_still_referenced: [
    'This asset is still on a page. Remove it from the page first.',
    'review',
  ],
  asset_too_large: ['That file is too large for a design.', 'none'],
  asset_type_unavailable: ["That file type can't be used in a design.", 'none'],
  asset_unavailable: ['That asset is no longer in the design.', 'none'],
  design_catalog_too_large: [
    'The design library is too large to show here.',
    'none',
  ],
  design_catalog_unavailable: [
    "The design library couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  design_finding_unavailable: ['That review note no longer applies.', 'none'],
  design_preset_builtin: [
    "Built-in presets can't be changed. Duplicate it first.",
    'review',
  ],
  design_preset_exists: [
    'A preset with that name already exists. Choose another name.',
    'review',
  ],
  design_preset_logo_unavailable: [
    "The preset's logo is missing. Add it again.",
    'review',
  ],
  design_preset_unavailable: ['That preset is no longer available.', 'none'],
  design_review_too_large: [
    'This design is too large to review at once.',
    'none',
  ],
  design_review_unavailable: [
    "The design review couldn't run. Try again.",
    'retry',
    RETRY,
  ],
  brand_website_unavailable: [
    "Row-Bot couldn't read that website. Check the address; pages on this computer or your local network aren't read.",
    'review',
  ],
  editing_record_too_large: [
    'This design is too large to edit in the panel.',
    'review',
  ],
  element_unavailable: [
    'This part of the design changed. Refresh before editing it.',
    'retry',
    RETRY,
  ],
  export_busy: [
    'Another export is still running. Try again when it finishes.',
    'retry',
    RETRY,
  ],
  export_capacity_reached: [
    'Export storage is full. Earlier exports were kept.',
    'review',
  ],
  export_conflict: [
    'A file with that name already exists. Export again with another name.',
    'review',
  ],
  export_expired: [
    'This download expired. Export the design again.',
    'retry',
    RETRY,
  ],
  export_incomplete: [
    "The export didn't finish. Any partial file was kept.",
    'retry',
    RETRY,
  ],
  export_runtime_missing: [
    'Exports need Browser Automation. Install it in Settings › System.',
    'review',
  ],
  export_size_limit: ['The export is too large.', 'none'],
  export_storage_unavailable: [
    "Exports can't be saved right now. Try again.",
    'retry',
    RETRY,
  ],
  export_unavailable: [
    "This export isn't available. Check the design before exporting again.",
    'review',
  ],
  font_unavailable: ["That font isn't available.", 'review'],
  history_unavailable: [
    'That design version is gone. The current design is kept.',
    'review',
  ],
  interactive_publish_requires_all_pages: [
    'Publish all pages of this interactive design together.',
    'review',
  ],
  invalid_canvas: ["That page size isn't valid.", 'review'],
  invalid_design_checkpoint: ["That design version isn't available.", 'review'],
  invalid_design_control: ["That design setting isn't valid.", 'review'],
  invalid_edit: ['Check the design change before saving.', 'review'],
  invalid_export: ['Check the export format and options.', 'review'],
  invalid_page_range: ['Choose page numbers within this design.', 'review'],
  invalid_preview_identity: [
    'The design preview is out of date. Refresh it.',
    'retry',
    RETRY,
  ],
  invalid_share: ['Check the sharing options before continuing.', 'review'],
  page_unavailable: ['That page is no longer in the design.', 'none'],
  preview_page_limit: [
    'This design has too many pages to preview at once.',
    'none',
  ],
  preview_too_large: ['The preview is too large to show.', 'none'],
  private_preview_export_unavailable: [
    "This preview can't be exported.",
    'none',
  ],
  share_review_changed: [
    'The design or destination changed. Check sharing again before continuing.',
    'retry',
    RETRY,
  ],
  sharing_busy: [
    'Another share is finishing. Try again when it completes.',
    'retry',
    RETRY,
  ],
  sharing_incomplete: [
    "Sharing didn't finish. Check the destination before sharing again.",
    'review',
  ],
  sharing_media_limit: ['Choose up to four pages for X.', 'review'],
  sharing_outcome_uncertain: [
    "Row-Bot couldn't confirm whether it was shared. Check the destination before trying again.",
    'review',
  ],
  channel_unavailable: [
    "This channel isn't set up or isn't running. Check it in Channels.",
    'review',
    CHANNELS,
  ],
  delivery_unavailable: [
    "This channel can't send that kind of file.",
    'review',
  ],
  recipient_unavailable: ['Check who it goes to before sharing.', 'review'],

  // Code folders, Git and commands
  change_set_already_reverted: ['These changes were already undone.', 'none'],
  change_set_revision_conflict: [
    'The files changed since this was prepared. Refresh the changes, then try again.',
    'retry',
    RETRY,
  ],
  change_set_unavailable: ['Those changes are no longer available.', 'none'],
  clean_git_root_required: [
    'This needs a code folder with no uncommitted changes. Commit or discard them first.',
    'review',
  ],
  clone_source_invalid: [
    'Enter a Git address without a password in it, and not a local file path.',
    'review',
  ],
  developer_repository_action_denied: [
    "That Git action isn't allowed in this code folder.",
    'none',
  ],
  developer_repository_action_unavailable: [
    "That Git action isn't available here.",
    'none',
  ],
  developer_repository_outcome_uncertain: [
    "Row-Bot couldn't confirm whether the Git action finished. Check the code folder before trying again.",
    'review',
    RETRY,
  ],
  developer_repository_receipt_unavailable: [
    "Row-Bot can't find the earlier Git action. Check the code folder's current state.",
    'review',
  ],
  developer_repository_revision_conflict: [
    'The code folder changed. Refresh it, then try again.',
    'retry',
    RETRY,
  ],
  developer_runtime_status_unavailable: [
    "The code folder's run status couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  developer_worktree_status_unavailable: [
    "The worktree's status couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  directory_revision_conflict: [
    'The folder changed. Refresh it, then try again.',
    'retry',
    RETRY,
  ],
  dirty_git_root_required: ['There are no uncommitted changes to use.', 'none'],
  docker_sandbox_required: [
    'This needs the Docker sandbox. Switch the code folder to Docker first.',
    'review',
  ],
  edit_recovery_conflict: [
    'The files changed since this edit was saved. Check the code folder before trying again.',
    'review',
  ],
  file_review_conflict: [
    'The file changed since you opened it. Reload it, then try again.',
    'retry',
    RETRY,
  ],
  file_revision_conflict: [
    'The file changed after you opened it. Its current contents are kept; reload it and check again.',
    'retry',
    RETRY,
  ],
  folder_selection_denied: [
    "Row-Bot isn't allowed to use that folder. Choose another.",
    'review',
  ],
  folder_selection_required: ['Choose a folder first.', 'review'],
  folder_picker_requires_desktop: [
    'Choose the folder in the Row-Bot desktop app.',
    'none',
  ],
  native_reconnecting: [
    'Desktop features are reconnecting. Try again in a moment.',
    'retry',
  ],
  git_push_failed: [
    "Git couldn't push. Check the remote and your access, then try again.",
    'retry',
    RETRY,
  ],
  git_read_hooks_unavailable: [
    "This folder's Git settings run programs Row-Bot won't run while reading. Check its Git configuration.",
    'review',
  ],
  git_remote_required: [
    'This code folder has no remote. Add one first.',
    'review',
  ],
  git_root_required: [
    'This needs a Git repository. Start one in the folder, or choose a folder that has one.',
    'review',
  ],
  git_status_unavailable: [
    "Git status couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  inspector_unavailable: [
    "The code folder couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  invalid_branch_name: [
    "That branch name isn't allowed. Use letters, numbers, dashes and slashes.",
    'review',
  ],
  invalid_custom_tool_command: [
    "One of the custom tool's settings isn't valid.",
    'review',
  ],
  invalid_developer_repository_command: [
    "That Git action isn't valid.",
    'review',
  ],
  media_destination_conflict: [
    'A different file already has that name.',
    'review',
  ],
  media_destination_is_code_folder: [
    "The workspace folder is a code folder. Use the code folder's import instead.",
    'review',
  ],
  media_destination_unavailable: [
    "The workspace folder can't be used for this file.",
    'review',
  ],
  media_scope_conflict: ['That file belongs to another conversation.', 'none'],
  media_type_conflict: [
    "That file's type doesn't match its contents.",
    'review',
  ],
  custom_tool_draft_unavailable: [
    'That custom tool draft is gone. Start again.',
    'review',
  ],
  custom_tool_unavailable: [
    'That custom tool is gone. Check the list.',
    'review',
  ],
  custom_tool_receipt_unavailable: [
    "Row-Bot can't find the earlier custom tool step. Check the tool before trying again.",
    'review',
  ],
  custom_tool_revision_conflict: [
    'The custom tool changed. Try again with its current version.',
    'retry',
    RETRY,
  ],
  no_recoverable_repository_delete_owner: [
    "Deleting a code folder isn't done from here. Remove it from the list instead.",
    'none',
  ],
  process_admission_failed: [
    "The command couldn't start. Try again.",
    'retry',
    RETRY,
  ],
  process_cleanup_incomplete: [
    'The command stopped, but not everything was cleaned up. Check the Run tab.',
    'review',
  ],
  process_cleanup_unconfirmed: [
    "Row-Bot couldn't confirm the command stopped. Check the Run tab before starting another.",
    'retry',
    RETRY,
  ],
  process_command_invalid: ["That command isn't valid here.", 'review'],
  process_containment_unavailable: [
    "Commands can't run safely here right now.",
    'none',
  ],
  process_cursor_invalid: [
    'The command output is out of date. Load it again.',
    'retry',
    RETRY,
  ],
  process_history_incomplete: ['Some command history is missing.', 'none'],
  process_history_unavailable: [
    "Command history couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  process_limit: [
    'Too many commands are running. Stop one before starting another.',
    'review',
  ],
  process_owner_lost: [
    'Row-Bot lost track of this command. Check the Run tab.',
    'review',
  ],
  process_policy_denied: [
    "That command isn't allowed in this code folder.",
    'none',
  ],
  process_recovery_unavailable: ["This command can't be recovered.", 'none'],
  process_review_stale: [
    'The command or the folder rules changed. Check the command before starting it.',
    'retry',
    RETRY,
  ],
  process_start_unconfirmed: [
    "Row-Bot couldn't confirm the command started. Check the Run tab before starting it again.",
    'review',
    RETRY,
  ],
  process_unavailable: ['That command is no longer running.', 'none'],
  runtime_installation_command_unavailable: [
    "That install action isn't available.",
    'none',
  ],
  runtime_installation_owner_unavailable: [
    'Another install is still running. Wait for it to finish.',
    'retry',
    RETRY,
  ],
  runtime_installation_pending: [
    'An install is still running. Check it before starting another.',
    'review',
    RUNTIMES,
  ],
  runtime_installation_policy_changed: [
    'The install rules changed. Check the install again.',
    'retry',
    RETRY,
  ],
  runtime_installation_unavailable: [
    "Installing isn't available right now. Try again.",
    'retry',
    RETRY,
  ],
  runtime_installation_unconfirmed: [
    "Row-Bot couldn't confirm the install. Check the runtimes in Settings › MCP before trying again.",
    'review',
    RUNTIMES,
  ],
  runtime_plan_changed: [
    'The runtime changed. Check its install again.',
    'retry',
    RETRY,
  ],
  runtime_plan_unavailable: [
    "The install plan couldn't be prepared. Try again.",
    'retry',
    RETRY,
  ],
  runtime_review_changed: [
    'The runtime changed. Check the install again.',
    'retry',
    RETRY,
  ],
  sandbox_busy_or_pending_import: [
    'The sandbox is busy or has changes waiting to import. Import or discard them first.',
    'review',
  ],
  sandbox_change_already_imported: [
    'These changes were already imported.',
    'none',
  ],
  sandbox_change_revision_conflict: [
    'The sandbox changed. Refresh it, then try again.',
    'retry',
    RETRY,
  ],
  sandbox_change_unavailable: ['Those sandbox changes are gone.', 'none'],
  sandbox_history_unavailable: [
    "The sandbox history couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  sandbox_import_format_unavailable: [
    "These changes can't be imported automatically.",
    'none',
  ],
  sandbox_import_too_large: [
    'These changes are too large to import at once.',
    'none',
  ],
  sandbox_patch_unavailable: [
    "The sandbox changes couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  sandbox_process_unavailable: ["The sandbox isn't running.", 'review'],
  use_workspace_process_review: [
    'Installs and network commands run from the Run tab, where you check them first.',
    'review',
  ],
  use_workspace_setup: [
    'To clone a repository, use Add resource or ask in the conversation.',
    'review',
  ],
  workspace_creation_denied: [
    "Row-Bot can't create a folder there. Check the folder's permissions or choose another place.",
    'review',
  ],
  workspace_creation_unconfirmed: [
    "Row-Bot couldn't confirm the folder was created. Check the folder before trying again.",
    'review',
  ],
  workspace_destination_exists: [
    'That folder already exists. Add the existing folder, choose another name or place, or cancel.',
    'review',
  ],
  workspace_destination_not_empty: [
    'This folder now has files in it. Check it before adding it as an existing folder.',
    'review',
  ],
  workspace_identity_conflict: [
    'That folder no longer matches. Choose it again.',
    'review',
  ],
  workspace_import_not_admitted: [
    "This import can't go ahead. Check the current changes before trying again.",
    'review',
  ],
  workspace_import_review_changed: [
    'The changes changed. Check them again.',
    'retry',
    RETRY,
  ],
  workspace_import_unavailable: ["Those changes can't be imported.", 'none'],
  workspace_name_invalid: [
    'Choose a folder name without slashes or reserved names.',
    'review',
  ],
  workspace_path_denied: ["Row-Bot isn't allowed to use that folder.", 'none'],
  workspace_read_hooks_unavailable: [
    "This folder's Git settings run programs Row-Bot won't run while reading. Check its Git configuration.",
    'review',
  ],
  workspace_recovery_conflict: [
    'The chosen folder changed. Check it before continuing.',
    'review',
  ],
  workspace_registration_failed: [
    "The folder couldn't be added. Try again.",
    'retry',
    RETRY,
  ],
  workspace_undo_approval_required: [
    'Undoing these changes needs your approval in the conversation.',
    'review',
  ],
  workspace_undo_not_admitted: [
    "This undo can't go ahead. Keep the current changes open while you check them.",
    'review',
  ],
  workspace_undo_proof_unavailable: [
    "The original files needed to undo this are missing. Your files weren't changed.",
    'review',
  ],
  workspace_undo_review_changed: [
    'The files or folder rules changed. Check the changes again.',
    'retry',
    RETRY,
  ],
  workspace_undo_too_large: ['Too many changes to undo at once.', 'none'],
  workspace_undo_unavailable: [
    "These changes can't be undone here. Ask Row-Bot to undo them.",
    'none',
  ],
  workspace_undo_unconfirmed: [
    "Row-Bot couldn't confirm the undo. Check the files before trying again.",
    'review',
  ],
  worktree_create_failed: [
    "The worktree couldn't be created. Try again.",
    'retry',
    RETRY,
  ],
  worktree_exists: ['A worktree with that name already exists.', 'review'],
  worktree_unavailable: ["The worktree isn't available.", 'none'],
  pull_request_failed: [
    "The pull request couldn't be created. Check your GitHub access, then try again.",
    'retry',
    GITHUB,
  ],

  // Designs and code folders in a conversation
  invalid_resource: [
    "That design or code folder can't be used here.",
    'review',
  ],
  resource_ambiguous: [
    'Choose one design and one code folder at most.',
    'review',
  ],
  resource_binding_revoked: [
    'This design or code folder was removed from the conversation. Add it again.',
    'review',
  ],
  resource_limit: [
    'This conversation already has the most designs and code folders it can hold.',
    'review',
  ],
  resource_revision_conflict: [
    'This changed somewhere else. Load it again, then make your change.',
    'retry',
    RETRY,
  ],
  resource_setup_partial: [
    "Setting up the design or code folder didn't finish. Try again; nothing will be created twice.",
    'retry',
    RETRY,
  ],
  resource_not_discardable: [
    'Row-Bot can only undo a design or code folder it created in this conversation and nothing else uses. You can still remove it from this conversation in Context.',
    'none',
  ],
  resource_not_empty: [
    'This code folder has files in it now, and Undo would delete them, so Row-Bot left it as it is. You can still remove it from this conversation in Context; its files stay.',
    'none',
  ],
  resource_state_invalid: [
    'The design or code folder is in an unexpected state. Try again.',
    'retry',
    RETRY,
  ],
  resource_too_large: ["It's too large to open here.", 'none'],
  resource_unavailable: [
    'That design or code folder is no longer available.',
    'none',
  ],

  // Managed browser
  browser_action_denied: [
    "The managed browser isn't allowed to do that.",
    'none',
  ],
  browser_action_failed: [
    "The browser action didn't work. Try again.",
    'retry',
    RETRY,
  ],
  browser_navigation_denied: [
    "The managed browser can't open that address.",
    'review',
  ],
  browser_outcome_uncertain: [
    "Row-Bot couldn't confirm whether the browser action finished. Check the browser before trying again.",
    'review',
    RETRY,
  ],
  browser_receipt_unavailable: [
    "Row-Bot can't find the earlier browser action. Check the browser's current page.",
    'review',
  ],
  browser_revision_conflict: [
    'The page changed. Look at it again, then try again.',
    'retry',
    RETRY,
  ],
  browser_session_inactive: [
    "The managed browser isn't running. Open it from the conversation, then try again.",
    'review',
  ],
  browser_status_unavailable: [
    "The browser's status couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  browser_window_unavailable: [
    'The browser window is closed. Open it again, then try.',
    'review',
  ],
  exact_page_target_and_hidden_text_contract_required: CANT_DO_FROM_HERE,
  exact_page_target_contract_required: CANT_DO_FROM_HERE,
  invalid_browser_command: ["That browser action isn't valid.", 'review'],
  invalid_browser_url: [
    "That web address isn't valid. Check it and try again.",
    'review',
  ],
  owned_tab_identity_contract_required: CANT_DO_FROM_HERE,
  semantic_page_observation_contract_required: CANT_DO_FROM_HERE,
  use_computer_use_for_external_browser: [
    "Row-Bot can't control your own browser from here. Ask it to use Computer Use instead.",
    'none',
  ],

  // Computer use (the card in the conversation)
  computer_use_busy: [
    'Computer use is changing over right now. Wait a moment, then try again.',
    'retry',
    RETRY,
  ],
  computer_use_inactive: [
    "Row-Bot isn't using your computer in this conversation.",
    'none',
  ],
  computer_use_local_only: [
    'Computer use can only be watched and controlled on the computer it runs on.',
    'none',
  ],
  computer_use_not_paused: [
    "Computer use isn't paused, so there's nothing to resume.",
    'none',
  ],
  computer_use_outcome_uncertain: [
    "Row-Bot couldn't confirm that finished. Check the card before trying again.",
    'review',
    RETRY,
  ],
  computer_use_resume_failed: [
    "Row-Bot couldn't pick up where it left off. Stop, then ask again in the conversation.",
    'review',
  ],
  computer_use_revision_conflict: [
    'The picture changed. Try again to see the latest one.',
    'retry',
    RETRY,
  ],
  invalid_computer_use_command: [
    "That computer-use action isn't valid.",
    'review',
  ],

  // Buddy
  buddy_account_unavailable: [
    "Buddy needs an account that isn't connected.",
    'review',
    ACCOUNTS,
  ],
  buddy_action_blocked: ["Buddy can't do that right now.", 'none'],
  buddy_asset_unavailable: ['That Buddy look is no longer available.', 'none'],
  buddy_command_unavailable: ["That Buddy action isn't available.", 'none'],
  buddy_config_unavailable: [
    "Buddy's settings couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  buddy_cursor_changed: [
    'The Buddy looks changed. Load them again.',
    'retry',
    RETRY,
  ],
  buddy_data_policy_denied: ["Buddy isn't allowed to use that data.", 'none'],
  buddy_media_capability_unavailable: [
    'Generating a Buddy needs an image model. Choose one first.',
    'review',
    IMAGE_MODEL,
  ],
  buddy_outcome_uncertain: [
    "Row-Bot couldn't confirm the Buddy change. Check Buddy before trying again.",
    'review',
    RETRY,
  ],
  buddy_pack_limit: [
    'You have the most Buddy looks Row-Bot keeps. Remove one first.',
    'review',
    BUDDY,
  ],
  buddy_pack_unavailable: ['That Buddy look is no longer available.', 'none'],
  buddy_policy_changed: [
    "Buddy's settings changed. Try again.",
    'retry',
    RETRY,
  ],
  buddy_policy_unavailable: [
    "Buddy's settings couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  buddy_review_changed: [
    'The Buddy change was edited somewhere else. Try again.',
    'retry',
    RETRY,
  ],
  buddy_revision_conflict: [
    'Buddy changed somewhere else. Try again with the current settings.',
    'retry',
    RETRY,
  ],
  buddy_tool_policy_denied: ["Buddy isn't allowed to use that tool.", 'none'],
  buddy_tool_unavailable: [
    'The tool Buddy needs is turned off.',
    'review',
    TOOLS,
  ],
  hatch_job_unavailable: [
    'That Buddy generation is no longer running.',
    'none',
  ],
  hatch_source_changed: [
    'The Buddy look changed. Start the generation again.',
    'retry',
    RETRY,
  ],
  hatch_worker_unavailable: [
    "Buddy generation isn't available right now. Try again.",
    'retry',
    RETRY,
  ],
  invalid_buddy_command: ["That Buddy action isn't valid.", 'review'],
  invalid_buddy_preferences: ["One of Buddy's settings isn't valid.", 'review'],
  invalid_hatch_request: [
    'Check the Buddy description and try again.',
    'review',
  ],

  // Channels
  channel_operation_unavailable: [
    "That channel action isn't available.",
    'none',
    CHANNELS,
  ],
  channel_operation_unconfirmed: [
    "Row-Bot couldn't confirm the channel change. Check the channel before trying again.",
    'review',
    CHANNELS,
  ],
  channel_pairing_unconfirmed: [
    "Pairing couldn't be confirmed. Check the channel, then pair again if needed.",
    'review',
    CHANNELS,
  ],
  channel_review_stale: [
    'The channel changed. Try again with its current settings.',
    'retry',
    RETRY,
  ],
  channel_revoke_unconfirmed: [
    "Row-Bot couldn't confirm the removal. Check the channel's approved people.",
    'review',
    CHANNELS,
  ],
  channel_start_failed: [
    "The channel didn't start. Check its settings, then start it again.",
    'review',
    CHANNELS,
  ],
  channel_status_unavailable: [
    "The channel's status couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  channel_not_running: [
    'Start the channel first, then send the test message.',
    'review',
    CHANNELS,
  ],
  channel_test_target_missing: [
    "Row-Bot doesn't know your account on this channel yet. Add your user ID or pair your account, then try again.",
    'review',
    CHANNELS,
  ],

  // Plugins, MCP and skills
  disable_plugin_to_configure: [
    'Turn the plugin off before changing its settings.',
    'review',
    PLUGINS,
  ],
  invalid_plugin_command: [
    "That plugin action isn't valid.",
    'review',
    PLUGINS,
  ],
  invalid_plugin_lifecycle_command: [
    "That plugin action isn't valid.",
    'review',
    PLUGINS,
  ],
  invalid_plugin_query: ["That plugin search isn't valid.", 'review'],
  mcp_catalog_stale: [
    "The server's tools changed. Test the server again.",
    'retry',
    RETRY,
  ],
  mcp_catalog_unavailable: [
    "The server's tools couldn't be read. Test it again.",
    'retry',
    RETRY,
  ],
  mcp_cleanup_incomplete: [
    "The server stopped, but some of its files couldn't be cleaned up.",
    'review',
    MCP,
  ],
  mcp_configuration_recovery_required: [
    'The MCP settings need repair.',
    'review',
    MCP,
  ],
  mcp_configuration_unavailable: [
    "MCP settings couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  mcp_configuration_unconfirmed: [
    "Row-Bot couldn't confirm the MCP change. Check the server list before trying again.",
    'review',
    MCP,
  ],
  mcp_policy_unavailable: [
    "The MCP tool rules couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  mcp_runtime_disabled: [
    'This MCP server is off. Turn it on first.',
    'review',
    MCP,
  ],
  mcp_runtime_identity_changed: [
    'The MCP server changed. Test it again.',
    'retry',
    RETRY,
  ],
  mcp_runtime_missing: [
    "The MCP server's program isn't installed.",
    'review',
    RUNTIMES,
  ],
  mcp_runtime_recovery_required: [
    'The MCP server needs repair.',
    'review',
    MCP,
  ],
  mcp_runtime_review_stale: [
    'The MCP server changed. Try again.',
    'retry',
    RETRY,
  ],
  mcp_runtime_unavailable: [
    "The MCP server isn't responding. Try again.",
    'retry',
    RETRY,
  ],
  mcp_runtime_unconfirmed: [
    "Row-Bot couldn't confirm the MCP server's state. Check it before trying again.",
    'review',
    MCP,
  ],
  mcp_server_collision: [
    'A server with that name already exists. Choose another name.',
    'review',
  ],
  plugin_already_disabled: ['The plugin is already off.', 'none'],
  plugin_catalog_unavailable: [
    "The plugin list couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  plugin_changed: ['The plugin changed. Try again.', 'retry', RETRY],
  plugin_checksum_unavailable: [
    "The marketplace lists no checksum for this plugin, so Row-Bot won't download it.",
    'none',
  ],
  plugin_configuration_unconfirmed: [
    "Row-Bot couldn't confirm the plugin's settings were saved. Check them before trying again.",
    'review',
    PLUGINS,
  ],
  plugin_enablement_unconfirmed: [
    "Row-Bot couldn't confirm whether the plugin turned on. Check the plugin list.",
    'review',
    PLUGINS,
  ],
  plugin_lifecycle_changed: [
    'The plugin changed while installing. Try again.',
    'retry',
    RETRY,
  ],
  plugin_lifecycle_receipt_unavailable: [
    "Row-Bot can't find the earlier plugin step. Check the plugin list before trying again.",
    'review',
    PLUGINS,
  ],
  plugin_lifecycle_worker_unavailable: [
    "Plugin installs aren't available right now. Try again.",
    'retry',
    RETRY,
  ],
  plugin_marketplace_entry_unavailable: [
    'That plugin is no longer in the marketplace.',
    'none',
  ],
  plugin_marketplace_unavailable: [
    "The plugin marketplace couldn't be reached. Try again.",
    'retry',
    RETRY,
  ],
  plugin_not_found: ["That plugin isn't installed.", 'none'],
  plugin_operation_unconfirmed: [
    "Row-Bot couldn't confirm the plugin change. Check the plugin list before trying again.",
    'review',
    PLUGINS,
  ],
  plugin_review_changed: ['The plugin changed. Try again.', 'retry', RETRY],
  plugin_runtime_unavailable: [
    "The plugin isn't running. Turn it on, then try again.",
    'review',
    PLUGINS,
  ],
  plugin_setup_or_test_required: [
    "Finish the plugin's setup and test it first.",
    'review',
    PLUGINS,
  ],
  plugin_source_unavailable: [
    "The plugin's folder isn't where the marketplace index says it is.",
    'none',
  ],
  plugin_source_unsupported: [
    'This plugin can’t be installed from where its marketplace entry points.',
    'none',
  ],
  plugin_already_installed: [
    'This plugin is already installed. Update it instead.',
    'none',
  ],
  plugin_not_installed: ['This plugin isn’t installed.', 'none'],
  plugin_update_unavailable: [
    'There’s no newer version of this plugin to update to.',
    'none',
  ],
  invalid_skill_action: ["That skill action isn't valid.", 'review', SKILLS],
  invalid_skill_command: ["That skill action isn't valid.", 'review', SKILLS],
  invalid_skill_fields: [
    "Check the skill's name and fields, then try again.",
    'review',
  ],
  invalid_skill_import: [
    "That skill can't be imported. Check the SKILL.md file.",
    'review',
  ],
  invalid_skill_query: ["That skill search isn't valid.", 'review'],
  invalid_skill_target: ['That skill is no longer there.', 'review', SKILLS],
  skill_catalog_changed: [
    'The search results changed. Search again before previewing.',
    'retry',
    RETRY,
  ],
  skill_catalog_expired: [
    'The search results expired. Search again.',
    'retry',
    RETRY,
  ],
  skill_command_conflict: [
    'This skill step no longer matches the one you started. Start it again.',
    'review',
    SKILLS,
  ],
  skill_confirmation_required: [
    'Confirm the skill before it is installed.',
    'review',
  ],
  skill_exists: [
    'A skill with that name already exists. Choose another name.',
    'review',
  ],
  skill_install_pending: [
    'This skill is still installing. Check it before starting again.',
    'review',
    SKILLS,
  ],
  skill_missing: ['That skill is no longer there.', 'none'],
  skill_not_installed: ["That skill isn't installed.", 'none'],
  skill_operation_unavailable: ["That skill action isn't available.", 'none'],
  skill_outcome_uncertain: [
    "Row-Bot couldn't confirm the skill change. Check your skills before trying again.",
    'review',
    SKILLS,
  ],
  skill_preview_changed: [
    'The skill changed since it was checked. Check it again before installing.',
    'retry',
    RETRY,
  ],
  skill_preview_expired: [
    'This skill preview expired. Check it again before installing.',
    'retry',
    RETRY,
  ],
  skill_preview_unavailable: [
    "Row-Bot couldn't read this skill's files from its source. Try again later or choose another skill.",
    'retry',
    RETRY,
  ],
  skill_proposal_changed: [
    'The suggested skill change was updated. Look at it again.',
    'retry',
    RETRY,
  ],
  skill_proposals_unavailable: [
    "Skill suggestions couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  skill_receipt_missing: [
    "Row-Bot can't find the earlier install. Check your skills before trying again.",
    'review',
    SKILLS,
  ],
  skill_record_changed: ['The skill changed. Try again.', 'retry', RETRY],
  skill_revision_conflict: [
    'The skill changed somewhere else. Try again with the current version.',
    'retry',
    RETRY,
  ],
  skill_source_timeout: [
    "The skill's source took too long to answer. Try again in a moment.",
    'retry',
    RETRY,
  ],
  skill_unavailable: ["Skills couldn't be read. Try again.", 'retry', RETRY],
  skills_response_too_large: [
    'There are too many skills to show here.',
    'none',
  ],

  // Agent profiles and goals
  goal_action_unavailable: [
    "That goal action isn't available right now.",
    'none',
  ],
  goal_library_too_large: ['There are too many saved goals to show.', 'none'],
  goal_operation_unconfirmed: [
    "Row-Bot couldn't confirm the goal change. Check the goal before trying again.",
    'review',
    RETRY,
  ],
  goal_review_stale: ['The goal changed. Try again.', 'retry', RETRY],
  goal_revision_conflict: [
    'The goal changed somewhere else. Try again with the current goal.',
    'retry',
    RETRY,
  ],
  profile_library_too_large: [
    'There are too many agent profiles to show.',
    'none',
  ],
  profile_mutation_rejected: [
    "That change to the profile isn't allowed.",
    'review',
  ],
  profile_operation_unconfirmed: [
    "Row-Bot couldn't confirm the profile change. Check the profile before trying again.",
    'review',
  ],
  profile_read_only: [
    "Built-in profiles can't be changed. Duplicate it first.",
    'review',
  ],
  profile_review_stale: ['The profile changed. Try again.', 'retry', RETRY],
  profile_revision_conflict: [
    'The profile changed somewhere else. Try again.',
    'retry',
    RETRY,
  ],
  profile_slug_conflict: [
    'A profile with that name already exists. Choose another name.',
    'review',
  ],
  profile_snapshot_unavailable: [
    "The profile for the waiting message couldn't be recovered. Check the message before continuing.",
    'review',
  ],
  profile_unavailable: [
    "The chosen profile isn't available. Choose another profile.",
    'review',
  ],

  // Knowledge, documents, wiki and Dream Cycle
  document_action_rejected: [
    "That document action isn't allowed right now.",
    'review',
  ],
  document_control_unavailable: [
    "Document processing can't be changed right now. Try again.",
    'retry',
    RETRY,
  ],
  document_operation_unavailable: [
    "That document action isn't available.",
    'none',
  ],
  document_outcome_uncertain: [
    "Row-Bot couldn't confirm the document change. Check the documents list before trying again.",
    'review',
    RETRY,
  ],
  document_processing_authority_changed: [
    'The documents list changed. Try again.',
    'retry',
    RETRY,
  ],
  document_processing_authority_unavailable: [
    "Document processing isn't available right now. Try again.",
    'retry',
    RETRY,
  ],
  document_processing_denied: [
    "This chat can't process documents: its Approvals are set to Block, or its agent profile leaves documents out. Open a new chat, then try again.",
    'none',
  ],
  document_processing_search_model_missing: [
    'Documents need the search model on this computer first. In Documents › Advanced, under Search model files, choose Download.',
    'none',
    open('documents', 'Documents'),
  ],
  document_processing_model_unavailable: [
    'Document processing needs a model. Choose one.',
    'review',
    CHOOSE_MODEL,
  ],
  document_processing_policy_changed: [
    'Document settings changed. Try again.',
    'retry',
    RETRY,
  ],
  document_processing_policy_unavailable: [
    "Document settings couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  document_processing_proof_unavailable: [
    "Row-Bot couldn't confirm which documents to process. Try again.",
    'retry',
    RETRY,
  ],
  document_processing_reasoning_unavailable: [
    "The chosen model's thinking level can't be used for documents. Choose another model.",
    'review',
    CHOOSE_MODEL,
  ],
  document_processing_uncertain: [
    "Row-Bot couldn't confirm whether processing started. Check the documents list before trying again.",
    'review',
    RETRY,
  ],
  document_processing_unavailable: [
    "Document processing isn't available right now. Try again.",
    'retry',
    RETRY,
  ],
  document_processing_worker_unavailable: [
    "The document worker isn't running. Try again in a moment.",
    'retry',
    RETRY,
  ],
  document_queue_changed: [
    'The documents list changed. Load it again.',
    'retry',
    RETRY,
  ],
  document_queue_unavailable: [
    "The documents list couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  document_removal_pending: ['This document is already being removed.', 'none'],
  document_review_changed: [
    'The documents changed. Check them again.',
    'retry',
    RETRY,
  ],
  document_review_unavailable: [
    "The document check couldn't be prepared. Try again.",
    'retry',
    RETRY,
  ],
  document_upload_closed: [
    'This upload has finished or was cancelled. Start a new upload.',
    'review',
    DOCUMENTS,
  ],
  document_upload_timeout: [
    'The upload took too long. Try again.',
    'retry',
    RETRY,
  ],
  document_upload_uncertain: [
    "Row-Bot couldn't confirm the upload. Check the documents list before uploading again.",
    'review',
    DOCUMENTS,
  ],
  document_upload_unavailable: [
    "Uploading documents isn't available right now. Try again.",
    'retry',
    RETRY,
  ],
  dream_changed: ['Dream Cycle changed. Try again.', 'retry', RETRY],
  dream_disabled: ['Dream Cycle is off. Turn it on first.', 'review', DREAM],
  dream_failed: ["Dream Cycle didn't finish. Try again.", 'retry', RETRY],
  dream_operation_unavailable: [
    "That Dream Cycle action isn't available.",
    'none',
  ],
  dream_outcome_uncertain: [
    "Row-Bot couldn't confirm whether Dream Cycle ran. Check Monitor before running it again.",
    'review',
    RETRY,
  ],
  dream_unavailable: [
    "Dream Cycle isn't available right now. Try again.",
    'retry',
    RETRY,
  ],
  invalid_document_command: ["That document action isn't valid.", 'review'],
  invalid_document_control: ["That document setting isn't valid.", 'review'],
  invalid_document_processing: [
    "Those documents can't be processed together. Choose them again.",
    'review',
  ],
  invalid_document_queue: [
    'The documents list changed. Load it again.',
    'retry',
    RETRY,
  ],
  invalid_document_target: ['That document is no longer there.', 'review'],
  invalid_document_upload: [
    "One of the files can't be uploaded. Check its type and size.",
    'review',
  ],
  invalid_dream_command: ["That Dream Cycle action isn't valid.", 'review'],
  invalid_insight_command: ["That insight action isn't valid.", 'review'],
  invalid_knowledge_command: ["That memory action isn't valid.", 'review'],
  invalid_knowledge_fields: [
    "Check the memory's fields, then try again.",
    'review',
  ],
  invalid_knowledge_query: ["That memory search isn't valid.", 'review'],
  invalid_knowledge_target: ['That memory is no longer there.', 'review'],
  invalid_monitor_query: ["That Monitor filter isn't valid.", 'review'],
  invalid_relation_command: [
    "That link between memories isn't valid.",
    'review',
  ],
  invalid_relation_page: [
    'The links changed. Load them again.',
    'retry',
    RETRY,
  ],
  invalid_relation_target: ['That memory is no longer there.', 'review'],
  invalid_relation_type: [
    'Name how the memories relate, such as part_of or uses.',
    'review',
  ],
  invalid_wiki_command: ["That wiki action isn't valid.", 'review', WIKI],
  invalid_wiki_query: ["That wiki search isn't valid.", 'review'],
  insight_proposal_draft_only: [
    "Row-Bot can't make this kind of change. Make it yourself if you agree, then reject the proposal.",
    'none',
  ],
  insight_proposal_finished: [
    'This suggestion was already applied or dismissed.',
    'none',
  ],
  insight_proposal_unavailable: [
    'This suggestion is no longer available.',
    'none',
  ],
  insight_receipt_unavailable: [
    "Row-Bot can't find the earlier step for this insight. Check Insights before trying again.",
    'review',
  ],
  insight_revision_conflict: [
    'This insight changed. Try again.',
    'retry',
    RETRY,
  ],
  insight_unavailable: ['This insight is no longer available.', 'none'],
  knowledge_changed: [
    'This memory changed. Try again with its current version.',
    'retry',
    RETRY,
  ],
  knowledge_missing: ['This memory no longer exists.', 'none'],
  knowledge_operation_unavailable: [
    "That memory action isn't available.",
    'none',
  ],
  knowledge_outcome_uncertain: [
    "Row-Bot couldn't confirm the memory change. Check the memory before trying again.",
    'review',
    RETRY,
  ],
  knowledge_unavailable: [
    "Memory couldn't be read. Try again.",
    'retry',
    RETRY,
  ],
  relation_changed: ['The link changed. Try again.', 'retry', RETRY],
  relation_type_too_vague: [
    'That kind of link is too vague to be useful. Say how the memories relate, such as part_of, uses or works_on.',
    'review',
  ],
  wiki_article_unavailable: ['That wiki page is gone.', 'none'],
  wiki_cancelled: ['The wiki sync was cancelled.', 'retry', RETRY],
  wiki_changed: ['The wiki changed. Try again.', 'retry', RETRY],
  wiki_conflict: [
    'This wiki page changed in two places. Check it before saving.',
    'review',
  ],
  wiki_conflict_review_required: [
    'Check the changed wiki pages first.',
    'review',
  ],
  wiki_disabled: ['The wiki vault is off. Turn it on first.', 'review', WIKI],
  wiki_enumeration_incomplete: [
    "Some wiki pages couldn't be listed. Try again.",
    'retry',
    RETRY,
  ],
  wiki_operation_unavailable: ["That wiki action isn't available.", 'none'],
  wiki_outcome_uncertain: [
    "Row-Bot couldn't confirm the wiki change. Check the vault before trying again.",
    'review',
    WIKI,
  ],
  wiki_review_too_large: ['Too many wiki changes to check at once.', 'none'],
  wiki_scope_unavailable: ["That wiki folder isn't allowed.", 'none'],
  wiki_source_unavailable: [
    "The wiki vault folder couldn't be read. Check that it still exists.",
    'review',
    WIKI,
  ],
  wiki_unavailable: [
    "The wiki vault couldn't be read. Try again.",
    'retry',
    RETRY,
  ],

  // Voice
  audio_decode_timeout: [
    'Reading the audio took too long. Try a shorter dictation.',
    'retry',
    RETRY,
  ],
  audio_duration_invalid: ['Keep each dictation under 30 seconds.', 'review'],
  audio_receive_timeout: [
    'The audio upload took too long. Start Dictate again when the connection is ready.',
    'retry',
    RETRY,
  ],
  empty_audio: [
    'No audio was recorded. Check the microphone permission, then try again.',
    'review',
  ],
  ffmpeg_unavailable: [
    "The audio decoder isn't available. Check Voice setup before using Dictate.",
    'review',
    VOICE,
  ],
  invalid_voice_event: ["Voice couldn't start. Try again.", 'retry', RETRY],
  invalid_voice_sdp: ["Voice couldn't start. Try again.", 'retry', RETRY],
  malformed_audio: [
    "The recording couldn't be read. Start Dictate again.",
    'retry',
    RETRY,
  ],
  realtime_auth_unavailable: [
    'Realtime Talk needs an OpenAI key or sign-in.',
    'review',
    PROVIDERS,
  ],
  realtime_credential_expired: [
    'The Realtime Talk session expired. Start Talk again.',
    'retry',
    RETRY,
  ],
  realtime_credential_unavailable: [
    "Realtime Talk couldn't get a session. Try again.",
    'retry',
    RETRY,
  ],
  realtime_exchange_timeout: [
    'Realtime Talk took too long to connect. Try again.',
    'retry',
    RETRY,
  ],
  realtime_provider_unavailable: [
    "The Realtime Talk service isn't responding. Try again.",
    'retry',
    RETRY,
  ],
  realtime_quota_or_rate_limit: [
    'The Realtime Talk provider refused the call: its quota or rate limit was reached.',
    'review',
    PROVIDERS,
  ],
  sensevoice_unavailable: [
    "SenseVoice isn't ready on this computer. Install it in Voice settings, or choose Whisper.",
    'review',
    VOICE,
  ],
  transcript_too_large: [
    'The transcript was too long for Dictate. Try a shorter recording.',
    'review',
  ],
  unsupported_audio_type: [
    "This audio format isn't supported. Try Dictate in another browser.",
    'review',
  ],
  voice_event_consumed: [
    'That voice step already happened. Start Talk again.',
    'retry',
    RETRY,
  ],
  voice_event_too_large: [
    'The voice data was too large. Try again.',
    'retry',
    RETRY,
  ],
  voice_exchange_consumed: [
    'That voice step already happened. Start Talk again.',
    'retry',
    RETRY,
  ],
  voice_operation_failed: [
    "Speech recognition didn't finish. Your draft is kept; start Dictate again.",
    'retry',
    RETRY,
  ],
  voice_output_consumed: ['That spoken reply was already played.', 'none'],
  voice_output_too_large: ['The spoken reply was too long to play.', 'none'],
  voice_output_unavailable: [
    "The spoken reply isn't available.",
    'retry',
    RETRY,
  ],
  voice_policy_changed: [
    'Voice settings changed. Start again.',
    'retry',
    RETRY,
  ],
  voice_provider_unavailable: [
    "The selected voice provider isn't available. Choose another in Voice settings.",
    'review',
    VOICE,
  ],
  voice_run_changed: [
    'The conversation moved on. Start Talk again.',
    'retry',
    RETRY,
  ],
  voice_sdp_too_large: ["Voice couldn't start. Try again.", 'retry', RETRY],
  voice_service_busy: [
    'Speech recognition is busy. Wait for it to finish, then try again.',
    'retry',
    RETRY,
  ],
  voice_session_busy: [
    'Voice is in use in another window or is still stopping. Finish it there, then try again.',
    'retry',
    RETRY,
  ],
  voice_session_expired: [
    'This voice session ended. Start again when you are ready.',
    'retry',
    RETRY,
  ],
  voice_session_limit: [
    'Voice is already in use in another window.',
    'retry',
    RETRY,
  ],
  voice_transcript_changed: [
    'The transcript changed. Try again.',
    'retry',
    RETRY,
  ],
  whisper_model_missing: [
    'Set up a speech recognition model in Voice settings before using Dictate.',
    'review',
    VOICE,
  ],

  // Uploads
  upload_expired: ['The upload expired. Attach the file again.', 'review'],
  upload_identity_conflict: [
    'That upload no longer matches. Attach the file again.',
    'review',
  ],
  upload_incomplete: [
    "The upload didn't finish. Attach the file again.",
    'retry',
    RETRY,
  ],
  upload_unavailable: [
    "Uploads aren't available right now. Try again.",
    'retry',
    RETRY,
  ],
};

/** Outcomes that may already have happened: a new attempt must check first. */
const MAY_HAVE_RUN = new Set([
  'authentication_required',
  'checkpoint_unavailable',
  'dependency_unavailable',
  'idempotency_expired',
  'idempotency_mismatch',
  'network_unavailable',
  'operation_pending',
  'operation_uncertain',
  'protocol_incompatible',
  'resource_setup_partial',
  'session_expired',
]);

const resourceDenials = new Set([
  'workspace_read_hooks_unavailable',
  'resource_binding_revoked',
  'workspace_path_denied',
  'folder_selection_denied',
  'path_denied',
  'capability_unavailable',
]);

function describe(code: string): ClientError | null {
  const entry = CATALOG[code];
  if (!entry) return null;
  const [message, recovery = 'review', action] = entry;
  return action
    ? { code, message, recovery, action }
    : { code, message, recovery };
}

/** Every code the catalog describes (for the catalog completeness test). */
export function catalogCodes(): string[] {
  return Object.keys(CATALOG);
}

/**
 * The server said no before anything ran: the request can be dropped and
 * sent afresh without the risk of doing the same thing twice.
 */
export function rejectedBeforeRunning(error: ClientError): boolean {
  return (
    error.code in CATALOG &&
    !MAY_HAVE_RUN.has(error.code) &&
    !/_(uncertain|unconfirmed)$/.test(error.code)
  );
}

/** Never display a server title, arbitrary exception message, path or response body. */
export function clientError(value: unknown): ClientError {
  const candidate =
    value && typeof value === 'object'
      ? (value as { code?: unknown; status?: unknown })
      : {};
  const code = typeof candidate.code === 'string' ? candidate.code : '';
  if (candidate.status === 401) return describe('session_expired')!;
  if (code && resourceDenials.has(code))
    return (
      describe(code) ?? {
        code,
        message:
          "That design or code folder can't be used right now. Refresh it and check its permissions.",
        recovery: 'review',
        action: RETRY,
      }
    );
  if (candidate.status === 403) return describe('action_denied')!;
  if (candidate.status === 426) return describe('protocol_incompatible')!;
  const known = code ? describe(code) : null;
  if (known) return known;
  if (value instanceof Error && value.message === 'protocol_incompatible')
    return describe('protocol_incompatible')!;
  if (isNetworkFailure(value)) return describe('network_unavailable')!;
  // An unknown code still says what happened and names itself, so a person
  // can report it and a developer can find it. Any other TypeError is a fault
  // in this client, not a lost connection that reconnecting would fix.
  const detail = code || (value instanceof TypeError ? 'client_error' : '');
  return {
    code: 'request_failed',
    message:
      detail && /^[a-z][a-z0-9_]{0,79}$/.test(detail)
        ? `Something went wrong. Try again. Details: ${detail}`
        : 'Something went wrong. Try again.',
    recovery: 'retry',
    action: RETRY,
  };
}

export function failureStatus(error: ClientError): ClientStatus {
  if (error.recovery === 'authenticate') return 'unauthorized';
  if (error.recovery === 'update') return 'incompatible';
  return error.code === 'network_unavailable' ? 'disconnected' : 'fatal';
}

export function aborted(value: unknown): boolean {
  return value instanceof DOMException && value.name === 'AbortError';
}
