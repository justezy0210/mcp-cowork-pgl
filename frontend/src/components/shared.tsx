import { t, localeTag } from "@/lib/i18n"
import { useId, useState, type ReactNode } from "react"
import { AlertCircle, Check, Copy, LoaderCircle, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle, AlertDialogDescription, AlertDialogFooter, AlertDialogCancel, AlertDialogAction } from "@/components/ui/alert-dialog"
import { states } from "@/lib/format"

export function Panel({ title, description, children, action, className = "" }: { title: string; description?: ReactNode; children: ReactNode; action?: ReactNode; className?: string }) {
  return <Card className={className}><CardHeader className="gap-3"><div className="flex flex-wrap items-center justify-between gap-3"><CardTitle className="text-lg">{title}</CardTitle>{action}</div>{description && <CardDescription className="leading-7">{description}</CardDescription>}</CardHeader><CardContent>{children}</CardContent></Card>
}
export function Field({ label, id, help, children }: { label: string; id: string; help?: ReactNode; children: ReactNode }) {
  return <div className="min-w-0 space-y-2"><Label htmlFor={id} className="leading-6">{label}</Label>{children}{help && <p id={id + "-help"} className="text-xs text-muted-foreground">{help}</p>}</div>
}
export function Notice({ children, error = false }: { children?: ReactNode; error?: boolean }) {
  if (!children) return null
  return <Alert variant={error ? "destructive" : "default"} className={error ? "" : "border-primary/20 bg-accent/60"} role={error ? "alert" : "status"}>{error ? <AlertCircle /> : <Check />}<AlertDescription className="break-words">{typeof children === "string" ? t(children) : children}</AlertDescription></Alert>
}
export function Loading() { return <div className="space-y-3" role="status" aria-label={t("불러오는 중")}><Skeleton className="h-5 w-40" /><Skeleton className="h-24 w-full" /></div> }
export function Empty({ children = t("표시할 내역이 없습니다.") }: { children?: ReactNode }) { return <p className="rounded-lg border border-dashed bg-muted/40 px-5 py-9 text-center text-sm text-muted-foreground">{children}</p> }
export function Status({ state }: { state: string }) {
  return <Badge variant={['FAILED','REJECTED','UNAVAILABLE'].includes(state) ? "destructive" : ['RUNNING','READY','SUCCEEDED','APPROVED'].includes(state) ? "default" : "secondary"} className="max-w-full whitespace-normal">{t(states[state] || state)}</Badge>
}
export function Refresh({ onClick, busy, updated }: { onClick(): void; busy: boolean; updated?: Date | null }) {
  return <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground"><span role="status">{updated ? t("마지막 확인 ") + updated.toLocaleTimeString(localeTag()) : busy ? t("최신 정보를 불러오는 중…") : ""}</span><Button type="button" variant="outline" onClick={onClick} disabled={busy}><RefreshCw className={busy ? "animate-spin" : ""} />{t("새로고침")}</Button></div>
}
export function Submit({ pending, children, disabled, id }: { pending: boolean; children: ReactNode; disabled?: boolean; id?: string }) {
  return <Button id={id} type="submit" disabled={pending || disabled}>{pending && <LoaderCircle className="animate-spin" />}{children}</Button>
}
export function Pager({ page, total, size = 20, onChange, busy = false }: { page: number; total: number; size?: number; onChange(page: number): void; busy?: boolean }) {
  const last = Math.max(1, Math.ceil(total / size))
  return <nav aria-label={t("목록 페이지")} className="mt-5 flex flex-wrap items-center justify-end gap-3"><span className="text-xs text-muted-foreground">{t("{total}건 · {page} / {last}", { total, page, last })}</span><Button variant="outline" onClick={() => onChange(page - 1)} disabled={busy || page <= 1}>{t("이전")}</Button><Button variant="outline" onClick={() => onChange(page + 1)} disabled={busy || page >= last}>{t("다음")}</Button></nav>
}
export function DataTable({ headers, rows, empty }: { headers: string[]; rows: ReactNode[][]; empty?: string }) {
  if (!rows.length) return <Empty>{empty}</Empty>
  return <div className="min-w-0 rounded-lg border"><Table><TableHeader><TableRow className="bg-muted/60">{headers.map(h => <TableHead key={h}>{h}</TableHead>)}</TableRow></TableHeader><TableBody>{rows.map((cells, i) => <TableRow key={i}>{cells.map((cell, j) => <TableCell key={j}>{cell ?? "—"}</TableCell>)}</TableRow>)}</TableBody></Table></div>
}
export function Detail({ children, note }: { children: ReactNode; note?: ReactNode }) { return <div className="space-y-1.5"><div>{children}</div>{note && <div className="text-xs leading-6 text-muted-foreground">{note}</div>}</div> }
export function ServerChoices({ nodes, value, onChange, label = t("허용 서버"), disabled = false }: { nodes: { id: string; enabled?: boolean }[]; value: string[]; onChange(value: string[]): void; label?: string; disabled?: boolean }) {
  const prefix = useId()
  return <fieldset className="min-w-0 space-y-3" disabled={disabled}><legend className="text-sm font-medium">{label}</legend><div className="flex flex-wrap gap-2.5">{nodes.map(node => <Label key={node.id} htmlFor={prefix + node.id} className="flex min-h-11 cursor-pointer gap-2.5 rounded-lg border bg-background px-3 py-2.5 has-[[data-state=checked]]:border-primary/50 has-[[data-state=checked]]:bg-accent has-[:disabled]:opacity-50"><Checkbox id={prefix + node.id} checked={value.includes(node.id)} disabled={disabled || node.enabled === false} onCheckedChange={checked => onChange(checked ? [...value, node.id] : value.filter(id => id !== node.id))} />{node.id}</Label>)}</div>{!nodes.length && <p className="text-sm text-muted-foreground">{t("등록된 서버가 없습니다.")}</p>}</fieldset>
}
export function ConfirmDialog({ open, onOpenChange, title, description, children, onConfirm, pending, error, destructive = false, confirmLabel = t("확인") }: { open: boolean; onOpenChange(open: boolean): void; title: string; description: ReactNode; children?: ReactNode; onConfirm(): void; pending: boolean; error?: string; destructive?: boolean; confirmLabel?: string }) {
  return <AlertDialog open={open} onOpenChange={value => { if (!pending) onOpenChange(value) }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{title}</AlertDialogTitle><AlertDialogDescription>{description}</AlertDialogDescription></AlertDialogHeader>{children}<Notice error>{error}</Notice><AlertDialogFooter className="gap-3"><AlertDialogCancel disabled={pending}>{t("돌아가기")}</AlertDialogCancel><AlertDialogAction className={destructive ? "bg-destructive hover:bg-destructive/90" : ""} disabled={pending} onClick={event => { event.preventDefault(); onConfirm() }}>{pending && <LoaderCircle className="animate-spin" />}{confirmLabel}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
}
export function Command({ text, id }: { text: string; id?: string }) {
  const [copied, setCopied] = useState("")
  return <div className="min-w-0 overflow-hidden rounded-lg border bg-muted/60"><div className="flex justify-end p-2"><Button type="button" size="sm" variant="outline" onClick={async () => { try { await navigator.clipboard.writeText(text); setCopied("복사됨") } catch { setCopied("텍스트를 선택해 복사하세요") } }}><Copy />{t(copied || "복사")}</Button></div><pre id={id} className="overflow-x-auto px-4 pb-4 font-mono text-xs leading-7"><code>{text}</code></pre></div>
}
