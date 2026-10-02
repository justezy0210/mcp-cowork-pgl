import { t } from "@/lib/i18n"
import { Trans } from "@/components/trans"
import { useState } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Field, Loading, Notice, Panel, Submit } from "@/components/shared"
import { post, session, useSession } from "@/lib/session"
import { useMutation } from "@/lib/hooks"
import type { Enrollment } from "@/lib/types"

export function OnboardingPage() {
  const { enrollment, message } = useSession()
  const mutation = useMutation(), [webhook, setWebhook] = useState("")
  const pending = enrollment?.state === "PENDING" || enrollment?.state === "LINKED"
  return <div className="mx-auto max-w-2xl p-4 py-10 sm:p-8"><Panel title={t("내 계정 등록")} description={t("계정 정보를 한 번 등록하면 관리자가 확인하고 사용할 서버를 지정합니다.")}>
    <div className="space-y-6"><Notice error>{message || mutation.error}</Notice>
      {!enrollment ? <><Loading /><Button variant="outline" onClick={session.refreshProfile}>{t("등록 상태 다시 확인")}</Button></> : pending ? <>
        <Notice>{enrollment.state === "PENDING" ? t("계정 승인 대기 중입니다. 승인되면 아래 버튼으로 확인하세요.") : t("계정이 연결되어 있습니다. 접근할 수 없다면 관리자에게 계정 상태를 확인해 주세요.")}</Notice>
        <p className="text-sm text-muted-foreground">{enrollment.account_name} · UID/GID {enrollment.uid}:{enrollment.gid}</p>
        <Button id="onboarding-refresh" onClick={session.refreshProfile}>{t("승인 상태 확인")}</Button>
      </> : <form id="onboarding-form" className="form-stack" autoComplete="off" onSubmit={event => {
        event.preventDefault(); const data = new FormData(event.currentTarget)
        const body = { account_name: String(data.get("account_name")).trim(), uid: Number(data.get("uid")), gid: Number(data.get("gid")), webhook_url: webhook.trim() }
        setWebhook("")
        void mutation.run(async () => { const result = await post<Enrollment>("/onboarding", body); session.setEnrollment(result) })
      }}>
        {enrollment.state === "REJECTED" && <Notice error>{t("등록이 승인되지 않았습니다. 정보를 확인한 뒤 다시 제출하세요.")}</Notice>}
        <Field id="onboarding-name" label={t("서버에서 사용하는 유저 ID (필수)")}><Input id="onboarding-name" name="account_name" required maxLength={64} pattern="[A-Za-z][A-Za-z0-9_.-]*" defaultValue={enrollment.account_name} autoComplete="username" placeholder={t("예: alice")} /></Field>
        <div className="field-pair">{(["uid", "gid"] as const).map(key => <Field key={key} id={"onboarding-" + key} label={t("계정 {kind} (필수)", { kind: key.toUpperCase() })}><Input id={"onboarding-" + key} name={key} type="number" required min={0} max={4294967295} step={1} defaultValue={enrollment[key]} /></Field>)}</div>
        <p className="text-xs text-muted-foreground"><Trans text="메인 컨테이너에서 {whoami}, {uid}, {gid}로 확인하세요. 다른 작업 컨테이너에서도 본인의 UID/GID가 같아야 합니다." values={{ whoami: <code>whoami</code>, uid: <code>id -u</code>, gid: <code>id -g</code> }} /></p>
        <Field id="onboarding-webhook" label={t("Discord 웹훅 URL (필수)")} help={t("연구실 Discord 서버의 본인 알림 채널 웹훅을 입력하세요. 저장 후 URL은 다시 표시하지 않습니다.")}><Input id="onboarding-webhook" type="password" required maxLength={512} autoComplete="off" spellCheck={false} value={webhook} onChange={e => setWebhook(e.target.value)} aria-describedby="onboarding-webhook-help" /></Field>
        <div className="action-row"><Submit id="onboarding-submit" pending={mutation.pending}>{t("계정 등록 요청")}</Submit></div>
      </form>}
    </div>
  </Panel></div>
}
