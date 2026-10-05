import { Fragment, useEffect, useRef, useState, type MouseEvent } from "react"
import { AlertCircle, ArrowDown, ArrowLeft, ArrowUp, ArrowUpDown, Check, ChevronRight, Copy, Database, FolderOpen, GitMerge, Grid2X2, Menu, Search, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { NativeSelect } from "@/components/ui/native-select"
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { CatalogAvailability } from "@/components/catalog-availability"
import { Empty, Loading, Notice, Pager, Panel } from "@/components/shared"
import { useHash, useResource } from "@/lib/hooks"
import { t, localeTag } from "@/lib/i18n"
import { cn } from "@/lib/utils"
import { catalogHref, fileSize, type CatalogDataset, type CatalogFile, type CatalogOriginalGroup, type CatalogOverview, type CatalogSample } from "@/lib/catalog"
import type { Page } from "@/lib/types"

function Evidence({ status }: { status: string }) {
  return <Badge variant="outline" className={cn("font-normal", status === "confirmed" ? "border-primary/25 bg-accent text-primary" : "text-muted-foreground")}>{t(status === "confirmed" ? "확인됨" : status === "provisional" ? "확인 예정" : "추정")}</Badge>
}
function DatasetName({ name }: { name: string }) { return <>{name === "Unclassified" ? t("분류 미확정") : name}</> }
const sampleHref = (sample: CatalogSample) => catalogHref(sample.project_id, sample.species_id, sample.id)

function SequenceCount({ file, metric }: { file?: CatalogFile | null; metric: "reads" | "bases" }) {
  const stats = file?.stats, value = stats?.[metric]
  if (stats?.status === "completed" && typeof value === "number" && Number.isSafeInteger(value) && value >= 0) {
    return <span className="whitespace-nowrap font-mono text-xs tabular-nums">{value.toLocaleString("en-US")}</span>
  }
  return <span className="whitespace-nowrap text-xs text-muted-foreground">{t(stats?.status === "failed" ? "계산 실패" : stats?.status === "unavailable" || stats?.status === "completed" ? "통계 확인 필요" : "미계산")}</span>
}

function QualityRate({ file }: { file?: CatalogFile | null }) {
  const stats = file?.stats, value = stats?.q30_percent
  if (stats?.status === "completed" && typeof stats.bases === "number" && stats.bases > 0 && typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100) {
    return <span className="whitespace-nowrap font-mono text-xs tabular-nums" title={t("품질 점수가 Q30 이상인 염기의 비율입니다.")}>{value.toLocaleString("en-US", { maximumFractionDigits: 2 })}%</span>
  }
  return <span className="text-xs text-muted-foreground" title={t("Q30 미확인")} aria-label={t("Q30 미확인")}>—</span>
}

function CopyFilePath({ file }: { file: Pick<CatalogFile, "name" | "path"> }) {
  const [status, setStatus] = useState<"idle" | "copied" | "error">("idle")
  useEffect(() => {
    if (status === "idle") return
    const timer = setTimeout(() => setStatus("idle"), 2500)
    return () => clearTimeout(timer)
  }, [status])
  const message = t(status === "copied" ? "복사됨" : status === "error" ? "복사하지 못했습니다. 파일명을 눌러 경로를 확인하세요." : "경로 복사")
  return <Button type="button" variant="ghost" size="icon" className={cn("shrink-0", status === "copied" ? "text-primary" : status === "error" ? "text-destructive" : "text-muted-foreground")} aria-label={`${message}: ${file.name}`} title={message} onClick={async () => {
    try { await navigator.clipboard.writeText(file.path); setStatus("copied") }
    catch { setStatus("error") }
  }}>
    {status === "copied" ? <Check aria-hidden="true" /> : status === "error" ? <AlertCircle aria-hidden="true" /> : <Copy aria-hidden="true" />}
    <span className="sr-only" role="status">{status !== "idle" ? message : ""}</span>
  </Button>
}

function SplitFiles({ inputs, select }: { inputs: CatalogFile["inputs"]; select(file: CatalogFile): void }) {
  const [limit, setLimit] = useState(50)
  return <><ul className="space-y-3">{inputs.slice(0, limit).map(input => {
    const file = input.file, name = input.path.split("/").at(-1) || input.path
    return <li key={input.path} className="min-w-0 space-y-2 border-l-2 border-primary/20 pl-4">
      <div className="flex flex-wrap items-center justify-between gap-x-5 gap-y-2">
        <div className="flex min-w-0 items-center gap-2">{file ? <button className="min-w-0 break-all text-left font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-ring" onClick={() => select(file)}>{name}</button> : <span className="break-all font-medium">{name}</span>}<CopyFilePath file={{ name, path: input.path }} /></div>
        <div className="flex flex-wrap items-center gap-3"><Evidence status={input.status} /><span className="text-xs text-muted-foreground">{t("Read 수")} <SequenceCount file={file} metric="reads" /></span><span className="text-xs text-muted-foreground">{t("Bases 수")} <SequenceCount file={file} metric="bases" /></span><span className="text-xs text-muted-foreground">Q30 <QualityRate file={file} /></span></div>
      </div>
      <p className="break-all font-mono text-xs leading-5 text-muted-foreground">{input.path}</p>
      {!!file?.inputs.length && <details className="group/input pt-1"><summary className="flex w-fit cursor-pointer list-none items-center gap-2 rounded py-2 text-xs text-primary focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden"><ChevronRight aria-hidden="true" className="size-3 transition-transform group-open/input:rotate-90" />{t("분할 파일 {count}개", { count: file.inputs.length })}</summary><div className="pt-2"><SplitFiles inputs={file.inputs} select={select} /></div></details>}
    </li>
  })}</ul>{inputs.length > limit && <Button variant="outline" onClick={() => setLimit(previous => previous + 50)}>{t("원본 더 보기")}</Button>}</>
}

function OriginalGroup({ group, sample, query, role, select }: { group: CatalogOriginalGroup; sample: CatalogSample; query: string; role: string; select(file: CatalogFile): void }) {
  const [open, setOpen] = useState(false), [page, setPage] = useState(1)
  const params = new URLSearchParams({ sample: group.sample_id, dataset: group.dataset_id, view: "originals", q: query, role, page: String(page), page_size: "25" })
  const resource = useResource<Page<CatalogFile>>(open ? `/catalog/files?${params}` : null, false, true)
  const dataset = sample.datasets.find(d => d.id === group.dataset_id)!
  const id = `originals-${group.sample_id}-${group.dataset_id}`
  return <div className="rounded-lg border">
    <button type="button" className="flex min-h-12 w-full items-center gap-3 rounded-lg px-4 py-3 text-left text-sm focus-visible:outline-2 focus-visible:outline-ring" aria-expanded={open} aria-controls={id} onClick={() => setOpen(previous => !previous)}>
      <ChevronRight aria-hidden="true" className={cn("size-4 shrink-0 transition-transform", open && "rotate-90")} /><span className="min-w-0 flex-1 break-words">{sample.name} · <DatasetName name={dataset.name} /></span><span className="shrink-0 text-xs text-muted-foreground">{t("원본 {count}개", { count: group.count })}</span>
    </button>
    {open && <div id={id} className="space-y-4 border-t p-4">
      <Notice error>{resource.error}</Notice>
      {resource.loading ? <Loading /> : resource.data && <>
        <ul className="space-y-4">{resource.data.items.map(file => <li key={file.id} className="space-y-2 border-l-2 border-border pl-3" data-catalog-original={file.id}>
          <div className="flex min-w-0 items-center gap-2"><button type="button" className="min-w-0 flex-1 break-all text-left text-sm font-medium text-primary hover:underline" onClick={() => select(file)}>{file.name}</button><CopyFilePath file={file} /></div>
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">{file.quality_group && <Badge variant="outline">{file.quality_group}</Badge>}<span>{t("Read 수")} <SequenceCount file={file} metric="reads" /></span><span>{t("Bases 수")} <SequenceCount file={file} metric="bases" /></span><span className="text-xs text-muted-foreground">Q30 <QualityRate file={file} /></span></div>
          <p className="break-all font-mono text-xs text-muted-foreground">{file.path}</p>
        </li>)}</ul>
        {!resource.data.items.length && <Empty>{t("조건에 맞는 파일이 없습니다.")}</Empty>}
        <Pager page={page} total={resource.data.total} size={25} busy={resource.loading} onChange={setPage} />
      </>}
      {!!resource.error && <Button variant="outline" onClick={resource.refresh}>{t("다시 시도")}</Button>}
    </div>}
  </div>
}

function FileDetails({ file, close }: { file: CatalogFile | null; close(): void }) {
  const [copied, setCopied] = useState(false), [copyError, setCopyError] = useState(false)
  useEffect(() => { setCopied(false); setCopyError(false) }, [file?.id])
  return <Sheet open={!!file} onOpenChange={open => { if (!open) close() }}><SheetContent side="right" className="w-full sm:max-w-xl">
    <SheetHeader><SheetTitle className="pr-6 break-all">{file?.name || t("파일 정보")}</SheetTitle></SheetHeader>
    {file && <div className="space-y-7 px-5 pb-8 text-sm">
      <div className="flex flex-wrap items-center gap-2"><Evidence status={file.match_status} /><Badge variant="secondary">{fileSize(file.bytes)}</Badge>{file.read_part && <Badge variant="outline">{file.read_part}</Badge>}{file.quality_group && <Badge variant="outline">{file.quality_group}</Badge>}</div>
      <section className="space-y-3"><h3 className="font-semibold">{t("파일 경로")}</h3><p className="break-all rounded-lg border bg-muted/60 p-3 font-mono text-xs leading-6 [overflow-wrap:anywhere]">{file.path}</p><Button variant="outline" onClick={async () => { try { await navigator.clipboard.writeText(file.path); setCopied(true); setCopyError(false) } catch { setCopyError(true) } }}><Copy className="size-4" />{t(copied ? "복사됨" : "경로 복사")}</Button>{copyError && <Notice error>{t("텍스트를 선택해 복사하세요")}</Notice>}</section>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-5 gap-y-3">
        <dt className="text-muted-foreground">{t("Read 수")}</dt><dd><SequenceCount file={file} metric="reads" /></dd>
        <dt className="text-muted-foreground">{t("Bases 수")}</dt><dd><SequenceCount file={file} metric="bases" /></dd>
        <dt className="text-muted-foreground">Q30</dt><dd><QualityRate file={file} /></dd>
        {file.stats?.computed_at && <><dt className="text-muted-foreground">{t("통계 계산일")}</dt><dd>{new Date(file.stats.computed_at).toLocaleString(localeTag())}</dd></>}
        <dt className="text-muted-foreground">{t("샘플 연결")}</dt><dd>{t(file.match_status === "confirmed" ? "확인한 이름·경로를 기준으로 연결했습니다." : "파일명과 폴더명으로 추정한 연결입니다.")}</dd>
        <dt className="text-muted-foreground">{t("파일 역할")}</dt><dd>{file.roles.includes("representative") ? t("대표 파일") : file.representative_candidate ? t("대표 후보") : file.roles.includes("merged") ? t("병합 후보") : file.roles.includes("source") ? t("원본 후보") : t("확인 예정")}</dd>
        {file.older_version && <><dt className="text-muted-foreground">{t("버전")}</dt><dd>{t("이전 버전 폴더")}</dd></>}
        {file.tissue && <><dt className="text-muted-foreground">{t("조직")}</dt><dd>{t(({ flower: "꽃", haustoria: "흡기", seedling: "유묘", stem: "줄기", leaf: "잎", root: "뿌리", "stem+root+flower": "줄기·뿌리·꽃 혼합" } as Record<string, string>)[file.tissue] || file.tissue)}</dd></>}
        {file.replicate && <><dt className="text-muted-foreground">{t("반복 번호")}</dt><dd>{file.replicate}<p className="mt-1 text-xs text-muted-foreground">{t("파일명에 기록된 번호이며 생물학적 반복 여부는 확인이 필요합니다.")}</p></dd></>}
      </dl>
      <section className="space-y-3"><h3 className="flex items-center gap-2 font-semibold"><GitMerge className="size-4" />{t("병합에 사용된 파일")}</h3>{file.inputs.length ? <><p className="text-xs text-muted-foreground">{t("확인 상태는 연결 관계를 뜻합니다. 파일 내용의 동일성을 검증한 결과는 아닙니다.")}</p><ul className="space-y-3">{file.inputs.map(input => <li key={input.path} className="space-y-2 rounded-lg border p-3"><Evidence status={input.status} /><p className="break-all font-mono text-xs [overflow-wrap:anywhere]">{input.path}</p></li>)}</ul></> : <p className="text-muted-foreground">{t("등록된 병합 입력 정보가 없습니다.")}</p>}</section>
      <section className="space-y-3"><h3 className="font-semibold">{t("업체 보고서")}</h3>{file.reports.length ? file.reports.map(report => <div key={report.id} className="rounded-lg border p-3"><p className="font-medium">{report.provider} · {report.id}</p><p className="mt-2 text-xs text-muted-foreground">{t("보고서 날짜")} {report.report_date || "—"}</p><p className="text-xs text-muted-foreground">{t("실제 수령일")} {report.received_date || t("미확인")}</p></div>) : <p className="text-muted-foreground">{t("연결된 보고서가 없습니다.")}</p>}</section>
    </div>}
  </SheetContent></Sheet>
}

type FileSort = "file" | "sample" | "species" | "project" | "data_type" | "reads" | "bases" | "q30"

function FileBrowser({ data, projectId, speciesId, sampleId, dataset }: { data: CatalogOverview; projectId?: string; speciesId?: string; sampleId?: string; dataset?: CatalogDataset }) {
  const [search, setSearch] = useState(""), [query, setQuery] = useState(""), [role, setRole] = useState("all"), [page, setPage] = useState(1)
  const [sort, setSort] = useState<{ by: FileSort | "default"; order: "asc" | "desc" }>({ by: "default", order: "asc" })
  const sortFocus = useRef<FileSort | null>(null)
  const [selected, setSelected] = useState<CatalogFile | null>(null)
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set())
  useEffect(() => setExpandedFiles(new Set()), [query, role, page, sort])
  useEffect(() => { const timer = setTimeout(() => { setQuery(search); setPage(1) }, 250); return () => clearTimeout(timer) }, [search])
  const params = new URLSearchParams({ project: projectId || "", species: speciesId || "", sample: sampleId || "", dataset: dataset?.id || "", q: query, role, grouped: "true", view: "library", page: String(page), page_size: "25", sort_by: sort.by, sort_order: sort.order })
  const resource = useResource<Page<CatalogFile> & { original_groups?: CatalogOriginalGroup[] }>(`/catalog/files?${params}`, false, true)
  const samples = new Map(data.samples.map(sample => [sample.id, sample]))
  const projects = new Map(data.projects.map(project => [project.id, project]))
  const linkClass = "text-left font-medium underline-offset-4 hover:text-primary hover:underline focus-visible:outline-2 focus-visible:outline-ring"
  const sortButton = (by: FileSort, label: string) => {
    const active = sort.by === by, numeric = ["reads", "bases", "q30"].includes(by)
    const next = active ? (sort.order === "asc" ? "desc" : "asc") : numeric ? "desc" : "asc"
    const Icon = active ? (sort.order === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown
    return <button type="button" data-sort={by} className={cn("inline-flex min-h-6 items-center gap-1 rounded text-left whitespace-nowrap focus-visible:outline-2 focus-visible:outline-ring", active ? "text-primary" : "hover:text-primary")}
      aria-label={t(next === "asc" ? "{column}: 오름차순 정렬" : "{column}: 내림차순 정렬", { column: label })}
      ref={element => { if (element && sortFocus.current === by) { element.focus({ preventScroll: true }); sortFocus.current = null } }}
      onClick={() => { sortFocus.current = by; setSort({ by, order: next }); setPage(1) }}>
      {label}<Icon aria-hidden="true" className="size-3 shrink-0" />
    </button>
  }
  const columns: { by: FileSort; label: string }[] = [{ by: "file", label: t("파일") }, { by: "sample", label: t("샘플") }, { by: "project", label: t("프로젝트") }, { by: "data_type", label: t("데이터 종류") }, { by: "reads", label: t("Read 수") }, { by: "bases", label: t("Bases 수") }, { by: "q30", label: "Q30" }]
  return <div className="space-y-5">
    {dataset?.pending_scope && <p className="rounded-lg border bg-muted/50 px-4 py-3 text-sm text-muted-foreground">{t("이 데이터는 프로젝트 포함 여부를 확인 중입니다.")}</p>}
    <Panel title={t("파일 목록")} action={resource.data && <Badge variant="secondary">{t("{count}개 파일", { count: resource.data.total })}</Badge>}>
      <div className="space-y-5"><div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_190px]"><div className="relative"><Search className="pointer-events-none absolute left-3 top-3.5 size-4 text-muted-foreground" /><Input aria-label={t("파일 검색")} placeholder={t("파일·샘플·종·프로젝트 검색")} maxLength={200} value={search} onChange={event => setSearch(event.target.value)} className="pl-9" /></div><NativeSelect aria-label={t("파일 역할 필터")} value={role} onChange={event => { setRole(event.target.value); setPage(1) }}><option value="all">{t("모든 파일")}</option><option value="merged">{t("통합 파일·후보")}</option><option value="representative">{t("대표 파일")}</option></NativeSelect></div>
      <p className="text-xs text-muted-foreground">{t("통합에 사용된 조각은 통합본 아래에, 별도 보관 원본은 아래 보관함에 접어 둡니다.")}</p><Notice error>{resource.error}</Notice>
      {resource.loading ? <Loading /> : resource.data && <>
      {resource.data.items.length ? <div className="min-w-0 rounded-lg border"><Table className="catalog-table"><TableHeader><TableRow className="bg-muted/60">{columns.map(({ by, label }, i) => <TableHead key={by} scope="col" className={i >= 4 ? "text-right" : undefined} aria-sort={sort.by === by || by === "sample" && sort.by === "species" ? (sort.order === "asc" ? "ascending" : "descending") : undefined}>
        {sortButton(by, label)}{by === "sample" && <> / {sortButton("species", t("종"))}</>}
      </TableHead>)}</TableRow></TableHeader><TableBody>{resource.data.items.map(file => {
        const sample = samples.get(file.sample_id)!, project = projects.get(sample.project_id)!, group = sample.datasets.find(d => d.id === file.dataset_id)!
        const cells = [
        <div className="min-w-52 max-w-72 space-y-0.5 whitespace-normal"><div className="flex items-center gap-2"><button className={cn(linkClass, "min-w-0 flex-1 line-clamp-2 break-all text-sm leading-5 text-primary")} title={file.name} onClick={() => setSelected(file)}>{file.name}</button><CopyFilePath key={file.id} file={file} /></div><div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">{(file.read_part || file.tissue || file.replicate || file.quality_group) && <span className="text-xs leading-5 text-muted-foreground">{[file.read_part, file.tissue && t(({ flower: "꽃", haustoria: "흡기", seedling: "유묘", stem: "줄기", leaf: "잎", root: "뿌리", "stem+root+flower": "줄기·뿌리·꽃 혼합" } as Record<string, string>)[file.tissue] || file.tissue), file.replicate && t("반복 {number}", { number: file.replicate }), file.quality_group].filter(Boolean).join(" · ")}</span>}{!!file.inputs.length && <button type="button" className="flex min-h-6 items-center gap-1.5 rounded text-left text-xs text-primary focus-visible:outline-2 focus-visible:outline-ring" aria-expanded={expandedFiles.has(file.id)} aria-controls={`inputs-${file.id}`} onClick={() => setExpandedFiles(previous => { const next = new Set(previous); if (next.has(file.id)) next.delete(file.id); else next.add(file.id); return next })}><ChevronRight aria-hidden="true" className={cn("size-3 shrink-0 transition-transform", expandedFiles.has(file.id) && "rotate-90")} />{t("분할 파일 {count}개", { count: file.inputs.length })}</button>}</div></div>,
        <div className="min-w-32 max-w-44 space-y-0.5 whitespace-normal leading-5"><a href={sampleHref(sample)} className={cn(linkClass, "break-words")}>{sample.name}</a><a href={catalogHref(project.id, sample.species_id)} className="block text-xs italic text-muted-foreground hover:text-primary hover:underline">{sample.species}</a></div>,
        <a href={catalogHref(project.id)} className={cn(linkClass, "block max-w-32 whitespace-normal text-xs text-muted-foreground")}>{project.name}</a>,
        <div className="max-w-36 space-y-1 whitespace-normal"><a href={catalogHref(project.id, sample.species_id, sample.id, group.id)} className={cn(linkClass, "text-xs")}><DatasetName name={group.name} /></a>{group.pending_scope && <p className="text-xs text-muted-foreground">{t("프로젝트 포함 여부 확인 중")}</p>}</div>,
        <SequenceCount file={file} metric="reads" />, <SequenceCount file={file} metric="bases" />, <QualityRate file={file} />,
        ]
        return <Fragment key={file.id}><TableRow data-catalog-file={file.id}>{cells.map((cell, i) => <TableCell key={i} className={i >= 4 ? "text-right" : undefined}>{cell}</TableCell>)}</TableRow>{expandedFiles.has(file.id) && <TableRow data-catalog-inputs={file.id} className="bg-muted/30"><TableCell colSpan={7} className="max-w-0 whitespace-normal"><div id={`inputs-${file.id}`} className="space-y-4 p-3"><p className="text-xs text-muted-foreground">{t("확인 상태는 통합 파일과 분할 파일의 연결 관계를 뜻합니다.")}</p><SplitFiles inputs={file.inputs} select={setSelected} /></div></TableCell></TableRow>}</Fragment>
      })}</TableBody></Table></div> : <Empty>{t(dataset?.file_count === 0 ? "파일 연결 예정" : role === "merged" && !query ? "등록된 통합 파일이 없습니다. ‘모든 파일’에서 개별 파일을 확인하세요." : "조건에 맞는 파일이 없습니다.")}</Empty>}
      <Pager page={page} total={resource.data.total} size={25} busy={resource.loading} onChange={setPage} />
      {!!resource.data.original_groups?.length && <section className="space-y-3 border-t pt-5" aria-label={t("원본 보관함")}>
        <h3 className="text-sm font-semibold">{t("원본 보관함")}</h3><p className="text-xs leading-6 text-muted-foreground">{t("FAIL·별도 보관 파일입니다. 통합본의 입력 파일을 뜻하지 않으며, 원본 파일은 그대로 보존됩니다.")}</p>
        {resource.data.original_groups.map(group => <OriginalGroup key={`${group.sample_id}-${group.dataset_id}-${query}-${role}`} group={group} sample={samples.get(group.sample_id)!} query={query} role={role} select={setSelected} />)}
      </section>}</>}
      {!!resource.error && <Button variant="outline" onClick={resource.refresh}>{t("다시 시도")}</Button>}
      </div>
    </Panel><FileDetails file={selected} close={() => setSelected(null)} />
  </div>
}

function CatalogContent({ data }: { data: CatalogOverview }) {
  const hash = useHash(), availabilityView = hash === "data/availability" || hash.startsWith("data/availability/")
  const segments = hash.split("/").slice(availabilityView ? 2 : 1)
  const scopeHref = (...ids: (string | undefined)[]) => availabilityView ? catalogHref(...ids).replace("#data", "#data/availability") : catalogHref(...ids)
  const [projectId, speciesId, sampleId, datasetId] = segments
  const project = data.projects.find(p => p.id === projectId)
  const species = project?.species.find(s => s.id === speciesId)
  const sample = data.samples.find(s => s.id === sampleId && s.project_id === projectId && s.species_id === speciesId)
  const dataset = sample?.datasets.find(d => d.id === datasetId)
  const invalid = segments.length > 4 || !!projectId && !project || !!speciesId && !species || !!sampleId && !sample || !!datasetId && !dataset
  const [mobile, setMobile] = useState(false)
  const [expanded, setExpanded] = useState(() => new Set([projectId, speciesId, sampleId].filter(Boolean)))
  const [librarySearch, setLibrarySearch] = useState("")
  const [searchCollapsed, setSearchCollapsed] = useState<Set<string>>(new Set())
  const normalize = (value: string) => value.normalize("NFKC").toLowerCase()
  const terms = normalize(librarySearch).trim().split(/\s+/).filter(Boolean)
  const matches = (...values: string[]) => terms.every(term => normalize(values.join(" ")).includes(term))
  const libraryProjects = data.projects.map(p => ({ ...p, species: p.species.map(sp => ({ ...sp,
    samples: data.samples.filter(s => s.project_id === p.id && s.species_id === sp.id && matches(p.name, p.id, sp.name, sp.id, s.name, s.id)),
  })).filter(sp => sp.samples.length || matches(p.name, p.id, sp.name, sp.id)) })).filter(p => p.species.length || matches(p.name, p.id))
  const isExpanded = (id: string) => terms.length ? !searchCollapsed.has(id) : expanded.has(id)
  const updateLibrarySearch = (value: string) => { setLibrarySearch(value); setSearchCollapsed(new Set()) }
  const treeNavigation = useRef<string | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    setMobile(false); heading.current?.focus({ preventScroll: true })
    if (treeNavigation.current !== hash) setExpanded(previous => new Set([...previous, ...[projectId, speciesId, sampleId].filter(Boolean)]))
    treeNavigation.current = null
  }, [hash])
  const toggleBranch = (event: MouseEvent<HTMLAnchorElement>, id: string) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return
    treeNavigation.current = event.currentTarget.hash === location.hash ? null : event.currentTarget.hash.slice(1)
    const update = terms.length ? setSearchCollapsed : setExpanded
    update(previous => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next })
  }
  const breadcrumbs = [{ label: t(availabilityView ? "데이터 보유 현황" : "데이터 관리"), href: scopeHref() }, ...(project ? [{ label: project.name, href: scopeHref(project.id) }] : []), ...(species ? [{ label: species.name, href: scopeHref(projectId, species.id) }] : []), ...(sample ? [{ label: sample.name, href: scopeHref(projectId, speciesId, sampleId) }] : [])]
  const title = dataset ? dataset.name === "Unclassified" ? t("분류 미확정") : dataset.name : sample?.name || species?.name || project?.name || t(availabilityView ? "데이터 보유 현황" : "데이터 관리")
  const nav = <nav aria-label={t("프로젝트 탐색")} className="space-y-2">
    <div className="relative mb-4"><Search aria-hidden="true" className="pointer-events-none absolute left-3 top-3.5 size-4 text-muted-foreground" /><Input type="search" aria-label={t("Data library 검색")} placeholder={t("종·품종·개체 검색")} maxLength={200} value={librarySearch} onChange={event => updateLibrarySearch(event.target.value)} className="pl-9 pr-10 text-xs [&::-webkit-search-cancel-button]:hidden" />{librarySearch && <Button type="button" variant="ghost" size="icon" className="absolute right-0 top-0" aria-label={t("목록 검색 지우기")} onClick={() => updateLibrarySearch("")}><X aria-hidden="true" /></Button>}</div>
    <a href="#data" aria-current={!availabilityView && !projectId ? "page" : undefined} className={cn("flex min-h-11 items-center gap-2 rounded-lg px-3 text-sm", !availabilityView && !projectId && "bg-accent font-semibold text-primary")}><Database className="size-4" />{t("전체 데이터")}</a>
    <a href="#data/availability" aria-current={availabilityView && !projectId ? "page" : undefined} className={cn("mb-4 flex min-h-11 items-center gap-2 rounded-lg px-3 py-2 text-sm", availabilityView ? "bg-accent font-semibold text-primary" : "hover:bg-muted")}><Grid2X2 className="size-4 shrink-0" />{t("데이터 보유 현황")}</a>
    {!libraryProjects.length && <p role="status" className="px-3 py-2 text-xs leading-5 text-muted-foreground">{t("일치하는 항목이 없습니다.")}</p>}
    {libraryProjects.map(p => <div key={p.id}>
      <a href={scopeHref(p.id)} onClick={event => toggleBranch(event, p.id)} aria-expanded={isExpanded(p.id)} className={cn("flex min-h-11 items-center gap-2 rounded-lg px-3 py-2 text-sm", p.id === projectId ? "bg-accent font-semibold text-primary" : "text-muted-foreground hover:bg-muted")} aria-current={p.id === projectId && !speciesId ? "page" : undefined}><FolderOpen className="size-4 shrink-0" /><span className="min-w-0 flex-1">{p.name}</span><ChevronRight aria-hidden="true" className={cn("size-3 shrink-0 transition-transform", isExpanded(p.id) && "rotate-90")} /></a>
      {isExpanded(p.id) && <div className="ml-4 mt-2 space-y-1 border-l pl-3">{p.species.map(sp => <div key={sp.id}>
        <a href={scopeHref(p.id, sp.id)} onClick={event => toggleBranch(event, sp.id)} aria-expanded={isExpanded(sp.id)} aria-current={sp.id === speciesId && !sampleId ? "page" : undefined} className={cn("flex items-center gap-2 rounded-md px-2 py-2 text-xs italic leading-5 hover:bg-muted", sp.id === speciesId && "font-semibold text-primary")}><span className="min-w-0 flex-1">{sp.name}</span><ChevronRight aria-hidden="true" className={cn("size-3 shrink-0 transition-transform", isExpanded(sp.id) && "rotate-90")} /></a>
        {isExpanded(sp.id) && <div className="ml-2 space-y-1 border-l pl-2">{sp.samples.map(s => <div key={s.id}>
          <a href={scopeHref(p.id, sp.id, s.id)} onClick={event => toggleBranch(event, s.id)} aria-expanded={s.datasets.length ? isExpanded(s.id) : undefined} aria-current={s.id === sampleId && !datasetId ? "page" : undefined} className={cn("flex items-center gap-2 rounded-md px-2 py-2 text-xs leading-5 hover:bg-muted", s.id === sampleId ? "bg-accent font-semibold text-primary" : "text-muted-foreground")}><span className="min-w-0 flex-1 break-words">{s.name}</span>{!!s.datasets.length && <ChevronRight aria-hidden="true" className={cn("size-3 shrink-0 transition-transform", isExpanded(s.id) && "rotate-90")} />}</a>
          {isExpanded(s.id) && <div className="ml-2 space-y-1 border-l pl-2">{s.datasets.map(d => <a key={d.id} href={catalogHref(p.id, sp.id, s.id, d.id)} aria-current={d.id === datasetId ? "page" : undefined} className={cn("block rounded-md px-2 py-2 text-xs leading-5 hover:bg-muted", d.id === datasetId ? "bg-accent font-semibold text-primary" : "text-muted-foreground")}><DatasetName name={d.name} /></a>)}</div>}
        </div>)}</div>}
      </div>)}</div>}
    </div>)}
  </nav>
  return <div id="portal" className="mx-auto grid max-w-[1540px] gap-6 px-4 py-6 md:grid-cols-[230px_minmax(0,1fr)] md:px-8 md:py-8 lg:gap-9">
    <aside className="sticky top-6 hidden max-h-[calc(100dvh-48px)] overflow-y-auto self-start md:block"><p className="mb-5 px-3 text-[10px] font-semibold tracking-[.2em] text-muted-foreground">DATA LIBRARY</p>{nav}</aside>
    <main className="min-w-0 space-y-6"><div className="flex items-start gap-3"><Button variant="outline" size="icon" aria-label={t("메뉴 열기")} className="shrink-0 md:hidden" onClick={() => setMobile(true)}><Menu /></Button><nav aria-label={t("현재 위치")} className="flex min-h-10 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">{breadcrumbs.map((crumb, i) => <span key={crumb.href} className="inline-flex min-w-0 items-center gap-2">{i > 0 && <ChevronRight className="size-3 shrink-0" />}<a href={crumb.href} className="break-words hover:text-primary">{crumb.label}</a></span>)}{dataset && <span className="inline-flex items-center gap-2"><ChevronRight className="size-3" /><DatasetName name={dataset.name} /></span>}</nav></div>
      <Sheet open={mobile} onOpenChange={setMobile}><SheetContent side="left" className="w-[min(320px,88vw)]"><SheetHeader><SheetTitle>{t("프로젝트 탐색")}</SheetTitle></SheetHeader><div className="px-4 pb-6">{nav}</div></SheetContent></Sheet>
      <div className="flex flex-wrap items-start justify-between gap-4"><div className="min-w-0"><p className="mb-2 text-[10px] font-semibold uppercase tracking-[.2em] text-primary">SEQUENCING LIBRARY</p><h1 ref={heading} tabIndex={-1} className={cn("text-2xl font-semibold tracking-tight outline-none sm:text-3xl", !!species && !sample && "italic")}>{title}</h1><p className="mt-3 text-sm text-muted-foreground">{t(availabilityView ? "프로젝트를 선택해 샘플별 데이터 보유 현황을 확인하세요." : "전체 파일을 한눈에 확인하고, 왼쪽 목록에서 범위를 좁혀보세요.")}</p></div>{project && !invalid && <Button asChild variant="outline"><a href={scopeHref()}><ArrowLeft />{t(availabilityView ? "프로젝트 선택" : "전체 데이터")}</a></Button>}</div>
      {invalid ? <><Notice error>{t("해당 프로젝트 또는 샘플을 찾을 수 없습니다.")}</Notice><Button asChild variant="outline"><a href="#data"><ArrowLeft />{t("전체 데이터")}</a></Button></>
      : availabilityView ? <CatalogAvailability key={hash} data={data} projectId={projectId} speciesId={speciesId} sampleId={sampleId} dataset={dataset} />
      : <FileBrowser key={hash} data={data} projectId={projectId} speciesId={speciesId} sampleId={sampleId} dataset={dataset} />}
      {!availabilityView && !project && data.unassigned_files > 0 && <p className="text-xs leading-6 text-muted-foreground">{t("샘플 연결이 미확정인 파일 {count}개는 이 목록에서 제외되어 있습니다.", { count: data.unassigned_files })}</p>}
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t pt-5 text-xs leading-6 text-muted-foreground"><span>{t("확인된 정보와 파일명으로 추정한 정보를 구분해 표시합니다.")}</span>{data.updated_at && <span>{t("목록 기준일")} {data.updated_at.slice(0, 10)}</span>}</footer>
    </main>
  </div>
}

export function DataPage({ allowed }: { allowed: boolean }) {
  const resource = useResource<CatalogOverview>(allowed ? "/catalog" : null, false, true)
  if (!allowed) return <main id="portal" className="mx-auto max-w-3xl px-4 py-10"><Panel title={t("데이터 관리")}><Empty>{t("데이터 관리 열람 권한이 필요합니다.")}</Empty></Panel></main>
  if (resource.data?.projects.length) return <CatalogContent data={resource.data} />
  return <main id="portal" className="mx-auto max-w-3xl space-y-5 px-4 py-10"><h1 className="text-2xl font-semibold">{t("데이터 관리")}</h1><Notice error>{resource.error}</Notice>{resource.loading ? <Loading /> : resource.error ? <Button onClick={resource.refresh} variant="outline">{t("다시 시도")}</Button> : <Empty>{t("아직 등록된 프로젝트가 없습니다.")}</Empty>}</main>
}
