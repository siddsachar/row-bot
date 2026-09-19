"""Approval snapshots must not execute provider/tool readiness on a cold scope."""
import pytest

from row_bot import tool_configuration
from row_bot.tools import registry

pytestmark = pytest.mark.subsystem


def test_cold_tool_policy_reads_saved_bytes_without_defaults_or_scope_migration(tmp_path, monkeypatch):
    monkeypatch.setenv('ROW_BOT_DATA_DIR', str(tmp_path / 'isolated'))
    class DynamicTool:
        @property
        def enabled_by_default(self):
            pytest.fail('Approval snapshot executed provider readiness')
        @property
        def destructive_tool_names(self):
            pytest.fail('Approval snapshot executed tool descriptor')
    monkeypatch.setattr(registry, '_tools', {'dynamic': DynamicTool()})
    monkeypatch.setattr(registry, '_active_config_path', tmp_path / 'other' / 'tools_config.json')
    monkeypatch.setattr(registry, '_ensure_config_scope', lambda: pytest.fail('Approval snapshot migrated registry scope'))
    first = registry.read_policy_snapshot()
    assert first['saved_revision'] == 'missing'
    assert first['registrations'][0][2] is None
    assert not (tmp_path / 'isolated').exists()
    path = tool_configuration.configuration_path()
    path.parent.mkdir()
    path.write_text('{"tools":{"dynamic":false}}', encoding='utf-8')
    second = registry.read_policy_snapshot()
    assert first != second
    path.write_text('{"tools":{"dynamic":true}}', encoding='utf-8')
    assert registry.read_policy_snapshot() != second
