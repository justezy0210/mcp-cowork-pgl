import { t, localeTag } from "@/lib/i18n"
import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Badge } from "@/components/ui/badge"
import { ConfirmDialog, DataTable, Detail, Empty, Field, Loading, Notice, Pager, Panel, ServerChoices, Submit } from "@/components/shared"
import { useMutation, useResource } from "@/lib/hooks"
import { post } from "@/lib/session"
import { date } from "@/lib/format"
import type { Audit, Enrollment, ManagedUser, Page, Server, UsersPage } from "@/lib/types"

function useReload(refresh: () => void, revision: number) {
  const seen = useRef(revision)
  useEffect(() => { if (seen.current !== revision) { seen.current = revision; refresh() } }, [revision, refresh])
}
function EnrollmentForm({ row, nodes, onSaved }: { row: Enrollment; nodes: Server[]; onSaved(): void }) {
  const [selected, setSelected] = useState<string[]>([]), [reject, setReject] = useState(false)
  const mutation = useMutation()
  const review = (decision: string) => void mutation.run(async () => { await post(`/admin/enrollments/${encodeURIComponent(row.id)}/review`, { decision, allowed_nodes: decision === "APPROVED" ? selected : [] }); setReject(false); onSaved() })
  return <article className="space-y-5 rounded-xl border p-4 sm:p-5"><div className="space-y-2"><h3 className="font-semibold">{row.account_name}</h3><p className="text-sm text-muted-foreground">{row.email || t("Google 계정")}</p><p className="text-xs text-muted-foreground">UID/GID {row.uid}:{row.gid} {t("· Discord 채널")} {row.channel_id}</p></div><form className="form-stack" onSubmit={event => { event.preventDefault(); if (selected.length) review("APPROVED") }}><ServerChoices nodes={nodes} value={selected} onChange={setSelected} label={t("사용 가능한 서버")} disabled={mutation.pending} /><Notice error>{mutation.error}</Notice><div className="action-row"><Submit pending={mutation.pending} disabled={!selected.length}>{t("계정 승인")}</Submit><Button type="button" variant="outline" onClick={() => { mutation.clearError(); setReject(true) }} disabled={mutation.pending}>{t("등록 거절")}</Button></div></form><ConfirmDialog open={reject} onOpenChange={setReject} title={t("이 계정 등록을 거절할까요?")} description={t("{v0}의 등록 요청을 거절합니다. 사용자는 정보를 수정해 다시 제출할 수 있습니다.", { v0: row.account_name })} destructive confirmLabel={t("등록 거절")} pending={mutation.pending} error={mutation.error} onConfirm={() => review("REJECTED")} /></article>
}
function Enrollments({ nodes, revision }: { nodes: Server[]; revision: number }) {
  const [page, setPage] = useState(1), [saved, setSaved] = useState("")
  const resource = useResource<Page<Enrollment>>(`/admin/enrollments?page=${page}&page_size=20`)
  useReload(resource.refresh, revision)
  return <Panel title={t("새 계정 승인")} description={t("Google 계정·컨테이너 계정·UID/GID·Discord 채널을 확인하고 사용할 서버를 지정하세요.")}><div id="admin-enrollment-list" className="space-y-5"><Notice>{saved}</Notice><Notice error>{resource.error}</Notice>{!resource.data ? resource.loading && <Loading /> : <>{resource.data.items.length ? resource.data.items.map(row => <EnrollmentForm key={row.id} row={row} nodes={nodes} onSaved={() => { setSaved("계정 등록 요청을 처리했습니다."); resource.refresh() }} />) : <Empty>{t("승인을 기다리는 계정이 없습니다.")}</Empty>}<Pager page={page} total={resource.data.total} busy={resource.loading} onChange={setPage} /></>}</div></Panel>
}
function UserForm({ user, nodes, notificationAvailable, onSaved }: { user: ManagedUser; nodes: Server[]; notificationAvailable: boolean; onSaved(): void }) {
  const [selected, setSelected] = useState(user.allowed_nodes), [confirm, setConfirm] = useState(false), [webhook, setWebhook] = useState("")
  const grants = useMutation(), notification = useMutation()
  return <article className="rounded-xl border p-4 sm:p-6"><div className="mb-6 flex flex-wrap items-center gap-3"><h3 className="font-semibold">{user.id}</h3><Badge variant="outline">UID/GID {user.uid ?? t("미설정")}:{user.gid ?? t("미설정")}</Badge>{!user.enabled && <Badge variant="destructive">{t("비활성 계정")}</Badge>}</div><div className="grid min-w-0 gap-7 xl:grid-cols-2">
    <form className="form-stack" onSubmit={event => { event.preventDefault(); grants.clearError(); setConfirm(true) }}><ServerChoices nodes={nodes.map(node => ({ id: node.id }))} value={selected} onChange={setSelected} disabled={grants.pending} /><div className="action-row"><Submit pending={grants.pending}>{t("권한 저장")}</Submit></div></form>
    <form className="form-stack border-t pt-6 xl:border-t-0 xl:border-l xl:pt-0 xl:pl-7" autoComplete="off" onSubmit={event => {
      event.preventDefault(); const value = webhook.trim(); setWebhook("")
      void notification.run(async () => { await post(`/admin/users/${encodeURIComponent(user.id)}/notifications`, { webhook_url: value }); onSaved() })
    }}><div className="space-y-1"><h4 className="text-sm font-medium">{t("Discord 알림")}</h4><p className="text-xs text-muted-foreground">{user.notification.configured ? t("연결된 채널: {v0}", { v0: user.notification.channel_id }) : t("채널 미설정")}</p></div><Field id={`notification-${user.id}`} label={t("사용자 전용 채널 웹훅 URL")}><Input id={`notification-${user.id}`} type="password" value={webhook} onChange={e => setWebhook(e.target.value)} required maxLength={512} autoComplete="off" spellCheck={false} disabled={!user.enabled || !notificationAvailable || notification.pending} /></Field><Notice error>{notification.error}</Notice><div className="action-row"><Submit pending={notification.pending} disabled={!user.enabled || !notificationAvailable}>{t("채널 연결")}</Submit></div></form>
  </div><ConfirmDialog open={confirm} onOpenChange={setConfirm} title={t("서버 권한을 변경할까요?")} description={t("{v0}의 허용 서버를 {v1}으로 변경합니다. 변경은 새 작업 제출·배정에 적용됩니다.", { v0: user.id, v1: selected.join(", ") || t("없음") })} confirmLabel={t("권한 저장")} pending={grants.pending} error={grants.error} onConfirm={() => void grants.run(async () => { await post(`/admin/users/${encodeURIComponent(user.id)}/grants`, { allowed_nodes: selected, expected_nodes: user.allowed_nodes }); setConfirm(false); onSaved() })} /></article>
}
function Users({ nodes, revision }: { nodes: Server[]; revision: number }) {
  const [page, setPage] = useState(1), [saved, setSaved] = useState("")
  const resource = useResource<UsersPage>(`/admin/users?page=${page}&page_size=20`)
  useReload(resource.refresh, revision)
  return <Panel title={t("사용자별 서버 권한·Discord 채널")} description={t("새 작업에 적용되는 권한과 개인 알림 채널을 관리합니다.")}><div id="admin-user-list" className="space-y-5"><details className="rounded-lg border bg-muted/30 p-4 text-sm"><summary className="cursor-pointer font-medium">{t("사용자별 Discord 채널 연결 방법")}</summary><p className="pt-3 text-muted-foreground">{t("연구실 Discord 서버에 사용자별 비공개 채널을 만들고 관리자와 해당 사용자에게 보기 권한을 주세요. 채널 편집 → 연동 → 웹훅에서 URL을 복사해 연결하세요. 새 작업부터 적용되며, 기존 작업은 기존 채널로 보냅니다.")}</p></details><Notice>{saved}</Notice><Notice error>{resource.error}</Notice>{!resource.data ? resource.loading && <Loading /> : <>{resource.data.items.length ? resource.data.items.map(user => <UserForm key={user.id + JSON.stringify([user.allowed_nodes, user.notification])} user={user} nodes={nodes} notificationAvailable={resource.data!.notification_registration_available} onSaved={() => { setSaved("사용자 설정을 저장했습니다."); resource.refresh() }} />) : <Empty />}<Pager page={page} total={resource.data.total} busy={resource.loading} onChange={setPage} /></>}</div></Panel>
}
function ServerForm({ node, onSaved }: { node: Server; onSaved(): void }) {
  const [cpus, setCpus] = useState(node.cpus), [memory, setMemory] = useState(node.memory_mib), [enabled, setEnabled] = useState(node.enabled), [confirm, setConfirm] = useState(false)
  const mutation = useMutation()
  return <form className="form-stack rounded-xl border p-4 sm:p-6" onSubmit={event => { event.preventDefault(); mutation.clearError(); setConfirm(true) }}><div className="space-y-2"><h3 className="font-semibold">{t("서버 {id}", { id: node.id })}</h3><p className="text-xs text-muted-foreground">{t("현재 예약 CPU {cpus} · 메모리 {memory} MiB · GPU {gpus}개", { cpus: node.reserved_cpus, memory: node.reserved_memory_mib.toLocaleString(localeTag()), gpus: node.gpus.length })}</p></div><div className="field-pair"><Field id={`budget-${node.id}-cpus`} label={t("CPU 예약 예산")}><Input id={`budget-${node.id}-cpus`} name="cpus" type="number" required min={1} max={65536} step={1} value={cpus} onChange={e => setCpus(Number(e.target.value))} /></Field><Field id={`budget-${node.id}-memory`} label={t("메모리 예약 예산 (MiB)")}><Input id={`budget-${node.id}-memory`} name="memory_mib" type="number" required min={1} max={2 ** 40} step={1} value={memory} onChange={e => setMemory(Number(e.target.value))} /></Field></div><Label className="min-h-11 gap-3"><Checkbox checked={enabled} onCheckedChange={value => setEnabled(value === true)} />{t("새 작업 배정 허용")}</Label><div className="action-row"><Submit pending={mutation.pending}>{t("서버 설정 저장")}</Submit></div><ConfirmDialog open={confirm} onOpenChange={setConfirm} title={t("서버 설정을 저장할까요?")} description={t("{v0}: CPU {v1}개, 메모리 {v2} MiB, {v3}. 현재 예약량보다 낮은 예산은 저장할 수 없습니다.", { v0: node.id, v1: cpus, v2: memory.toLocaleString(localeTag()), v3: enabled ? t("새 작업 배정 허용") : t("새 작업 배정 중지") })} confirmLabel={t("서버 설정 저장")} pending={mutation.pending} error={mutation.error} onConfirm={() => void mutation.run(async () => { await post(`/admin/servers/${encodeURIComponent(node.id)}`, { cpus, memory_mib: memory, enabled, expected: { cpus: node.cpus, memory_mib: node.memory_mib, enabled: node.enabled } }); setConfirm(false); onSaved() })} /></form>
}
function AuditLog({ revision }: { revision: number }) {
  const [page, setPage] = useState(1)
  const resource = useResource<Page<Audit>>(`/admin/audit?page=${page}&page_size=20`)
  useReload(resource.refresh, revision)
  return <Panel title={t("관리 변경 이력")}><Notice error>{resource.error}</Notice>{!resource.data ? resource.loading && <Loading /> : <><DataTable headers={[t("시각 / 관리자"), t("변경"), t("대상"), t("변경 내용")]} rows={resource.data.items.map(row => [<Detail note={row.actor}>{date(row.created_at)}</Detail>, row.action, row.target, <Detail note={t("이전: ") + JSON.stringify(row.before_value)}>{JSON.stringify(row.after_value)}</Detail>])} /><Pager page={page} total={resource.data.total} busy={resource.loading} onChange={setPage} /></>}</Panel>
}
export function AdminPage() {
  const resource = useResource<Server[]>("/admin/servers"), [revision, setRevision] = useState(0), [saved, setSaved] = useState("")
  return <div className="page-stack"><div className="flex justify-end"><Button variant="outline" disabled={resource.loading} onClick={() => { resource.refresh(); setRevision(v => v + 1) }}>{t("관리 정보 새로고침")}</Button></div><Notice error>{resource.error}</Notice>{!resource.data ? resource.loading && <Loading /> : <Tabs defaultValue="enrollments" className="gap-6"><TabsList className="w-full flex-wrap justify-start gap-1 bg-muted p-1.5 group-data-[orientation=horizontal]/tabs:h-auto">{[["enrollments", t("새 계정 승인")], ["users", t("사용자 권한")], ["servers", t("서버 설정")], ["audit", t("변경 이력")]].map(([value, label]) => <TabsTrigger key={value} value={value} className="h-auto min-h-11 flex-auto basis-24 px-3">{label}</TabsTrigger>)}</TabsList>
      <TabsContent value="enrollments"><Enrollments nodes={resource.data} revision={revision} /></TabsContent>
      <TabsContent value="users"><Users nodes={resource.data} revision={revision} /></TabsContent>
      <TabsContent value="servers"><Panel title={t("서버 예약 예산·배정")} description={t("현재 예약량보다 낮은 예산은 저장할 수 없습니다. 배정 중지는 새 배정에 적용됩니다.")}><div id="admin-server-list" className="space-y-5"><Notice>{saved}</Notice>{resource.data.map(node => <ServerForm key={node.id + JSON.stringify([node.cpus, node.memory_mib, node.enabled])} node={node} onSaved={() => { setSaved("서버 설정을 저장했습니다."); resource.refresh() }} />)}</div></Panel></TabsContent>
      <TabsContent value="audit"><AuditLog revision={revision} /></TabsContent>
    </Tabs>}
  </div>
}
