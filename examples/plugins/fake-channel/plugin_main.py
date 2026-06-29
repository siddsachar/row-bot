from plugins.api import Channel


class FakePluginChannel(Channel):
    def __init__(self, api):
        self.api = api
        self._running = False
        self.messages = []

    @property
    def name(self):
        return "fake_plugin_channel"

    @property
    def display_name(self):
        return "Fake Plugin Channel"

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
            raise RuntimeError("Fake Plugin Channel target is not configured")
        return target

    def send_message(self, target, text):
        self.messages.append((target, text))


def register(api):
    api.register_channel(FakePluginChannel(api))
