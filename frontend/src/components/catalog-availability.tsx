import { useState } from "react"
import { Check, CircleHelp, Minus, Search } from "lucide-react"
import { Empty } from "@/components/shared"
import { Input } from "@/components/ui/input"
import { NativeSelect } from "@/components/ui/native-select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { catalogHref, type CatalogAvailabilityGroup, type CatalogDataset, type CatalogOverview, type CatalogSample } from "@/lib/catalog"
import { t } from "@/lib/i18n"
import { cn } from "@/lib/utils"

const typeOrder = ["HiFi", "PacBio CLR", "ONT", "WGS short read", "Hi-C", "Omni-C", "10X linked-read", "Unclassified"]
const tissueOrder = ["stem", "leaf", "root", "flower", "haustoria", "seedling", "stem+root+flower", ""]
const unspecified: CatalogAvailabilityGroup = { tissue: "", condition: "", condition_description: "", replicate_count: null, replicate_status: "unknown" }
const groupKey = (group: CatalogAvailabilityGroup) => JSON.stringify([group.tissue, group.condition])
const groups = (dataset: CatalogDataset) => dataset.availability?.groups.length ? dataset.availability.groups : [unspecified]
const typeLabel = (name: string) => name === "Unclassified" ? t("분류 미확정") : name === "WGS short read" ? "WGS short reads" : name

function groupLabel(group: CatalogAvailabilityGroup) {
  const tissues: Record<string, string> = { stem: t("줄기"), leaf: t("잎"), root: t("뿌리"), flower: t("꽃"), haustoria: t("흡기"), seedling: t("유묘"), "stem+root+flower": t("줄기·뿌리·꽃 혼합") }
  const condition = group.condition_description === "floating individuals" ? t("떠 있는 개체")
    : group.condition_description === "sinking individuals" ? t("가라앉은 개체") : group.condition
  return [tissues[group.tissue] || group.tissue, condition].filter(Boolean).join(" · ") || t("조직·조건 미기록")
}

function AvailabilityCell({ sample, dataset, group, label }: {
  sample: CatalogSample; dataset?: CatalogDataset; group?: CatalogAvailabilityGroup; label: string
}) {
  if (!dataset) return <span className="mx-auto flex h-8 w-16 items-center justify-center rounded-md bg-muted/40 text-muted-foreground/60" title={t("미등록")}><Minus aria-hidden="true" className="size-4" /><span className="sr-only">{t("미등록")}</span></span>
  const present = dataset.availability ? dataset.availability.status === "present" : dataset.file_count > 0 && !dataset.pending_scope && dataset.name !== "Unclassified"
  const count = group?.replicate_count
  const counted = present && typeof count === "number" && Number.isSafeInteger(count) && count > 0
  const inferred = counted && group?.replicate_status !== "confirmed"
  const description = [t(present ? "보유" : "확인 필요"), counted ? t("반복 번호 {count}개", { count }) : "", inferred ? t("반복 번호는 파일명 기준입니다.") : "", present && dataset.status !== "confirmed" ? t("샘플·데이터 유형은 추정 정보입니다.") : "", t("파일 목록 보기")].filter(Boolean).join(" · ")
  return <a href={catalogHref(sample.project_id, sample.species_id, sample.id, dataset.id)}
    className={cn("mx-auto flex h-8 w-16 items-center justify-center gap-1 rounded-md border text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring", present ? "border-primary/20 bg-primary/15 text-primary hover:bg-primary/25" : "border-dashed border-muted-foreground/40 text-muted-foreground hover:bg-muted")}
    title={description} aria-label={`${sample.name} · ${label} · ${description}`}>
    {present ? <Check aria-hidden="true" className="size-4" /> : <CircleHelp aria-hidden="true" className="size-4" />}
    {counted && <span className="tabular-nums">×{count}{inferred && <sup>*</sup>}</span>}
  </a>
}

export function CatalogAvailability({ data, projectId, speciesId, sampleId, dataset }: {
  data: CatalogOverview; projectId?: string; speciesId?: string; sampleId?: string; dataset?: CatalogDataset
}) {
  const [query, setQuery] = useState("")
  const terms = query.normalize("NFKC").trim().toLowerCase().split(/\s+/).filter(Boolean)
  const scoped = data.samples.filter(s => s.project_id === projectId && (!speciesId || s.species_id === speciesId) && (!sampleId || s.id === sampleId))
  const projects = data.projects.filter(p => p.id === projectId && scoped.some(s => s.project_id === p.id))
  const matches = (s: CatalogSample) => terms.every(term => `${s.name} ${s.species}`.normalize("NFKC").toLowerCase().includes(term))
  return <div className="space-y-6" data-catalog-availability>
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex w-full min-w-0 flex-col gap-3 sm:w-auto sm:flex-row sm:flex-wrap sm:items-end">
        <div className="space-y-2 sm:w-60 [&_[data-slot=native-select-wrapper]]:w-full">
          <label htmlFor="availability-project" className="text-xs font-medium text-muted-foreground">{t("프로젝트")}</label>
          <NativeSelect id="availability-project" value={projectId || ""} className="h-11" onChange={event => { location.hash = catalogHref(event.target.value || undefined).replace("#data", "#data/availability") }}>
            <option value="">{t("프로젝트 선택")}</option>
            {data.projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
          </NativeSelect>
        </div>
        {projectId && <div className="relative w-full sm:w-72"><Search aria-hidden="true" className="pointer-events-none absolute left-3 top-3.5 size-4 text-muted-foreground" /><Input type="search" className="pl-9" value={query} onChange={event => setQuery(event.target.value)} maxLength={200} aria-label={t("현황에서 샘플 검색")} placeholder={t("종·품종·개체 검색")} /></div>}
      </div>
      {projectId && <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground" aria-label={t("보유 현황 범례")}>
        <span className="inline-flex items-center gap-1.5"><Check aria-hidden="true" className="size-4 text-primary" />{t("보유")}</span>
        <span className="inline-flex items-center gap-1.5"><Minus aria-hidden="true" className="size-4" />{t("미등록")}</span>
        <span className="inline-flex items-center gap-1.5"><CircleHelp aria-hidden="true" className="size-4" />{t("확인 필요")}</span>
      </div>}
    </div>
    {projects.map(project => {
      const projectSamples = scoped.filter(s => s.project_id === project.id)
      const visible = projectSamples.filter(matches)
      if (!visible.length) return null
      const datasets = projectSamples.flatMap(s => s.datasets).filter(d => !dataset || dataset.name === d.name)
      const names = [...new Set(datasets.map(d => d.name))].filter(name => name !== "RNA-seq").sort((a, b) => {
        const rank = (name: string) => typeOrder.includes(name) ? typeOrder.indexOf(name) : typeOrder.length
        return rank(a) - rank(b) || a.localeCompare(b)
      })
      const rnaGroups = [...new Map(datasets.filter(d => d.name === "RNA-seq").flatMap(groups).map(g => [groupKey(g), g])).values()].sort((a, b) => {
        const rank = (name: string) => tissueOrder.includes(name) ? tissueOrder.indexOf(name) : tissueOrder.length
        return rank(a.tissue) - rank(b.tissue) || a.tissue.localeCompare(b.tissue) || a.condition.localeCompare(b.condition)
      })
      return <section key={project.id} aria-label={project.name} className="overflow-hidden rounded-xl border bg-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-4 sm:px-5"><h2 className="text-base font-semibold">{project.name}</h2><span className="text-xs text-muted-foreground">{t("샘플 {count}개", { count: visible.length })}</span></div>
        {!names.length && !rnaGroups.length ? <Empty>{t("아직 등록된 데이터 유형이 없습니다.")}</Empty> : <Table className="catalog-table" aria-label={t("{project} 데이터 보유 현황", { project: project.name })}>
          <TableHeader className="bg-muted/40">
            <TableRow className="hover:bg-transparent"><TableHead scope="col" rowSpan={rnaGroups.length ? 2 : 1} className="sticky left-0 z-10 min-w-44 bg-card px-4 sm:min-w-56 sm:px-5">{t("샘플 / 종")}</TableHead>
              {names.map(name => <TableHead scope="col" key={name} rowSpan={rnaGroups.length ? 2 : 1} className="min-w-24 px-3 text-center text-xs">{typeLabel(name)}</TableHead>)}
              {!!rnaGroups.length && <TableHead scope="colgroup" colSpan={rnaGroups.length} className="border-l text-center text-xs">RNA-seq</TableHead>}
            </TableRow>
            {!!rnaGroups.length && <TableRow className="hover:bg-transparent">{rnaGroups.map((group, index) => <TableHead scope="col" key={groupKey(group)} className={cn("min-w-24 px-3 text-center text-xs", index === 0 && "border-l")}>{groupLabel(group)}</TableHead>)}</TableRow>}
          </TableHeader>
          <TableBody>{visible.map(sample => <TableRow key={sample.id} data-availability-sample={sample.id} className="hover:bg-transparent">
            <th scope="row" className="sticky left-0 z-10 bg-card px-4 py-3 text-left sm:px-5"><a href={catalogHref(sample.project_id, sample.species_id, sample.id)} className="block max-w-44 break-words text-sm font-medium leading-5 underline-offset-4 hover:text-primary hover:underline focus-visible:outline-ring">{sample.name}</a><span className="mt-0.5 block text-xs font-normal italic text-muted-foreground">{sample.species}</span></th>
            {names.map(name => <TableCell key={name} data-availability-type={name} className="px-3"><AvailabilityCell sample={sample} dataset={sample.datasets.find(d => d.name === name)} label={typeLabel(name)} /></TableCell>)}
            {rnaGroups.map((group, index) => {
              const rna = sample.datasets.find(d => d.name === "RNA-seq")
              const ownGroup = rna && groups(rna).find(g => groupKey(g) === groupKey(group))
              return <TableCell key={groupKey(group)} data-availability-tissue={group.tissue} data-availability-condition={group.condition} className={cn("px-3", index === 0 && "border-l")}><AvailabilityCell sample={sample} dataset={ownGroup ? rna : undefined} group={ownGroup} label={`RNA-seq · ${groupLabel(group)}`} /></TableCell>
            })}
          </TableRow>)}</TableBody>
        </Table>}
      </section>
    })}
    {!projectId ? <Empty>{t("프로젝트를 선택하면 데이터 보유 현황이 표시됩니다.")}</Empty> : !scoped.some(matches) && <Empty>{t("일치하는 항목이 없습니다.")}</Empty>}
    {projectId && <div className="space-y-1 text-xs leading-6 text-muted-foreground">
      <p>{t("등록된 파일 기준입니다. 미등록은 데이터가 없다고 확인된 상태가 아닙니다.")}</p>
      <p>{t("×N은 조직·조건별 반복 번호 수입니다. R1/R2와 분할·통합 파일은 중복 계산하지 않습니다.")}</p>
      <p>{t("* 파일명에서 확인한 반복 번호로, 생물학적 반복 여부는 확인이 필요합니다.")}</p>
    </div>}
  </div>
}
