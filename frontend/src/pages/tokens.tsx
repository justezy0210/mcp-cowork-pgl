import { t } from "@/lib/i18n"
import { useEffect, useRef, useState } from "react"
import { KeyRound } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { ConfirmDialog, Empty, Field, Loading, Notice, Panel, Submit } from "@/components/shared"
import { TokenDelivery } from "@/components/token-delivery"
import { api, post } from "@/lib/session"
import { useMutation } from "@/lib/hooks"
import { date } from "@/lib/format"
import type { IssuedToken, Profile, Token } from "@/lib/types"

export function TokensPage({ profile, pendingToken, onToken }: { profile: Profile; pendingToken: IssuedToken | null; onToken(token: IssuedToken | null): void }) {
  const [tokens, setTokens] = useState<Token[]>([]), [loading, setLoading] = useState(true), [error, setError] = useState("")
  const [more, setMore] = useState(false), [name, setName] = useState("")
  const [revoking, setRevoking] = useState<Token | null>(null)
  const issue = useMutation(), revoke = useMutation(), serial = useRef(0), alive = useRef(true), listLock = useRef(false)
  const load = async (append = false) => {
    if (listLock.current && append) return
    const revision = ++serial.current; listLock.current = true; setLoading(true); setError("")
    try {
      const rows = await api<Token[]>("/tokens?limit=100" + (append && tokens.length ? "&after=" + encodeURIComponent(tokens[tokens.length - 1].id) : ""))
      if (alive.current && revision === serial.current) { setTokens(previous => append ? [...previous, ...rows] : rows); setMore(rows.length === 100) }
    } catch (failure) { if (alive.current && revision === serial.current) { setTokens([]); setError(failure instanceof Error ? failure.message : "목록을 불러오지 못했습니다.") } }
    finally { if (revision === serial.current) { listLock.current = false; if (alive.current) setLoading(false) } }
  }
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; serial.current++ } }, [])
  return <div className="page-stack"><Notice error>{error || issue.error}</Notice>
    <Panel title={t("새 토큰 만들기")} description={t("사용할 곳을 이름으로 적어 두면 연결을 구분하기 쉽습니다.")}><form id="create-form" className="form-stack" onSubmit={event => {
      event.preventDefault(); if (pendingToken) return
      void issue.run(async () => { const token = await post<IssuedToken>("/tokens", { name: name.trim() || `${profile.user_id}-MCP` }); if (alive.current) { onToken(token); await load() } })
    }}><Field id="token-name" label={t("토큰 이름 (선택)")} help={t("비워 두면 예시 이름으로 발급합니다.")}><Input id="token-name" value={name} onChange={e => setName(e.target.value)} maxLength={200} placeholder={t("예: {v0}-MCP", { v0: profile.user_id })} aria-describedby="token-name-help" /></Field><div className="action-row"><Submit id="create" pending={issue.pending} disabled={!!pendingToken}>{t("토큰 발급")}</Submit></div><p className="text-xs text-muted-foreground">{t("발급 직후에만 복사하거나 다운로드할 수 있습니다. 기존 토큰은 계속 유지됩니다.")}</p></form></Panel>
    {pendingToken && <TokenDelivery key={pendingToken.id} token={pendingToken} onSaved={() => { onToken(null); setName("") }} />}
    <Panel title={t("발급한 토큰")} action={<Button id="refresh" variant="outline" onClick={() => load()} disabled={loading}>{t("새로고침")}</Button>}><div className="space-y-4">{loading && !tokens.length ? <Loading /> : !tokens.length ? <Empty>{t("아직 발급한 토큰이 없습니다.")}</Empty> : <ul id="token-list" className="divide-y">{tokens.map(token => <li key={token.id} className="flex flex-wrap items-center justify-between gap-4 py-5 first:pt-0"><div className="min-w-0 flex-1 basis-48"><p className="flex items-start gap-2 font-medium"><KeyRound className="mt-1 size-4 shrink-0 text-muted-foreground" /><span className="break-all">{token.name}</span></p><div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><span>{date(token.created_at)} {t("발급")}</span><Badge variant={token.revoked_at == null ? "secondary" : "outline"}>{token.revoked_at == null ? t("사용 가능") : t("폐기됨")}</Badge></div></div>{token.revoked_at == null && <Button variant="outline" onClick={() => { revoke.clearError(); setRevoking(token) }} aria-label={t("{v0} 토큰 폐기", { v0: token.name })}>{t("폐기")}</Button>}</li>)}</ul>}{more && <Button variant="outline" onClick={() => load(true)} disabled={loading}>{t("더 보기")}</Button>}<p className="text-xs text-muted-foreground">{t("이 화면에서 발급한 토큰을 관리합니다. 관리자가 처음 전달한 토큰은 이 목록에 표시되지 않습니다.")}</p></div></Panel>
    <ConfirmDialog open={!!revoking} onOpenChange={open => { if (!open) setRevoking(null) }} title={t("이 토큰을 폐기할까요?")} description={t("{v0}을 사용하는 프로그램의 허브 접속이 끊깁니다. 사용 중이라면 새 토큰으로 교체하세요. 기존 작업을 취소하거나 예약을 반환하지는 않습니다.", { v0: revoking?.name || "" })} destructive confirmLabel={t("토큰 폐기")} pending={revoke.pending} error={revoke.error} onConfirm={() => { if (!revoking) return; const token = revoking; void revoke.run(async () => { await api(`/tokens/${encodeURIComponent(token.id)}`, { method: "DELETE" }); if (pendingToken?.id === token.id) onToken(null); setRevoking(null); await load() }) }} />
  </div>
}
