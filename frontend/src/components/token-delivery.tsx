import { t } from "@/lib/i18n"
import { useState } from "react"
import { Copy, Download } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Notice, Panel } from "@/components/shared"
import type { IssuedToken } from "@/lib/types"

export function TokenDelivery({ token, onSaved }: { token: IssuedToken; onSaved(): void }) {
  const [copied, setCopied] = useState(false), [error, setError] = useState("")
  return <Panel title={t("개인 토큰이 준비됐습니다")} className="border-primary/30 bg-accent/40" description={t("{v0}에 사용할 토큰입니다.", { v0: token.name })}>
    <div id="download-panel" className="space-y-5">
      <Notice error>{error}</Notice>
      <div className="action-row">
        <Button id="copy-token" onClick={async () => {
          try { await navigator.clipboard.writeText(token.token); setCopied(true); setError("") }
          catch { setError("클립보드 권한을 허용하거나 user.token을 다운로드해 --token-file 옵션으로 지정하세요.") }
        }}><Copy />{copied ? t("복사됨") : t("토큰 복사")}</Button>
        <Button id="download" variant="outline" onClick={() => {
          const url = URL.createObjectURL(new Blob([token.token + "\n"], { type: "text/plain;charset=utf-8" }))
          const link = document.createElement("a"); link.href = url; link.download = "user.token"
          document.body.append(link); link.click(); link.remove()
          setTimeout(() => URL.revokeObjectURL(url), 1000)
        }}><Download />{t("user.token 다운로드")}</Button>
        <Button id="dismiss-download" variant="outline" onClick={onSaved}>{t("저장 완료")}</Button>
      </div>
      <p className="text-sm text-muted-foreground">{t("설치 프로그램이 요청할 때 복사한 토큰을 터미널에 붙여 넣으세요. 저장 완료 후에는 다시 받을 수 없습니다.")}</p>
    </div>
  </Panel>
}
