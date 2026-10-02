#!/usr/bin/env python3
"""Install and connect a user's Cowork client; token values never enter command arguments."""

import argparse
import fcntl
import getpass
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import warnings
from pathlib import Path


class SetupError(Exception):
    pass


def release_files(source):
    """Explicit client bundle contents; exclude credentials, server configuration and caches."""
    files = [source / name for name in (
        "pyproject.toml", "requirements-client.txt", "scripts/setup_client.py",
        "scripts/install_connector.py", "scripts/setup_autostart.py", "scripts/check_mcp.py",
    )]
    files += sorted((source / "src/cowork_hub").glob("*.py"))
    web = source / "src/cowork_hub/web/dist"
    if not (web / "index.html").is_file():
        raise SetupError("React 웹 빌드가 없습니다. npm --prefix frontend run build를 먼저 실행하세요.")
    files += [web / "index.html", web / "favicon.svg"]
    files += sorted(path for path in (web / "assets").rglob("*") if path.is_file())
    for pattern in ("*.md", "*.yaml"):
        files += sorted((source / "skills/cowork-jobs").rglob(pattern))
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise SetupError("배포 파일에 누락 또는 심볼릭 링크가 있습니다.")
    return files


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise SetupError("설정 폴더는 본인 소유의 일반 디렉터리이며 권한이 700이어야 합니다.")


def private_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SetupError("토큰·설정 파일은 본인 소유 일반 파일이며 권한이 600이어야 합니다.")
        value = stream.read(262145)
        if len(value) > 262144:
            raise SetupError("설정 파일이 너무 큽니다.")
        return value


def token_value(path=None):
    if path:
        value = private_file(path).strip()
    else:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                value = getpass.getpass("웹에서 복사한 개인 토큰을 붙여 넣고 Enter (화면에 표시되지 않음): ").strip()
        except (getpass.GetPassWarning, EOFError):
            raise SetupError("대화형 터미널에서 실행하거나 --token-file로 개인 토큰 파일을 지정하세요.") from None
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,4096}", value):
        raise SetupError("토큰 형식이 올바르지 않습니다. 웹에서 발급한 개인 토큰을 사용하세요.")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def hub_get(hub, token, endpoint):
    request = urllib.request.Request(hub + endpoint, headers={"Authorization": "Bearer " + token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise SetupError(f"허브 조회 실패 (HTTP {exc.code}). 토큰과 서버 권한을 확인하세요.") from None
    except (OSError, ValueError):
        raise SetupError("허브에 연결할 수 없습니다. 내부 허브 주소와 네트워크를 확인하세요.") from None


def preflight(hub, token, node):
    identity = hub_get(hub, token, "/v1/identity")
    if not identity.get("configured") or (identity.get("uid"), identity.get("gid")) != (os.getuid(), os.getgid()):
        raise SetupError("현재 컨테이너의 UID/GID와 허브 등록값이 다릅니다. 관리자에게 확인하세요.")
    if node not in {item["id"] for item in hub_get(hub, token, "/v1/cluster")}:
        raise SetupError("선택한 서버의 사용 승인이 필요합니다.")
    capabilities = hub_get(hub, token, "/v1/capabilities")
    if capabilities.get("connector") != 1:
        raise SetupError("허브의 연결 프로그램 기능을 먼저 업데이트해야 합니다.")


def find_environment(hub, token, environment_id):
    after = ""
    while True:
        items = hub_get(hub, token, "/v1/environments?limit=100&after=" + urllib.parse.quote(after))
        for item in items:
            if item["id"] == environment_id:
                return item
        if len(items) < 100 or items[-1]["id"] <= after:
            raise SetupError("등록한 환경의 현재 상태를 찾을 수 없습니다. 웹에서 확인하세요.")
        after = items[-1]["id"]


def copy_source(source, target, *, replace=False):
    files = release_files(source)
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir() or target.stat().st_uid != os.getuid():
            raise SetupError("기존 소스 경로가 본인 소유 일반 폴더가 아닙니다.")
        identical = True
        for path in files:
            existing = target / path.relative_to(source)
            if existing.is_symlink() or not existing.is_file() or existing.read_bytes() != path.read_bytes():
                identical = False
                break
        if identical:
            return None
        if not replace:
            raise SetupError("기존 소스의 설치 기록을 확인할 수 없습니다. 기존 파일은 보존했습니다.")
    staging = Path(tempfile.mkdtemp(prefix=".cowork-source-", dir=target.parent))
    backup = staging / "previous"
    published = False
    try:
        staged = staging / "source"
        for path in files:
            output = staged / path.relative_to(source)
            output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(path, output)
        if target.exists():
            target.rename(backup)
        try:
            staged.rename(target)
            published = True
        except BaseException:
            if backup.exists():
                try:
                    backup.rename(target)
                except OSError:
                    raise SetupError("소스 교체 복구에 실패했습니다. 기존 소스 보존 위치: " + str(backup)) from None
            raise
    finally:
        if published or not backup.exists():
            shutil.rmtree(staging)


def command(argv, stage, *, json_output=False):
    result = subprocess.run([str(arg) for arg in argv], capture_output=True, text=True, timeout=90)
    if result.returncode:
        # Do not echo tool output, which may include private configuration.
        raise SetupError(f"{stage} 실패. 기존 설정은 보존했습니다. 같은 설치 명령으로 재시도할 수 있습니다.")
    if json_output:
        return json.loads(result.stdout)
    return result.stdout


def stop_previous_connector(connector, config):
    current = command([connector, "status", "--config", config], "기존 연결 확인", json_output=True)
    if not current["running"]:
        return
    command([connector, "stop", "--config", config], "기존 연결 종료", json_output=True)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        latest = command([connector, "status", "--config", config], "기존 연결 확인", json_output=True)
        # Supervisor may already have restarted this connector using the updated runtime.
        if not latest["running"] or latest["pid"] != current["pid"]:
            return
        time.sleep(0.1)
    raise SetupError("기존 연결 프로그램이 종료 중입니다. 잠시 후 같은 설치 명령으로 재시도하세요.")


def skill_file_available(path):
    """Check discovery prerequisites without parsing or replacing a user's skill."""
    try:
        entry = path / "SKILL.md"
        if not entry.is_file():
            return False
        with entry.open(encoding="utf-8") as stream:
            return bool(stream.read(4096).strip())
    except (OSError, UnicodeError):
        return False


def link_skill(source, client):
    home = Path.home()
    base = home / (".agents" if client == "codex" else ".claude") / "skills"
    target = base / "cowork-jobs"
    candidates = [target]
    if client == "codex":
        candidates.append(home / ".codex/skills/cowork-jobs")
    existing = []
    available = False
    for path in candidates:
        try:
            occupied = path.exists() or path.is_symlink()
        except OSError:
            # An inaccessible parent is not evidence that it is safe to install here.
            occupied = True
        if not occupied:
            continue
        existing.append(path)
        if skill_file_available(path):
            available = True
        else:
            print("스킬 경로 확인 필요: 읽을 수 있는 SKILL.md가 없습니다. 기존 경로 보존: " + str(path), file=sys.stderr)
    if available:
        return "existing"
    if existing:
        print("cowork-jobs 자동 선택 준비 안 됨: 위 경로의 링크 대상·SKILL.md·접근 권한을 복구한 뒤 처음 설치 명령을 다시 실행하세요.", file=sys.stderr)
        return "unavailable"
    if not skill_file_available(source / "skills/cowork-jobs"):
        raise SetupError("설치 파일에서 cowork-jobs/SKILL.md를 읽을 수 없습니다. 설치 파일을 다시 내려받으세요.")
    base.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source / "skills/cowork-jobs", target_is_directory=True)
    return "installed"


def connect_client(bin_path, root, hub, client):
    if client == "cli":
        return {"mcp": "not_requested", "skill": "not_requested"}
    executable = shutil.which(client)
    if not executable:
        raise SetupError(f"{client} 명령을 찾을 수 없습니다. 에이전트가 설치된 메인 컨테이너에서 실행하세요.")
    existing = subprocess.run([executable, "mcp", "get", "cowork"], capture_output=True, text=True, timeout=30)
    if existing.returncode == 0:
        mcp = "existing"
    else:
        # Some client errors are not 'missing'; never replace a registration after an ambiguous read.
        text = (existing.stdout + existing.stderr).lower()
        if not any(message in text for message in ("no mcp server", "not found", "does not exist")):
            raise SetupError("기존 cowork MCP 등록을 확인할 수 없습니다. 에이전트 설정을 확인하세요.")
        args = [executable, "mcp", "add", "cowork"]
        if client == "claude":
            args += ["-s", "user"]
        args += ["--", str(bin_path / "cowork-mcp"), "--hub-url", hub,
                 "--token-file", str(root / "state/user.token"),
                 "--runner-config", str(root / "state/runner.json")]
        command(args, "MCP 등록")
        mcp = "installed"
    return {"mcp": mcp, "skill": link_skill(root / "source", client)}


def enable_autostart(python, config, source):
    from setup_autostart import AutostartError, supervisor_layout

    try:
        _, directory = supervisor_layout()
    except AutostartError:
        return {"autostart_configured": False,
                "reason": "이 컨테이너에서 지원하는 Supervisor 설정을 찾지 못했습니다. 계정 승인과 별개이며, sudo 재시도로 해결되지 않습니다. 컨테이너 관리자에게 시작 방식에 맞는 자동 시작 설정을 요청하세요."}
    # Keep the helper outside the temporary zipapp extraction, including for older installations.
    payload = (source / "scripts/setup_autostart.py").read_bytes()
    helper = config.parent / ("autostart-" + hashlib.sha256(payload).hexdigest()[:12] + ".py")
    try:
        fd = os.open(helper, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        if private_file(helper).encode() != payload:
            raise SetupError("기존 자동 시작 등록 파일이 다릅니다. 기존 파일을 보존했습니다.") from None
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
    argv = ["/usr/bin/python3", "-I", "-S", str(helper), "--python", str(python), "--config", str(config)]
    retry = shlex.join(["sudo", *argv])
    if os.geteuid() != 0 and not os.access(directory, os.W_OK):
        sudo = shutil.which("sudo")
        if not sudo:
            return {"autostart_configured": False, "reason": "자동 시작 등록에 관리자 권한이 필요합니다.", "retry": retry}
        if sys.stdin.isatty():
            print("연결 프로그램의 자동 시작을 등록합니다. sudo 암호를 요청하면 터미널에 입력하세요.", flush=True)
        authentication = subprocess.run([sudo, *([] if sys.stdin.isatty() else ["-n"]), "-v"], timeout=180)
        if authentication.returncode:
            return {"autostart_configured": False, "reason": "sudo 인증을 완료하지 못했습니다. 터미널에서 한 번 등록하세요.", "retry": retry}
        # Only this small registration helper runs as root; the connector runs as its owner.
        argv = [sudo, "-n", *argv]
    result = subprocess.run(argv, stdout=subprocess.PIPE, text=True, timeout=180)
    try:
        registered = json.loads(result.stdout)
    except ValueError:
        raise SetupError("자동 시작 등록 결과를 확인하지 못했습니다. 다시 확인할 명령: " + retry) from None
    if result.returncode or not registered.get("autostart_configured"):
        # The service may already be registered. Never race a second unmanaged daemon against it.
        raise SetupError("자동 시작 등록 확인 실패 (" + registered.get("error", "UNKNOWN") + "). 다시 확인할 명령: " + retry)
    return {**registered, "retry": retry}


def autostart_only(root, source):
    root = root.expanduser().absolute()
    config = root / "state/connector.json"
    private_file(config)
    private_directory(config.parent)
    version = tomllib.loads((source / "pyproject.toml").read_text())["project"]["version"]
    python = root / ("client-" + version) / "venv/bin/python"
    if not python.is_file():
        raise SetupError("기존 설치를 찾지 못했습니다. 처음 설치할 때 사용한 폴더를 지정하세요.")
    return {**enable_autostart(python, config, source),
            "restart": shlex.join([str(python), "-m", "cowork_hub.connector", "start", "--config", str(config)])}


def report_autostart(result):
    if result["autostart_configured"]:
        print("자동 시작 등록 완료: " + result["service"])
        print("컨테이너의 Supervisor가 시작될 때 자동 연결되며, 비정상 종료하면 다시 실행합니다.")
    else:
        print("자동 시작 미설정: " + result["reason"])
        if result.get("retry"):
            print("한 번만 등록할 명령: " + result["retry"])
        if result.get("restart"):
            print("현재 연결 수동 시작 (본인 계정, sudo 없이): " + result["restart"])


def update_settings(args, source):
    """Reuse this installation's private settings without guessing a server or client."""
    root = args.root.expanduser().absolute()
    if root == Path("/") or ".." in root.parts or root.is_symlink():
        raise SetupError("처음 설치할 때 사용한 본인 소유 공유 폴더를 지정하세요.")
    from install_connector import read_installation
    version = tomllib.loads((source / "pyproject.toml").read_text())["project"]["version"]
    try:
        previous = read_installation(root / ("client-" + version), root / "source")
        if previous is None:
            raise ValueError
        state = root / "state"
        existing = json.loads(private_file(state / "runner.json"))
        if Path(existing["token_file"]) != state / "user.token":
            raise ValueError
        token_value(state / "user.token")
        args.hub_url = existing["hub_url"]
        args.node = existing["environment"]["node_id"]
        if not isinstance(args.hub_url, str) or not isinstance(args.node, str):
            raise ValueError
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        raise SetupError("업데이트할 기존 설치·환경 설정을 확인할 수 없습니다. 처음 설치한 폴더를 지정하세요. 환경 등록 전에 중단된 설치는 처음 설치 명령으로 다시 진행하세요.") from None
    # Existing MCP clients keep the same executable path; no client CLI is needed to update it.
    args.client = "cli"
    return args


def setup(args, source):
    if os.geteuid() == 0 and os.environ.get("SUDO_UID", "0") != "0":
        raise SetupError("설치 전체에 sudo를 붙이지 마세요. 본인 계정으로 실행하면 자동 시작 등록 단계에서만 sudo를 요청합니다.")
    if sys.platform != "linux" or sys.version_info < (3, 12):
        raise SetupError("Linux와 Python 3.12 이상이 필요합니다.")
    root = args.root.expanduser().absolute()
    if root == Path("/") or ".." in root.parts or root.is_symlink():
        raise SetupError("본인 소유 공유 폴더의 절대 경로를 지정하세요.")
    parsed = urllib.parse.urlsplit(args.hub_url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise SetupError("허브 주소는 http(s)://호스트:포트 형식이어야 합니다.")
    hub = args.hub_url.rstrip("/")
    if args.client != "cli" and not shutil.which(args.client):
        raise SetupError(f"{args.client} 명령을 찾을 수 없습니다. 해당 프로그램이 설치된 컨테이너에서 실행하세요.")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.stat().st_uid != os.getuid():
        raise SetupError("설치 폴더는 본인 소유여야 합니다.")
    state = root / "state"
    private_directory(state)
    with os.fdopen(os.open(state / "setup.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SetupError("이 폴더에서 다른 설치가 진행 중입니다.") from None
        token_path = state / "user.token"
        token_exists = token_path.exists() or token_path.is_symlink()
        token = token_value(token_path if token_exists else args.token_file)
        print("[1/4] 계정·서버 권한 확인", flush=True)
        preflight(hub, token, args.node)
        if not token_exists:
            with os.fdopen(os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
                stream.write(token + "\n")
        print("[2/4] Cowork 프로그램 설치 (처음에는 몇 분 걸릴 수 있습니다)", flush=True)
        installed_source = root / "source"
        # Import only the installer shipped beside this script, before adding any user paths.
        from install_connector import install, read_installation
        version = tomllib.loads((source / "pyproject.toml").read_text())["project"]["version"]
        prefix = root / ("client-" + version)
        try:
            previous = read_installation(prefix, installed_source)
        except RuntimeError:
            raise SetupError("기존 프로그램 폴더의 설치 기록을 확인할 수 없어 보존했습니다. 설치 경로와 권한을 확인하세요.") from None
        copy_source(source, installed_source, replace=previous is not None)
        try:
            result = install(prefix, installed_source, wheelhouse=args.wheelhouse)
        except (RuntimeError, subprocess.SubprocessError):
            raise SetupError("프로그램 설치에 실패했습니다. Python·인터넷 연결을 확인하세요. 기존 토큰·설정은 보존했으며 같은 설치 명령으로 재시도할 수 있습니다.") from None
        bin_path = Path(result["bin"])
        connector = bin_path / "cowork-connector"
        config, runner = state / "connector.json", state / "runner.json"
        print("[3/4] 메인 환경·에이전트 연결", flush=True)
        if runner.exists() or runner.is_symlink():
            existing = json.loads(private_file(runner))
            if (existing["hub_url"].rstrip("/") != hub or
                    Path(existing["token_file"]) != token_path or
                    existing["environment"]["node_id"] != args.node):
                raise SetupError("기존 환경의 허브·토큰 경로·서버와 다릅니다. 기존 설정은 보존했습니다.")
            configure_args = ["--runner-config", runner]
        else:
            configure_args = ["--hub-url", hub, "--token-file", token_path, "--node", args.node, "--workdir", root]
        configured = command([connector, "configure", *configure_args, "--config", config], "환경 등록", json_output=True)
        client = ({"mcp": "preserved", "skill": "preserved"} if getattr(args, "update", False)
                  else connect_client(bin_path, root, hub, args.client))
        stop_previous_connector(connector, config)
        automatic = ({"autostart_configured": False, "reason": "자동 시작 등록을 생략했습니다."}
                     if getattr(args, "no_autostart", False) else enable_autostart(bin_path / "python", config, source))
        if not automatic["autostart_configured"]:
            command([connector, "start", "--config", config], "연결 프로그램 시작", json_output=True)
        print("[4/4] MCP 연결 확인", flush=True)
        command([bin_path / "python", installed_source / "scripts/check_mcp.py", "--hub-url", hub,
                 "--token-file", token_path, "--runner-config", runner], "MCP 연결 확인")
        environment = find_environment(hub, token, configured["environment_id"])
        notifications = hub_get(hub, token, "/v1/notifications")
        return {"installed": True, "environment_id": environment["id"], "environment_status": environment["status"],
                "discord_configured": notifications["configured"], "client": args.client, **client,
                "restart": shlex.join([str(connector), "start", "--config", str(config)]), **automatic}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Cowork 설치·연결을 한 번에 진행합니다. 업데이트는 --root 설치폴더 --update만 지정하세요.")
    parser.add_argument("--root", type=Path, required=True, help="본인 소유 공유 설치 폴더")
    parser.add_argument("--node", help="메인 컨테이너가 있는 서버 ID")
    parser.add_argument("--client", choices=("codex", "claude", "cli"))
    parser.add_argument("--hub-url")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--update", action="store_true", help="기존 폴더에서 업데이트 (서버·허브·토큰·MCP 설정 재사용)")
    mode.add_argument("--autostart-only", action="store_true", help="기존 설치에 자동 시작만 등록 (재설치·토큰 입력 없음)")
    parser.add_argument("--no-autostart", action="store_true", help="관리자가 시작 절차를 별도로 관리할 때만 등록 생략")
    parser.add_argument("--token-file", type=Path, help="선택: 기존 개인 토큰 파일 (지정하지 않으면 숨김 입력)")
    parser.add_argument("--wheelhouse", type=Path, help="선택: 오프라인 의존성 폴더")
    args = parser.parse_args()
    if args.autostart_only and args.no_autostart:
        parser.error("--autostart-only와 --no-autostart는 함께 사용할 수 없습니다.")
    if args.update and (args.node or args.hub_url or args.client or args.token_file):
        parser.error("업데이트는 기존 설정을 재사용합니다. --node, --hub-url, --client, --token-file을 빼고 실행하세요.")
    if not args.autostart_only and not args.update and (not args.node or not args.hub_url):
        parser.error("새 설치에는 --node와 --hub-url이 필요합니다.")
    try:
        source = Path(__file__).resolve().parents[1]
        if args.autostart_only:
            result = autostart_only(args.root, source)
            report_autostart(result)
            return 0 if result["autostart_configured"] else 1
        if args.update:
            args = update_settings(args, source)
            print("기존 설치 설정으로 업데이트합니다. 토큰을 다시 입력하거나 MCP를 재등록할 필요가 없습니다.", flush=True)
        else:
            args.client = args.client or "codex"
        result = setup(args, source)
    except (SetupError, RuntimeError, OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as exc:
        detail = str(exc) if isinstance(exc, SetupError) else "설치 또는 연결 확인에 실패했습니다. 기존 파일은 보존했습니다."
        print("설치 중단: " + detail, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n설치를 중단했습니다. 같은 명령으로 다시 진행할 수 있습니다.", file=sys.stderr)
        return 130
    print("\n프로그램 설치와 연결 확인이 끝났습니다.")
    print("환경 ID: " + result["environment_id"])
    print("환경 상태: " + result["environment_status"])
    if result["environment_status"] != "READY":
        print("환경이 아직 사용 가능 상태가 아닙니다. 웹의 ‘작업할 컨테이너’에서 상태를 확인하세요. 허용 서버·UID/GID·자원 설정과 허브 버전 확인이 필요합니다.")
    if not result["discord_configured"]:
        print("관리자에게 본인 Discord 알림 채널 연결을 요청하세요.")
    if result["mcp"] == "existing":
        print("기존 cowork MCP 등록을 보존했습니다. 에이전트에서 연결 대상이 이 설치인지 확인하세요.")
    if result["skill"] == "existing":
        print("기존 cowork-jobs 스킬을 보존했습니다.")
    elif result["skill"] == "unavailable":
        print("주의: 프로그램은 설치되었지만 스킬 자동 선택은 준비되지 않았습니다. 위 스킬 경로 안내를 확인하세요.")
    if args.update:
        print("기존 MCP·스킬 등록과 실행 경로를 유지했습니다. 사용 중인 에이전트에서 MCP를 다시 연결하거나 에이전트를 재시작하세요.")
    elif args.client != "cli":
        print("에이전트를 다시 시작하면 여러 프로젝트에서 같은 MCP를 사용할 수 있습니다.")
    report_autostart(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
