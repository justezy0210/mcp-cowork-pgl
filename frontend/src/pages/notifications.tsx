import { t } from "@/lib/i18n"
import { Bell } from "lucide-react"
import { Loading, Notice, Panel, Refresh } from "@/components/shared"
import { useResource } from "@/lib/hooks"
import type { NotificationStatus } from "@/lib/types"

export function NotificationsPage() {
  const resource = useResource<NotificationStatus>("/notifications")
  return <div className="page-stack"><Refresh onClick={resource.refresh} busy={resource.loading} updated={resource.updated} /><Panel title={t("내 알림 채널")} description={t("본인이 제출한 작업의 시작·완료·실패·취소 알림을 받습니다.")}><div className="space-y-5"><Notice error>{resource.error}</Notice>{!resource.data ? resource.loading && <Loading /> : <div className="flex items-start gap-3 rounded-lg border bg-muted/40 p-4"><Bell className="mt-1 size-5 shrink-0 text-primary" /><div><p className="font-medium">{resource.data.configured ? t("Discord 연결 완료") : t("알림 채널 미설정")}</p><p id="notification-status" className="mt-1 text-sm text-muted-foreground">{resource.data.configured ? t("채널 {v0}", { v0: resource.data.channel_id }) : t("관리자에게 본인 채널 연결을 요청하세요.")}</p></div></div>}<p className="text-sm text-muted-foreground">{t("가입할 때 등록한 본인 채널에서 알림을 확인하세요. 채널을 변경하려면 관리자에게 요청하세요.")}</p><p className="text-xs text-muted-foreground">{t("변경은 이후 제출하는 작업부터 적용됩니다. 이미 제출한 작업은 기존 채널로 알림을 보냅니다.")}</p></div></Panel></div>
}
