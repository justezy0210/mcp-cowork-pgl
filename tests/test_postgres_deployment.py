import importlib.util
import json
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "enable_postgres", Path(__file__).resolve().parents[1] / "scripts/enable_postgres.py"
)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


@pytest.mark.parametrize("failure", ["new_job", "import", "activation", "timeout", None])
def test_cutover_failure_boundaries(tmp_path, monkeypatch, capsys, failure):
    monkeypatch.setattr(helper, "PROJECT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["enable_postgres.py", "--apply"])
    calls = []

    def invoke(command, **kwargs):
        calls.append(command)
        if (
            command[-1] == helper.PREFLIGHT
            and failure == "new_job"
            and sum(c[-1] == helper.PREFLIGHT for c in calls) == 2
        ):
            raise RuntimeError("New job exists")
        if "migrate-postgres" in command:
            if failure == "timeout":
                raise helper.CommandUncertain("Timeout")
            if failure == "import":
                raise RuntimeError("Import failed")
            return json.dumps(
                {"verified": True, "rows": {"principals": 3}, "backup": "/data/backup.sqlite3"}
            )
        if command[-1] == helper.ACTIVATE and failure == "activation":
            raise RuntimeError("Activation interrupted")
        return ""

    monkeypatch.setattr(helper, "invoke", invoke)
    if failure:
        with pytest.raises(RuntimeError):
            helper.main()
    else:
        helper.main()
    restored = any(command[-2:] == ["start", "hub"] for command in calls)
    assert restored == (failure in ("new_job", "import"))
    assert any(c[-1] == helper.VERIFY for c in calls) == (failure is None)
    output = capsys.readouterr()
    for name in ("admin", "app"):
        path = tmp_path / ".local" / "postgres" / (name + "-password")
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.read_text().strip() not in output.out + output.err


def test_existing_password_is_preserved_and_symlinks_rejected(tmp_path):
    path = tmp_path / "password"
    first = helper.credential(path)
    assert helper.credential(path) == first
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(RuntimeError, match="private regular"):
        helper.credential(link)
