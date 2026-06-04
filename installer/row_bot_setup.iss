; =============================================================================
; Row-Bot v4.0.0 - Inno Setup Script
; Self-contained installer: bundles embedded Python with all pip packages
; pre-installed.  No internet downloads at install time.
; =============================================================================
;
; Prerequisites (placed in installer\build\ by build_installer.ps1):
;   build\python\          â€“ Embedded Python with all packages pre-installed
;
; Compile with:  iscc installer\row_bot_setup.iss

#ifnexist "build\python\Lib\site-packages\sentence_transformers\__init__.py"
  #error Embedded Python is missing sentence_transformers. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\langchain_huggingface\__init__.py"
  #error Embedded Python is missing langchain_huggingface. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\transformers\__init__.py"
  #error Embedded Python is missing transformers. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\torch\__init__.py"
  #error Embedded Python is missing torch. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\httpx\__init__.py"
  #error Embedded Python is missing httpx. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\youtube_search\__init__.py"
  #error Embedded Python is missing youtube_search. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#ifnexist "build\python\Lib\site-packages\youtube_transcript_api\__init__.py"
  #error Embedded Python is missing youtube_transcript_api. Run installer\build_installer.ps1 before compiling row_bot_setup.iss.
#endif

#define MyAppName      "Row-Bot"
#define MyAppVersion   "4.0.0"
#define MyAppPublisher "Row-Bot"
#define MyAppURL       "https://row-bot.ai"
#define MyAppExeName   "launch_row_bot.vbs"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=RowBotSetup_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\row-bot.ico
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; The Windows build is self-contained. Replace the embedded Python on repair
; and upgrade so user-installed or broken optional packages cannot survive.
Type: filesandordirs; Name: "{app}\python"

[Files]
; â”€â”€ App source code â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source: "..\app.py";                  DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\agent.py";                 DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\approval_policy.py";       DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\brand.py";                 DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\memory.py";                DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\memory_policy.py";         DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\memory_evolution.py";      DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\memory_extraction.py";     DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\knowledge_graph.py";       DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\wiki_vault.py";             DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\dream_cycle.py";            DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\insights.py";              DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\document_extraction.py";    DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\tasks.py";                 DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\models.py";                DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\data_reader.py";            DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\documents.py";             DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\embedding_config.py";      DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\embedding_providers.py";   DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\threads.py";               DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\api_keys.py";              DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\app_port.py";              DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\data_paths.py";            DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\secret_store.py";          DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\github_account.py";        DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\voice\*.py";              DestDir: "{app}\app\voice"; Flags: ignoreversion
Source: "..\tts.py";                   DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\vision.py";                DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\launcher.py";              DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\notifications.py";         DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\prompts.py";               DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\requirements.txt";         DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\row-bot.ico";                DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\logging_config.py";         DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\terminal_bridge.py";        DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\terminal_pty.py";           DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\tunnel.py";                DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\identity.py";              DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\self_knowledge.py";        DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\startup_diagnostics.py";   DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\stability.py";             DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\version.py";               DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\updater.py";               DestDir: "{app}\app"; Flags: ignoreversion
; â”€â”€ Static assets (JS libraries) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source: "..\static\*";                 DestDir: "{app}\app\static"; Flags: ignoreversion recursesubdirs createallsubdirs

; â”€â”€ Sounds â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source: "..\sounds\*.wav";              DestDir: "{app}\app\sounds"; Flags: ignoreversion

; â”€â”€ Buddy package â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source: "..\buddy\*.py";                DestDir: "{app}\app\buddy"; Flags: ignoreversion

; â”€â”€ Channels package â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source: "..\channels\__init__.py";      DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\auth.py";          DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\auth_store.py";    DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\base.py";          DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\commands.py";      DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\config.py";        DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\discord_channel.py"; DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\media.py";         DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\registry.py";      DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\runtime.py";       DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\slack.py";         DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\sms.py";           DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\telegram.py";      DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\tool_factory.py";  DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\approval.py";      DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\media_capture.py";  DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\thread_repair.py";  DestDir: "{app}\app\channels"; Flags: ignoreversion
Source: "..\channels\whatsapp.py";      DestDir: "{app}\app\channels"; Flags: ignoreversion
; WhatsApp Node.js bridge
Source: "..\channels\whatsapp_bridge\bridge.js";    DestDir: "{app}\app\channels\whatsapp_bridge"; Flags: ignoreversion
Source: "..\channels\whatsapp_bridge\package.json"; DestDir: "{app}\app\channels\whatsapp_bridge"; Flags: ignoreversion
Source: "..\channels\whatsapp_bridge\package-lock.json"; DestDir: "{app}\app\channels\whatsapp_bridge"; Flags: ignoreversion

; â”€â”€ Utils package â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source: "..\utils\__init__.py";        DestDir: "{app}\app\utils"; Flags: ignoreversion
Source: "..\utils\text.py";            DestDir: "{app}\app\utils"; Flags: ignoreversion
Source: "..\utils\media.py";           DestDir: "{app}\app\utils"; Flags: ignoreversion

; â”€â”€ Providers package â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source: "..\providers\*";              DestDir: "{app}\app\providers"; Flags: ignoreversion recursesubdirs createallsubdirs

; â”€â”€ MCP client package â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source: "..\mcp_client\__init__.py";    DestDir: "{app}\app\mcp_client"; Flags: ignoreversion
Source: "..\mcp_client\conflicts.py";   DestDir: "{app}\app\mcp_client"; Flags: ignoreversion
Source: "..\mcp_client\config.py";      DestDir: "{app}\app\mcp_client"; Flags: ignoreversion
Source: "..\mcp_client\logging.py";     DestDir: "{app}\app\mcp_client"; Flags: ignoreversion
Source: "..\mcp_client\marketplace.py"; DestDir: "{app}\app\mcp_client"; Flags: ignoreversion
Source: "..\mcp_client\recommended_servers.json"; DestDir: "{app}\app\mcp_client"; Flags: ignoreversion
Source: "..\mcp_client\requirements.py"; DestDir: "{app}\app\mcp_client"; Flags: ignoreversion
Source: "..\mcp_client\results.py";     DestDir: "{app}\app\mcp_client"; Flags: ignoreversion
Source: "..\mcp_client\runtime.py";     DestDir: "{app}\app\mcp_client"; Flags: ignoreversion
Source: "..\mcp_client\safety.py";      DestDir: "{app}\app\mcp_client"; Flags: ignoreversion
Source: "..\skills_hub\*";              DestDir: "{app}\app\skills_hub"; Flags: ignoreversion recursesubdirs createallsubdirs

; â”€â”€ Migration wizard package â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source: "..\migration\__init__.py";     DestDir: "{app}\app\migration"; Flags: ignoreversion
Source: "..\migration\apply.py";        DestDir: "{app}\app\migration"; Flags: ignoreversion
Source: "..\migration\core.py";         DestDir: "{app}\app\migration"; Flags: ignoreversion
Source: "..\migration\detection.py";    DestDir: "{app}\app\migration"; Flags: ignoreversion
Source: "..\migration\fixtures.py";     DestDir: "{app}\app\migration"; Flags: ignoreversion
Source: "..\migration\planner.py";      DestDir: "{app}\app\migration"; Flags: ignoreversion
Source: "..\migration\redaction.py";    DestDir: "{app}\app\migration"; Flags: ignoreversion
Source: "..\migration\row_bot_legacy_rebrand.py"; DestDir: "{app}\app\migration"; Flags: ignoreversion

; â”€â”€ Tools package â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source: "..\tools\__init__.py";        DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\approval_gate.py";   DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\base.py";            DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\registry.py";        DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\arxiv_tool.py";      DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\calculator_tool.py"; DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\calendar_tool.py";   DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\chart_tool.py";      DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\conversation_search_tool.py"; DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\custom_tool_builder_tool.py"; DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\documents_tool.py";  DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\duckduckgo_tool.py"; DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\filesystem_tool.py"; DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\gmail_tool.py";      DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\mcp_tool.py";        DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\memory_tool.py";     DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\system_info_tool.py"; DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\tracker_tool.py";    DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\task_tool.py";       DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\url_reader_tool.py"; DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\vision_tool.py";     DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\weather_tool.py";    DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\web_search_tool.py"; DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\wikipedia_tool.py";  DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\wolfram_tool.py";    DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\browser_tool.py";    DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\shell_tool.py";      DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\youtube_tool.py";    DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\wiki_tool.py";      DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\image_gen_tool.py";  DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\video_gen_tool.py";  DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\x_tool.py";         DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\row_bot_status_tool.py"; DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\updater_tool.py";    DestDir: "{app}\app\tools"; Flags: ignoreversion
Source: "..\tools\developer_tool.py";  DestDir: "{app}\app\tools"; Flags: ignoreversion

; â”€â”€ Plugins package â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source: "..\plugins\__init__.py";       DestDir: "{app}\app\plugins"; Flags: ignoreversion
Source: "..\plugins\api.py";            DestDir: "{app}\app\plugins"; Flags: ignoreversion
Source: "..\plugins\installer.py";      DestDir: "{app}\app\plugins"; Flags: ignoreversion
Source: "..\plugins\loader.py";         DestDir: "{app}\app\plugins"; Flags: ignoreversion
Source: "..\plugins\manifest.py";       DestDir: "{app}\app\plugins"; Flags: ignoreversion
Source: "..\plugins\marketplace.py";    DestDir: "{app}\app\plugins"; Flags: ignoreversion
Source: "..\plugins\registry.py";       DestDir: "{app}\app\plugins"; Flags: ignoreversion
Source: "..\plugins\sandbox.py";        DestDir: "{app}\app\plugins"; Flags: ignoreversion
Source: "..\plugins\state.py";          DestDir: "{app}\app\plugins"; Flags: ignoreversion
Source: "..\plugins\ui_marketplace.py"; DestDir: "{app}\app\plugins"; Flags: ignoreversion
Source: "..\plugins\ui_plugin_dialog.py"; DestDir: "{app}\app\plugins"; Flags: ignoreversion
Source: "..\plugins\ui_settings.py";    DestDir: "{app}\app\plugins"; Flags: ignoreversion

; â”€â”€ Designer package â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source: "..\designer\*";              DestDir: "{app}\app\designer"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\developer\*";             DestDir: "{app}\app\developer"; Flags: ignoreversion recursesubdirs createallsubdirs

; â”€â”€ Bundled Skills â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source: "..\bundled_skills\*";         DestDir: "{app}\app\bundled_skills"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\tool_guides\*";           DestDir: "{app}\app\tool_guides"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\skills.py";                 DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\skills_activation.py";      DestDir: "{app}\app"; Flags: ignoreversion
Source: "..\slash_commands.py";         DestDir: "{app}\app"; Flags: ignoreversion
; â”€â”€ UI package (modular frontend) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source: "..\ui\__init__.py";            DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\buddy.py";               DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\bulk_select.py";         DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\chat.py";               DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\chat_components.py";    DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\confirm.py";            DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\constants.py";          DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\export.py";             DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\graph_panel.py";        DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\head_html.py";          DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\helpers.py";            DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\home.py";               DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\knowledge_audit.py";    DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\mcp_settings.py";       DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\migration_wizard.py";   DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\model_catalog.py";      DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\onboarding_center.py";  DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\onboarding_state.py";   DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\performance.py";        DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\post_migration.py";     DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\provider_settings.py";  DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\render.py";             DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\settings.py";           DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\setup_wizard.py";       DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\sidebar.py";            DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\skeleton.py";           DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\state.py";              DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\status_bar.py";         DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\status_checks.py";      DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\streaming.py";          DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\task_dialog.py";        DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\thread_actions.py";     DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\command_center.py";     DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\entity_editor.py";      DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\terminal_widget.py";    DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\timer_utils.py";        DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\tool_trace.py";         DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\transcript.py";         DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\update_dialog.py";      DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\voice_lifecycle.py";    DestDir: "{app}\app\ui"; Flags: ignoreversion
Source: "..\ui\voice_realtime_events.py"; DestDir: "{app}\app\ui"; Flags: ignoreversion
; Runtime diagnostics script
Source: "..\scripts\verify_runtime_dependencies.py"; DestDir: "{app}\app\scripts"; Flags: ignoreversion
; â”€â”€ Embedded Python (with all packages pre-installed) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source: "build\python\*";              DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs

; â”€â”€ Launcher scripts â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Source: "launch_row_bot.bat";            DestDir: "{app}"; Flags: ignoreversion
Source: "launch_row_bot.vbs";            DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}";                    Filename: "wscript.exe"; Parameters: """{app}\{#MyAppExeName}"""; IconFilename: "{app}\app\row-bot.ico"; Comment: "Launch Row-Bot"
Name: "{group}\Uninstall {#MyAppName}";           Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";               Filename: "wscript.exe"; Parameters: """{app}\{#MyAppExeName}"""; IconFilename: "{app}\app\row-bot.ico"; Tasks: desktopicon

[Run]
; â”€â”€ Launch app after install (optional) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
Filename: "wscript.exe"; Parameters: """{app}\{#MyAppExeName}"""; Description: "Launch {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app\__pycache__"
Type: filesandordirs; Name: "{app}\app\tools\__pycache__"
Type: filesandordirs; Name: "{app}\app\channels\__pycache__"
Type: filesandordirs; Name: "{app}\app\ui\__pycache__"
Type: filesandordirs; Name: "{app}\app\plugins\__pycache__"
