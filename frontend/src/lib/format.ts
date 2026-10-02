import { localeTag } from "./i18n"
export const date = (value?: number | null) => value == null ? "—" : new Date(value * 1000).toLocaleString(localeTag())
export const gib = (value: number) => (value / 1024).toLocaleString(localeTag(), { maximumFractionDigits: 1 })
export const states: Record<string, string> = {
  PENDING: "검토 대기", APPROVED: "승인", REJECTED: "거절", REGISTERED: "등록 완료",
  QUEUED: "자원 대기", DISPATCHING: "실행 준비", RUNNING: "실행 중", UNKNOWN: "상태 확인 필요",
  SUCCEEDED: "완료", FAILED: "실패", CANCELLED: "취소", READY: "사용 가능",
  PENDING_APPROVAL: "등록 조건 확인 필요", PENDING_VERIFICATION: "연결 확인 중", UNAVAILABLE: "사용 불가",
}
export const reasons: Record<string, string> = {
  CAPACITY_UNAVAILABLE: "예약 가능한 자원을 기다리고 있습니다.", ENVIRONMENT_NOT_READY: "실행환경 준비를 기다리고 있습니다.",
  NO_ELIGIBLE_ENVIRONMENT: "배정 가능한 환경이 없습니다.", WORKER_UNREACHABLE: "실행기 상태를 확인하고 있습니다.",
  WAITING_FOR_RESOURCES_OR_ENVIRONMENT: "자원 또는 실행환경을 기다리고 있습니다.",
  RUNNER_OFFLINE: "실행 스크립트의 연결을 기다리고 있습니다.", WORKER_OFFLINE: "실행기의 연결을 기다리고 있습니다.",
}
