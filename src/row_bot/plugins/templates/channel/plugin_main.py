from plugins.api import Channel


class TemplateChannel(Channel):
    def __init__(self, api):
        self.api = api
        self._running = False

    @property
    def name(self):
        return "template_channel"

    @property
    def display_name(self):
        return "Template Channel"

    async def start(self):
        self._running = self.is_configured()
        return self._running

    async def stop(self):
        self._running = False

    def is_configured(self):
        return bool(self.api.get_config("default_target", ""))

    def is_running(self):
        return self._running

    def get_default_target(self):
        target = self.api.get_config("default_target", "")
        if not target:
            raise RuntimeError("Template Channel default target is not configured")
        return target

    def send_message(self, target, text):
        raise RuntimeError("Template Channel transport is not implemented yet")


def register(api):
    api.register_channel(TemplateChannel(api))
