from plugins.api import PluginTool


class HelloTool(PluginTool):
    @property
    def name(self):
        return "hello_tool"

    @property
    def display_name(self):
        return "Hello Tool"

    @property
    def description(self):
        return "Return a friendly deterministic greeting."

    def execute(self, query: str) -> str:
        name = (query or "there").strip()
        return f"Hello, {name}."


def register(api):
    api.register_tool(HelloTool(api))
