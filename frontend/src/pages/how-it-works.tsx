import { ExternalLink } from "lucide-react"
import workflowImage from "@/assets/cowork-workflow.png"
import { Button } from "@/components/ui/button"
import { t } from "@/lib/i18n"

export function HowItWorksPage() {
  return <div id="how-it-works" className="page-stack">
    <p className="max-w-3xl text-sm text-muted-foreground">{t("Cowork는 AI에게 요청한 작업을 연구실의 공유 서버에서 실행하도록 연결합니다. 사용자가 AI에게 작업을 요청하면, AI가 실행할 작업을 준비해 Cowork에 제출합니다.")}</p>
    <figure className="overflow-hidden rounded-xl border bg-card">
      <a href={workflowImage} target="_blank" rel="noopener noreferrer" aria-label={t("작동 원리 그림 크게 보기 (새 탭)")}>
        <img id="workflow-image" src={workflowImage} width={1672} height={941} decoding="async" className="block h-auto w-full" alt={t("사람이 AI에게 작업을 요청하고, AI가 Cowork에 제출합니다. Cowork는 자원을 배정하고 작업을 대기시키거나 서버에서 실행한 뒤 웹과 Discord로 상태를 전달합니다. 접수 후 AI는 연결을 종료할 수 있습니다.")} />
      </a>
      <figcaption className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3">
        <span className="text-xs text-muted-foreground">{t("사람의 요청부터 작업 실행과 알림까지의 흐름입니다. 그림은 영어로 제공됩니다.")}</span>
        <Button asChild variant="outline" size="sm"><a href={workflowImage} target="_blank" rel="noopener noreferrer">{t("그림 크게 보기")}<ExternalLink className="size-4" /></a></Button>
      </figcaption>
    </figure>
    <div className="grid gap-5 text-sm text-muted-foreground lg:grid-cols-2">
      <p>{t("작업이 접수되면 Cowork가 자원 배정·대기·실행을 관리하므로 AI 에이전트는 연결을 종료할 수 있습니다. 대기 중인 작업을 관리하는 데 추가 AI 대화나 토큰은 필요하지 않습니다.")}</p>
      <p>{t("진행 상황은 웹에서 확인할 수 있으며, 작업이 시작되거나 완료·실패하면 본인의 Discord 채널로 알림을 받습니다.")}</p>
    </div>
  </div>
}
