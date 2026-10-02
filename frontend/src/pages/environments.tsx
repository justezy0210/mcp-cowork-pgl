import { t } from "@/lib/i18n"
import { Trans } from "@/components/trans"
import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { NativeSelect } from "@/components/ui/native-select"
import { Command, DataTable, Detail, Field, Loading, Notice, Pager, Panel, Refresh, Status, Submit } from "@/components/shared"
import { post } from "@/lib/session"
import { useMutation, useResource } from "@/lib/hooks"
import { date } from "@/lib/format"
import type { Connector, Environment, Page, Profile, Registration, SSHAddress } from "@/lib/types"

export function EnvironmentTable({ data }: { data: Page<Environment> }) {
  return <DataTable headers={[t("컨테이너 / 서버"), t("SSH 접속 / 설치 기준 경로"), "UID : GID", t("상태")]} rows={data.items.map(row => [<Detail note={row.node_id}>{row.name}</Detail>, <Detail note={row.workdir}>{row.ssh_target}</Detail>, `${row.uid ?? "—"} : ${row.gid ?? "—"}`, <Status state={row.allowed ? row.status : t("서버 권한 없음")} />])} />
}
export function EnvironmentsPage({ profile }: { profile: Profile }) {
  const [envPage, setEnvPage] = useState(1), [requestPage, setRequestPage] = useState(1)
  const envs = useResource<Page<Environment>>(`/environments?page=${envPage}&page_size=20`, true)
  const requests = useResource<Page<Registration>>(`/registrations?page=${requestPage}&page_size=20`, true)
  const connectors = useResource<Connector[]>("/connectors", true)
  const [connector, setConnector] = useState(""), [node, setNode] = useState(profile.allowed_nodes[0] || "")
  const [command, setCommand] = useState(""), [parsed, setParsed] = useState<{ command: string; address: SSHAddress } | null>(null)
  const [notice, setNotice] = useState("")
  const parse = useMutation(), submit = useMutation(), serial = useRef(0), intent = useRef({ payload: "", key: "" })
  useEffect(() => { if (connectors.data && !connectors.data.some(item => item.id === connector)) setConnector(connectors.data[0]?.id || "") }, [connectors.data, connector])
  useEffect(() => { if (!profile.allowed_nodes.includes(node)) setNode(profile.allowed_nodes[0] || "") }, [profile.allowed_nodes, node])
  const refresh = () => { envs.refresh(); requests.refresh(); connectors.refresh() }
  return <div className="page-stack"><Refresh onClick={refresh} busy={envs.loading || requests.loading || connectors.loading} updated={envs.updated} /><Notice>{notice}</Notice>
    <Panel title={t("연결된 컨테이너")} description={t("허용 서버·UID/GID·자원 검사를 통과하면 추가 승인 없이 작업 대상으로 사용할 수 있습니다.")}><Notice error>{envs.error}</Notice>{!envs.data ? envs.loading && <Loading /> : <><EnvironmentTable data={envs.data} /><Pager page={envPage} total={envs.data.total} busy={envs.loading} onChange={setEnvPage} /></>}</Panel>
    <Panel title={t("다른 서버의 내 컨테이너 연결")} description={t("이미 있는 본인 컨테이너를 Cowork 작업 대상으로 등록합니다.")}><div className="space-y-6">
      <div className="space-y-3 rounded-lg border bg-muted/30 p-4"><p className="text-sm"><Trans text="먼저 {install}하고, {terminal}에서 SSH 공개키를 등록하세요." values={{ install: <a className="text-link" href="#guide">{t("메인 프로그램을 설치")}</a>, terminal: <strong>{t("작업을 보낼 메인 컨테이너 터미널")}</strong> }} /></p><Command text="ssh-copy-id -p {port} {user}@{ip}" /><p className="text-xs text-muted-foreground"><Trans text="{port}는 대상 컨테이너의 SSH 포트, {user}는 본인 계정, {ip}는 접속 주소입니다. 비밀번호는 터미널에 입력하세요. 이미 비밀번호 없이 SSH 키 접속이 된다면 생략할 수 있습니다." values={{ port: <code>{"{port}"}</code>, user: <code>{"{user}"}</code>, ip: <code>{"{ip}"}</code> }} /></p></div>
      <Notice error>{connectors.error || parse.error || submit.error}</Notice>
      <form id="ssh-form" className="form-stack" onSubmit={event => {
        event.preventDefault()
        if (!parsed || parsed.command !== command.trim() || !connector || !node) return
        const body = { connector_id: connector, node_id: node, ssh_command: parsed.command }
        const payload = JSON.stringify(body)
        if (intent.current.payload !== payload) intent.current = { payload, key: crypto.randomUUID() }
        void submit.run(async () => { await post("/containers", { ...body, request_key: intent.current.key }); setCommand(""); setParsed(null); intent.current = { payload: "", key: "" }; setNotice("컨테이너 연결을 요청했습니다. 연결 프로그램이 SSH와 실행 조건을 확인하면 바로 사용할 수 있습니다."); requests.refresh() })
      }}>
        <div className="field-pair"><Field id="ssh-connector" label={t("작업을 보낼 메인 연결 프로그램")}><NativeSelect id="ssh-connector" required value={connector} onChange={e => setConnector(e.target.value)} disabled={connectors.loading && !connectors.data}><option value="">{t("메인 연결 프로그램 선택")}</option>{connectors.data?.map(item => <option key={item.id} value={item.id}>{item.name} · {item.online ? t("연결됨") : t("연결 대기")}</option>)}</NativeSelect></Field><Field id="ssh-node" label={t("대상 컨테이너가 있는 서버")}><NativeSelect id="ssh-node" required value={node} onChange={e => setNode(e.target.value)}><option value="">{t("서버 선택")}</option>{profile.allowed_nodes.map(id => <option key={id} value={id}>{id}</option>)}</NativeSelect></Field></div>
        <Field id="ssh-command" label={t("컨테이너 SSH 접속 명령")} help={t("ssh-copy-id가 아닌 실제 접속 명령을 입력하세요.")}><Input id="ssh-command" required value={command} onChange={event => { serial.current++; setCommand(event.target.value); setParsed(null); setNotice(""); parse.clearError() }} spellCheck={false} autoComplete="off" placeholder={t("ssh -p 11010 {v0}@서버주소", { v0: profile.user_id })} aria-describedby="ssh-command-help" /></Field>
        <div className="space-y-3"><Button id="ssh-parse" type="button" variant="outline" disabled={parse.pending || !command.trim()} onClick={() => {
          const current = ++serial.current, value = command.trim(); setParsed(null)
          void parse.run(async () => { const address = await post<SSHAddress>("/ssh/parse", { ssh_command: value }); if (current === serial.current) setParsed({ command: value, address }) })
        }}>{parse.pending ? t("확인 중…") : t("입력 내용 확인")}</Button>{parsed && <Notice>{t("계정")} {parsed.address.user} {t("· 주소")} {parsed.address.host} {t("· 포트")} {parsed.address.port}</Notice>}<p className="text-xs text-muted-foreground">{t("입력한 계정·주소·포트를 보여줍니다. 실제 SSH 접속 검사는 컨테이너 연결 요청 후 진행합니다.")}</p></div>
        <div className="space-y-4 border-t pt-5"><p className="text-sm text-muted-foreground">{t("요청하면 메인 연결 프로그램이 SSH로 UID/GID와 공유 경로를 확인합니다. 검사를 통과하면 추가 승인 없이 작업을 보낼 수 있습니다. 새 컨테이너를 만들거나 계산을 시작하지 않습니다.")}</p><p className="text-xs text-muted-foreground">{t("설치 공유 폴더는 그대로 사용하고, 프로젝트 경로와 계산 자원은 작업마다 지정하세요. 연결 프로그램이 꺼져 있으면 다시 연결될 때 요청을 처리합니다.")}</p>{connectors.data?.length === 0 && <Notice>{t("먼저 메인 연결 프로그램을 설치하고 시작하세요.")}</Notice>}<div className="action-row"><Submit id="ssh-submit" pending={submit.pending} disabled={!parsed || parsed.command !== command.trim() || !connector || !node}>{t("컨테이너 연결 요청")}</Submit></div></div>
      </form>
    </div></Panel>
    <Panel title={t("컨테이너 연결 진행 상황")} description={t("SSH 연결·실행 조건 확인 → 자동 등록 → 작업 가능 순서로 진행합니다.")}><Notice error>{requests.error}</Notice>{!requests.data ? requests.loading && <Loading /> : <><DataTable headers={[t("대상"), t("상태"), t("접수")]} rows={requests.data.items.map(row => [<Detail note={row.target.workdir}>{row.target.node_id} · {row.target.user}@{row.target.host}:{row.target.port}</Detail>, <Detail note={row.error_code}><Status state={row.state} /></Detail>, date(row.created_at)])} /><Pager page={requestPage} total={requests.data.total} busy={requests.loading} onChange={setRequestPage} /></>}</Panel>
  </div>
}
