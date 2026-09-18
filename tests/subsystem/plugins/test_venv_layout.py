"""Copied venv construction stays compatible with strict link-free readiness."""
from __future__ import annotations

import os
import venv
from uuid import uuid4

import pytest

pytestmark = pytest.mark.subsystem


def test_copied_environment_creation_is_link_free(plugin_modules, monkeypatch):
    from row_bot.plugins import sandbox

    installer = plugin_modules["installer"]
    environment = installer._generation_path("sample-plugin", str(uuid4()), create=True)

    def create(argv, *, cwd, timeout):
        assert argv[-1] == str(environment)
        venv.EnvBuilder(with_pip=False, symlinks=False).create(environment)

    monkeypatch.setattr(sandbox, "_run", create)
    sandbox.create_environment(environment)
    assert not (environment / "lib64").is_symlink()
    assert installer._tree_revision(environment, source=False)
    sandbox._target(environment)


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv alias layout")
@pytest.mark.parametrize("target", ["lib", "../outside", "missing"])
def test_only_standard_alias_is_normalised(plugin_modules, target):
    from row_bot.plugins import sandbox

    installer = plugin_modules["installer"]
    environment = installer._generation_path("sample-plugin", str(uuid4()), create=True)
    environment.mkdir()
    (environment / "lib").mkdir()
    alias = environment / "lib64"
    alias.symlink_to(target)
    if target == "lib":
        sandbox._normalise_created_environment(environment)
        assert not os.path.lexists(alias)
        # Introducing the same alias after preparation is still rejected.
        alias.symlink_to("lib")
        with pytest.raises(sandbox.EnvironmentError, match="environment_path_invalid"):
            installer._tree_revision(environment, source=False)
    else:
        with pytest.raises(sandbox.EnvironmentError, match="environment_path_invalid"):
            sandbox._normalise_created_environment(environment)
        assert alias.is_symlink()
