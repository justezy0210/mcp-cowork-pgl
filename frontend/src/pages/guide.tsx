import { t } from "@/lib/i18n"
import { Trans } from "@/components/trans"
import { useEffect, useRef, useState } from "react"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { NativeSelect } from "@/components/ui/native-select"
import { Command, Field, Notice, Panel, Submit } from "@/components/shared"
import { TokenDelivery } from "@/components/token-delivery"
import { installGuide } from "@/lib/install-guide"
import { useMutation } from "@/lib/hooks"
import { post } from "@/lib/session"
import type { IssuedToken, Profile } from "@/lib/types"

export function GuidePage({ profile, pendingToken, onToken }: { profile: Profile; pendingToken: IssuedToken | null; onToken(token: IssuedToken | null): void }) {
  const [mode, setMode] = useState<"install" | "update">("install")
  const [client, setClient] = useState("codex"), [node, setNode] = useState(profile.allowed_nodes.length === 1 ? profile.allowed_nodes[0] : "")
  const [root, setRoot] = useState(`/10Gdata/${profile.user_id}/cowork`), [hub, setHub] = useState("http://192.168.10.41:8080")
  const issue = useMutation(), alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const allowed = profile.allowed_nodes.join(",")
  useEffect(() => { if (!profile.allowed_nodes.includes(node)) setNode(profile.allowed_nodes.length === 1 ? profile.allowed_nodes[0] : "") }, [allowed])
  let result: ReturnType<typeof installGuide> | null = null, error = ""
  if (mode === "update" || profile.allowed_nodes.includes(node)) {
    try { result = installGuide(root, hub, node, client, mode) }
    catch (failure) { error = (failure as Error).message }
  }
  return <div className="page-stack">
    <Panel title={t("메인 컨테이너에 설치하기")} description={t("평소 에이전트를 실행하는 원격 컨테이너에서 아래 명령을 실행하세요.")}>
      <div className="space-y-6">
        <div className="flex flex-wrap gap-2"><Badge variant="secondary">{profile.user_id}{profile.identity.configured && ` · ${profile.identity.uid}:${profile.identity.gid}`}</Badge><Badge variant="outline">{t("허용 서버")} {profile.allowed_nodes.join(", ") || t("없음")}</Badge></div>
        <Field id="guide-mode" label={t("진행할 작업")}><NativeSelect id="guide-mode" value={mode} onChange={e => setMode(e.target.value as "install" | "update")}><option value="install">{t("처음 설치 · 중단된 설치 다시 진행")}</option><option value="update">{t("기존 설치 업데이트")}</option></NativeSelect></Field>
        {mode === "install" && <div className="field-pair">
          <Field id="guide-client" label={t("사용할 프로그램")}><NativeSelect id="guide-client" value={client} onChange={e => setClient(e.target.value)}><option value="codex">Codex</option><option value="claude">Claude Code</option><option value="cli">{t("터미널 CLI만 사용")}</option></NativeSelect></Field>
          <Field id="guide-node" label={t("메인 컨테이너가 있는 서버")}><NativeSelect id="guide-node" value={node} onChange={e => setNode(e.target.value)}><option value="">{t("메인 서버 선택")}</option>{profile.allowed_nodes.map(id => <option key={id} value={id}>{t("서버 {id}", { id })}</option>)}</NativeSelect></Field>
        </div>}
        <Field id="guide-root" label={mode === "update" ? t("기존 설치 폴더") : t("설치할 공유 폴더")} help={mode === "update" ? t("처음 설치할 때 사용한 경로를 그대로 지정하세요. 저장된 서버·허브·토큰과 기존 MCP 등록을 재사용합니다.") : t("본인 소유이며 사용할 모든 컨테이너에서 같은 경로로 보이는 폴더입니다. 프로젝트가 바뀌어도 다시 설치하지 않습니다.")}><Input id="guide-root" value={root} maxLength={1024} spellCheck={false} autoComplete="off" onChange={e => setRoot(e.target.value)} aria-describedby="guide-root-help" /></Field>
        {mode === "install" && <details className="rounded-lg border p-4"><summary className="cursor-pointer text-sm font-medium">{t("고급 설정 · 내부 허브 주소")}</summary><div className="pt-4"><Field id="guide-hub" label={t("컨테이너에서 접속할 내부 허브")} help={t("관리자가 다른 내부 주소를 안내한 경우에만 바꾸세요.")}><Input id="guide-hub" type="url" value={hub} maxLength={2048} onChange={e => setHub(e.target.value)} aria-describedby="guide-hub-help" /></Field></div></details>}
        <Notice error>{error}</Notice>
        {!result && !error && <Notice>{profile.allowed_nodes.length ? t("메인 서버를 선택하면 설치 명령이 바로 표시됩니다.") : t("관리자 승인 후 사용 가능한 서버가 지정되면 설치할 수 있습니다.")}</Notice>}
      </div>
    </Panel>
    {result && <div id="guide-result" className="page-stack">
      <Panel title={mode === "update" ? t("업데이트 명령 복사 → 기존 메인 컨테이너에서 실행") : t("1. 명령 복사 → 메인 컨테이너에서 실행")} description={result.title}>
        <div className="space-y-4"><Command id="guide-install" text={result.install} />
          <p className="text-sm text-muted-foreground"><Trans text="본인 계정으로 실행하세요. 자동 시작 등록에 필요한 경우에만 {sudo} 암호를 요청합니다." values={{ sudo: <code>sudo</code> }} /></p>
          {mode === "update" ? <p className="text-sm text-muted-foreground">{t("새 토큰이나 서버 정보를 입력하지 않습니다. 수정된 프로그램으로 교체하고 이전 소스는 정리합니다. 실행 중인 에이전트에서 MCP를 다시 연결하거나 에이전트를 재시작하세요.")}</p> : <p className="text-xs text-muted-foreground">{t("Linux·Python 3.12 이상·curl과 선택한 에이전트가 필요합니다. 설치 중 GPU 드라이버를 검사하지 않습니다.")}</p>}
        </div>
      </Panel>
      {mode === "install" && <>
        <Panel title={t("2. 처음 설치할 때만 토큰 붙여 넣기")} description={t("토큰은 컨테이너의 프로그램이 허브에 본인 계정임을 증명하는 인증 정보입니다.")}>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">{t("설치 프로그램이 토큰을 요청하면 아래에서 발급·복사해 터미널에 붙여 넣으세요. 입력은 화면에 표시되지 않습니다. 이미 저장된 토큰이 있으면 다시 묻지 않습니다.")}</p>
            <Notice error>{issue.error}</Notice>
            {!pendingToken && <form onSubmit={event => {
              event.preventDefault()
              void issue.run(async () => { const token = await post<IssuedToken>("/tokens", { name: `${profile.user_id}-${node}-MCP` }); if (alive.current) onToken(token) })
            }}><div className="action-row"><Submit id="guide-create-token" pending={issue.pending}>{t("토큰 발급")}</Submit></div></form>}
            {pendingToken && <TokenDelivery key={pendingToken.id} token={pendingToken} onSaved={() => onToken(null)} />}
            <details className="text-sm"><summary className="cursor-pointer text-muted-foreground">{t("이미 발급한 토큰이 있나요?")}</summary><p className="pt-3 text-muted-foreground"><Trans text="복사해 둔 토큰을 그대로 사용하세요. 프로그램에 저장된 토큰은 설치·업데이트 시 재사용합니다. 발급 내역과 폐기는 {tokens}에서 확인할 수 있습니다." values={{ tokens: <a href="#tokens" className="text-link">{t("개인 토큰 관리")}</a> }} /></p></details>
          </div>
        </Panel>
        <Notice><p><Trans text="설치·연결 확인이 끝나면 에이전트를 다시 시작하세요. {environments}에서 사용 가능 상태를 확인하면 바로 작업할 수 있습니다. 컨테이너별 추가 승인은 없습니다." values={{ environments: <a className="text-link" href="#environments">{t("작업할 컨테이너")}</a> }} /></p></Notice>
        <details className="rounded-xl border bg-card p-6"><summary className="cursor-pointer font-medium">{t("설치 후: 첫 작업 · 다른 서버 연결")}</summary><div className="space-y-4 pt-5 text-sm text-muted-foreground"><p>{result.firstNote}</p><Command id="guide-first" text={result.first} /><p><Trans text="{jobs}에서 완료와 로그의 {output}, Discord 시작·종료 알림을 확인하세요." values={{ jobs: <a className="text-link" href="#jobs">{t("내 작업")}</a>, output: <code>cowork-ok</code> }} /></p><p><Trans text="다른 서버는 메인에서 SSH 키 접속을 준비한 뒤 {environments}에서 연결하세요. 해당 프로젝트 경로에 접근할 수 있어야 합니다." values={{ environments: <a className="text-link" href="#environments">{t("작업할 컨테이너")}</a> }} /></p></div></details>
      </>}
      <details className="rounded-xl border bg-card p-6"><summary className="cursor-pointer font-medium">{t("문제가 있을 때: 자동 시작 · 수동 연결")}</summary><div className="space-y-5 pt-5 text-sm text-muted-foreground">
        <p><Trans text="설치 결과가 {configured}이면 컨테이너의 Supervisor가 시작될 때 자동 연결됩니다. 연결 프로그램의 자동 시작은 중단된 계산의 자동 재개와 별개입니다." values={{ configured: <strong>{t("자동 시작 등록 완료")}</strong> }} /></p>
        <div className="space-y-3"><p className="font-medium text-foreground">{t("기존 설치에 자동 시작만 추가")}</p><Command id="guide-autostart" text={result.autostart} /></div>
        <div className="space-y-3"><p className="font-medium text-foreground">{t("연결 프로그램 수동 시작")}</p><Command id="guide-restart" text={result.restart} /></div>
      </div></details>
    </div>}
    <p className="text-right text-xs"><a className="text-link" href="https://github.com/justezy0210/mcp-cowork-pgl/blob/main/docs/first-user-guide.md" target="_blank" rel="noopener noreferrer">{t("전체 설치 안내·오류 해결 보기 ↗")}</a></p>
  </div>
}
