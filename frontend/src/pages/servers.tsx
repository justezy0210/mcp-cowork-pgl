import { t } from "@/lib/i18n"
import { useEffect, useState } from "react"
import { ArrowLeft, Cpu, MemoryStick, CircuitBoard } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import { DataTable, Detail, Empty, Loading, Notice, Pager, Panel, Refresh, Status } from "@/components/shared"
import { useResource } from "@/lib/hooks"
import { date, gib, reasons } from "@/lib/format"
import type { Job, Page, Profile, Server } from "@/lib/types"

function Resources({ server }: { server: Server }) {
  const metrics = [
    { title: t("CPU 예약"), value: t("{v0} / {v1}개", { v0: server.reserved_cpus, v1: server.cpus }), percent: server.reserved_cpus / server.cpus * 100, icon: Cpu },
    { title: t("메모리 예약"), value: `${gib(server.reserved_memory_mib)} / ${gib(server.memory_mib)} GiB`, percent: server.reserved_memory_mib / server.memory_mib * 100, icon: MemoryStick },
    { title: t("GPU 예약"), value: server.gpus.length ? t("{v0} / {v1}장", { v0: server.gpus.filter(g => g.reserved).length, v1: server.gpus.length }) : t("없음"), percent: server.gpus.length ? server.gpus.filter(g => g.reserved).length / server.gpus.length * 100 : 0, icon: CircuitBoard },
  ]
  return <div className="grid min-w-0 gap-5 sm:grid-cols-3">{metrics.map(metric => <div key={metric.title} className="min-w-0 space-y-2"><p className="flex items-center gap-2 text-xs text-muted-foreground"><metric.icon className="size-4" />{metric.title}</p><p className="text-sm font-semibold tabular-nums">{metric.value}</p><Progress aria-label={metric.title} value={Math.min(100, metric.percent)} className="h-1.5" /></div>)}</div>
}
export function ServersPage() {
  const resource = useResource<Server[]>("/servers", true)
  return <div className="page-stack"><Refresh onClick={resource.refresh} busy={resource.loading} updated={resource.updated} /><Notice error>{resource.error}</Notice>
    {!resource.data ? resource.loading && <Loading /> : !resource.data.length ? <Empty>{t("사용 가능한 서버가 없습니다. 관리자에게 서버 지정을 요청하세요.")}</Empty> : <div className="space-y-4">{resource.data.map(server => <a key={server.id} href={`#servers/${server.id}`} aria-label={t("{v0} 서버 작업 보기", { v0: server.id })} className="group block rounded-xl focus-visible:rounded-xl"><Panel title={t("서버 {v0}", { v0: server.id })} className="transition-colors group-hover:border-primary/50 group-hover:bg-accent/30" action={<Badge variant={server.enabled ? "secondary" : "outline"}>{server.enabled ? t("배정 허용") : t("새 배정 중지")}</Badge>}>
      <Resources server={server} /><div className="mt-5 flex flex-wrap gap-3 border-t pt-4 text-xs text-muted-foreground"><span>{t("실행 {count}건", { count: server.job_counts.RUNNING })}</span><span>{t("대기 {count}건", { count: server.job_counts.QUEUED })}</span>{server.job_counts.DISPATCHING > 0 && <span>{t("실행 준비 {count}건", { count: server.job_counts.DISPATCHING })}</span>}{server.job_counts.UNKNOWN > 0 && <span className="text-amber-700">{t("상태 확인 필요 {count}건", { count: server.job_counts.UNKNOWN })}</span>}</div>
    </Panel></a>)}</div>}
    <p className="text-xs text-muted-foreground">{t("신청량을 기준으로 예약한 자원입니다. 실제 CPU·메모리 사용률과는 다르며, 20초마다 갱신합니다.")}</p>
  </div>
}
function JobTable({ data, shared = false, queued = false }: { data: Page<Job>; shared?: boolean; queued?: boolean }) {
  return <DataTable headers={[t("순번"), shared ? t("작업 / 사용자") : t("작업"), t("상태"), ...(shared ? [] : [t("서버")]), t("신청 자원"), t("접수"), ...(shared && !queued ? [t("시작")] : [])]}
    rows={data.items.map((job, index) => [job.number ?? (data.page - 1) * data.page_size + index + 1,
      <Detail note={shared ? job.user_id + (job.multiple_candidates ? t(" · 여러 서버 후보") : "") : job.id.slice(0, 12)}>{job.name}</Detail>,
      <Detail note={job.reason ? t(reasons[job.reason] || job.reason) : undefined}><Status state={job.state} /></Detail>,
      ...(shared ? [] : [job.node_id]), `CPU ${job.cpus} · ${gib(job.memory_mib)} GiB · GPU ${job.gpu_count}`, date(job.created_at), ...(shared && !queued ? [date(job.started_at)] : []),
    ])} />
}
function ServerJobs({ id, group, revision }: { id: string; group: "active" | "queued"; revision: number }) {
  const [page, setPage] = useState(1)
  const resource = useResource<Page<Job>>(`/servers/${encodeURIComponent(id)}/jobs?page=${page}&page_size=20&group=${group}`, true)
  useEffect(() => { resource.refresh() }, [revision])
  useEffect(() => { if (resource.data && page > Math.max(1, Math.ceil(resource.data.total / 20))) setPage(Math.max(1, Math.ceil(resource.data.total / 20))) }, [resource.data, page])
  return <Panel title={group === "active" ? t("진행·예약 중") : t("대기 예약")} description={group === "active" ? t("실행 중인 작업을 먼저 표시하고, 실행 준비·상태 확인이 필요한 작업을 이어서 표시합니다.") : t("접수 순으로 표시하며, 실행 가능한 작업부터 배정합니다. 여러 서버를 후보로 신청한 작업은 각 서버에 표시됩니다.")}><div className="space-y-4"><Notice error>{resource.error}</Notice>{!resource.data ? resource.loading && <Loading /> : <><JobTable data={resource.data} shared queued={group === "queued"} /><Pager page={page} total={resource.data.total} onChange={setPage} busy={resource.loading} /></>}</div></Panel>
}
export function ServerDetailPage({ id }: { id: string }) {
  const resource = useResource<Server[]>("/servers", true), [revision, setRevision] = useState(0)
  const server = resource.data?.find(node => node.id === id)
  return <div className="page-stack"><div className="flex flex-wrap items-center justify-between gap-3"><Button asChild variant="outline"><a href="#servers"><ArrowLeft />{t("서버 목록")}</a></Button><Button variant="outline" disabled={resource.loading} onClick={() => { resource.refresh(); setRevision(v => v + 1) }}>{t("새로고침")}</Button></div><Notice error>{resource.error}</Notice>
    {!resource.data ? resource.loading && <Loading /> : !server ? <Notice error>{t("이 서버의 조회 권한이 없거나 등록된 서버가 아닙니다.")}</Notice> : <>
      <Panel title={t("예약 자원")} action={<Badge variant="secondary">{server.enabled ? t("배정 허용") : t("새 배정 중지")}</Badge>}><Resources server={server} /><p className="mt-5 text-sm text-muted-foreground">{t("추가 예약 가능 CPU {cpus}개 · 메모리 {memory} GiB", { cpus: Math.max(0, server.cpus - server.reserved_cpus), memory: gib(Math.max(0, server.memory_mib - server.reserved_memory_mib)) })}</p>{server.gpus.length > 0 && <div className="mt-4 grid gap-2 border-t pt-4 sm:grid-cols-2">{server.gpus.map(gpu => <p key={gpu.id} title={gpu.id} className="flex flex-wrap items-center gap-2 text-xs">{gpu.model}<Badge variant={gpu.reserved ? "default" : "secondary"}>{gpu.reserved ? t("예약 중") : t("예약 가능")}</Badge></p>)}</div>}<p className="mt-4 text-xs text-muted-foreground">{server.recent_report ? t("실행기 최근 보고 정상") : t("최근 실행기 보고 없음")} · {date(server.last_seen)}</p>{!server.recent_report && <p className="mt-2 text-xs text-muted-foreground">{t("최근 보고 여부는 SSH 접속 가능 여부와는 별개입니다.")}</p>}</Panel>
      <ServerJobs id={id} group="active" revision={revision} /><ServerJobs id={id} group="queued" revision={revision} />
    </>}
  </div>
}
type JobSettings = { server: string; completed: boolean; page: number }
export function JobsPage({ profile, settings, onChange }: { profile: Profile; settings: JobSettings; onChange(settings: JobSettings): void }) {
  const { server, completed, page } = settings
  const resource = useResource<Page<Job>>(`/jobs?page=${page}&page_size=20&include_completed=${completed}` + (server ? `&node_id=${encodeURIComponent(server)}` : ""), true)
  useEffect(() => { if (server && !profile.allowed_nodes.includes(server)) onChange({ ...settings, server: "", page: 1 }) }, [profile.allowed_nodes, server])
  useEffect(() => { if (resource.data && page > Math.max(1, Math.ceil(resource.data.total / 20))) onChange({ ...settings, page: Math.max(1, Math.ceil(resource.data.total / 20)) }) }, [resource.data, page])
  return <div className="page-stack"><Refresh onClick={resource.refresh} busy={resource.loading} updated={resource.updated} /><Panel title={t("작업 목록")} action={<Label className="min-h-11 gap-2.5"><Checkbox id="include-completed" checked={completed} onCheckedChange={value => onChange({ ...settings, completed: value === true, page: 1 })} />{t("완료된 작업 포함")}</Label>}>
    <div className="space-y-5"><div className="flex flex-wrap gap-2" role="group" aria-label={t("서버별 내 작업")}>{["", ...profile.allowed_nodes].map(id => <Button key={id} variant={server === id ? "default" : "outline"} aria-pressed={server === id} onClick={() => onChange({ ...settings, server: id, page: 1 })}>{id || t("전체")}</Button>)}</div><p className="text-xs text-muted-foreground">{t("실행 중 → 다른 미완료 작업 → 완료 작업 순으로, 각 그룹에서는 최근 접수 순으로 표시합니다. 순번은 실행 우선순위를 뜻하지 않습니다.")}{server && t(" 배정 전 작업은 신청한 후보 서버에 표시됩니다.")}</p><Notice error>{resource.error}</Notice>{!resource.data ? resource.loading && <Loading /> : <><JobTable data={resource.data} /><Pager page={page} total={resource.data.total} busy={resource.loading} onChange={value => onChange({ ...settings, page: value })} /></>}</div>
  </Panel></div>
}
