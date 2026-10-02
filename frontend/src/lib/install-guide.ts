import { t } from "@/lib/i18n"
export const quote = (value: string) => "'" + value.replaceAll("'", "'\"'\"'") + "'"
const block = (...commands: string[]) => '(\nset -eu\n' + commands.join('\n') + '\n)'
export function installGuide(rootInput: string, hubInput: string, node: string, client: string, mode: "install" | "update" = "install") {
  const root = rootInput.replace(/\/+$/, "")
  if (!root.startsWith("/") || /[\x00-\x1f\x7f]/.test(root) || root.split("/").some(part => part === "." || part === "..")) throw new Error("본인 폴더의 절대 경로를 입력하세요. 줄바꿈과 . 또는 .. 경로는 사용할 수 없습니다.")
  let hub = ""
  try { const url = new URL(hubInput); if (["http:", "https:"].includes(url.protocol) && !url.username && !url.password && !url.search && !url.hash && url.pathname === "/" && !/\.(web\.app|firebaseapp\.com)$/.test(url.hostname)) hub = url.origin } catch { /* Report below. */ }
  if (mode === "install" && !hub) throw new Error("내부 허브의 http(s)://호스트:포트 주소를 입력하세요. 공개 웹 주소나 로그인 정보는 넣지 않습니다.")
  if (!["codex", "claude", "cli"].includes(client)) throw new Error("사용할 프로그램을 선택하세요.")
  const bin = root + "/client-0.1.0/venv/bin", state = root + "/state"
  const name = client === "claude" ? "Claude Code" : client === "codex" ? "Codex" : t("터미널 CLI")
  const installer = (...args: string[]) => block(
    'cowork_setup_file="$(mktemp)"',
    'trap \'rm -f "$cowork_setup_file"\' EXIT',
    'curl --fail --silent --show-error --location https://mcp-cowork-pgl.web.app/downloads/cowork-setup.pyz --output "$cowork_setup_file"',
    `python3 "$cowork_setup_file" --root ${quote(root)} ${args.join(' ')}`)
  return {
    title: mode === "update" ? t("기존 설치 업데이트") : t("{v0} · {v1} 서버 간편 설치", { v0: name, v1: node }),
    install: mode === "update" ? installer("--update") : installer(`--node ${quote(node)} --client ${quote(client)} --hub-url ${quote(hub)}`),
    autostart: installer("--autostart-only"),
    firstNote: client === "cli" ? t("메인 컨테이너에서 작은 작업으로 확인하세요.") : t("{v0}에 다음과 같이 요청하세요.", { v0: name }),
    first: client === "cli" ? block(`${quote(bin + '/cowork-run')} --config ${quote(state + '/runner.json')} \\\n  --cpus 1 --mem 64MiB --name first-test --detach -- /bin/echo cowork-ok`)
      : t("cowork로 {v0} 서버의 메인 환경에서 /bin/echo cowork-ok를\nCPU 1개, 메모리 64MiB로 실행해줘.\n부족하면 대기열에 넣고, 접수 후에는 감시하지 말고 마무리해줘.", { v0: node }),
    restart: block(`${quote(bin + '/cowork-connector')} start --config ${quote(state + '/connector.json')}`),
  }
}
