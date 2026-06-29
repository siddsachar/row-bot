from plugins.api import PluginTool


class SettingsEchoTool(PluginTool):
    @property
    def name(self):
        return "settings_echo"

    @property
    def display_name(self):
        return "Settings Echo"

    @property
    def description(self):
        return "Report whether required plugin setup is present."

    def execute(self, query: str) -> str:
        workspace = self.plugin_api.get_config("workspace", "")
        has_key = bool(self.plugin_api.get_secret("API_KEY"))
        return f"workspace={bool(workspace)} api_key={has_key}"


def register(api):
    api.register_tool(SettingsEchoTool(api))
