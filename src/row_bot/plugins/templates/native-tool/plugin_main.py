from plugins.api import PluginTool


class TemplateEchoTool(PluginTool):
    @property
    def name(self):
        return "template_echo"

    @property
    def display_name(self):
        return "Template Echo"

    @property
    def description(self):
        return "Echo a short input string."

    def execute(self, query: str) -> str:
        return f"echo: {query}"


def register(api):
    api.register_tool(TemplateEchoTool(api))
