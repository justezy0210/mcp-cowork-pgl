import subprocess
import sys

from cowork_hub.service import Hub
from cowork_hub.store import Store


def test_initialization_stores_private_token_and_preserves_existing_state(tmp_path):
    directory = tmp_path / "data"
    command = [
        sys.executable,
        "-c",
        "from cowork_hub.cli import main; main()",
        "init",
        "--data-dir",
        str(directory),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    token_path = directory / "admin.token"
    token = token_path.read_text().strip()
    assert token not in result.stdout + result.stderr
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert Hub(Store(directory / "hub.sqlite3")).authenticate(token)["role"] == "admin"
    again = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert again.returncode != 0
    assert token_path.read_text().strip() == token


def test_serve_requires_initialization(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from cowork_hub.cli import main; main()",
            "serve",
            "--data-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Run cowork-hub init" in result.stderr
