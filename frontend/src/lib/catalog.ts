export type CatalogAvailabilityGroup = {
  tissue: string; condition: string; condition_description: string
  replicate_count: number | null; replicate_status: "confirmed" | "inferred" | "unknown"
}
export type CatalogDataset = {
  id: string; name: string; file_count: number; representative_count: number; status: string; pending_scope: boolean
  availability?: { status: "present" | "needs_review"; groups: CatalogAvailabilityGroup[] }
}
export type CatalogSample = { id: string; name: string; project_id: string; species_id: string; species: string; identity_status: string; datasets: CatalogDataset[] }
export type CatalogProject = { id: string; name: string; species: { id: string; name: string }[] }
export type CatalogOverview = { version: number; updated_at: string | null; projects: CatalogProject[]; samples: CatalogSample[]; unassigned_files: number }
export type CatalogOriginalGroup = { sample_id: string; dataset_id: string; count: number }
export type CatalogFile = {
  id: string; sample_id: string; dataset_id: string; name: string; path: string; bytes: number
  roles: string[]; representative_candidate: boolean; match_status: string; read_part: string
  tissue: string; replicate: string; quality_group: string; older_version: boolean
  display_group?: "retained_original"
  stats?: { status: "completed" | "pending" | "failed" | "unavailable"; reads: number | null; bases: number | null; q30_percent?: number | null; computed_at: string | null }
  reports: { id: string; provider: string; report_date: string; received_date: string | null }[]
  inputs: { path: string; status: string; file?: CatalogFile | null }[]
}
export const catalogHref = (...ids: (string | undefined)[]) => "#data" + ids.filter(Boolean).map(id => "/" + encodeURIComponent(id!)).join("")
export function fileSize(bytes: number) {
  if (bytes < 1000) return `${bytes} B`
  const exponent = Math.min(4, Math.floor(Math.log10(bytes) / 3))
  return `${(bytes / 1000 ** exponent).toFixed(exponent > 1 ? 2 : 1)} ${["B", "KB", "MB", "GB", "TB"][exponent]}`
}
