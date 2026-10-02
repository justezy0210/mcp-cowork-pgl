import { t, useLanguage, setLanguage, type Language } from "@/lib/i18n"
import { useEffect, useRef, useState } from "react"
import { Bell, BookOpen, Boxes, KeyRound, ListTodo, LogOut, Menu, Server, ShieldCheck } from "lucide-react"
import { Button } from "@/components/ui/button"
import { NativeSelect } from "@/components/ui/native-select"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { Loading, Notice, Panel } from "@/components/shared"
import { session, useSession } from "@/lib/session"
import { useHash } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { OnboardingPage } from "@/pages/onboarding"
import { ServersPage, ServerDetailPage, JobsPage } from "@/pages/servers"
import { EnvironmentsPage } from "@/pages/environments"
import { TokensPage } from "@/pages/tokens"
import { GuidePage } from "@/pages/guide"
import { AdminPage } from "@/pages/admin"
import { NotificationsPage } from "@/pages/notifications"
import type { Profile, IssuedToken } from "@/lib/types"

const navigation = [
  { id: "servers", label: "서버 현황", icon: Server, description: "사용 가능한 서버와 예약 현황을 확인하세요." },
  { id: "jobs", label: "내 작업", icon: ListTodo, description: "제출한 작업의 진행 상황과 결과를 확인하세요." },
  { id: "environments", label: "작업할 컨테이너", icon: Boxes, description: "메인 컨테이너에서 SSH로 작업을 보낼 대상을 연결하세요." },
  { id: "tokens", label: "개인 토큰", icon: KeyRound, description: "메인 연결 프로그램에서 사용할 토큰을 관리하세요." },
  { id: "notifications", label: "Discord 알림", icon: Bell, description: "내 작업의 알림을 받는 채널을 확인하세요." },
  { id: "guide", label: "메인 프로그램 설치", icon: BookOpen, description: "평소 에이전트를 실행하는 메인 컨테이너에 한 번 설치하세요." },
  { id: "admin", label: "관리자", icon: ShieldCheck, description: "계정을 승인하고 사용자 권한을 관리하세요." },
]
function Brand() { return <a href="#servers" className="flex shrink-0 items-center gap-2.5 font-semibold tracking-tight"><span className="grid size-8 place-items-center rounded-lg bg-primary text-xl text-white">c</span><span className="text-xl">cowork</span><span className="ml-1 hidden border-l sm:inline pl-3 text-[10px] tracking-widest text-muted-foreground">PGL</span></a> }
function Portal({ profile }: { profile: Profile }) {
  const hash = useHash(), [mobile, setMobile] = useState(false)
  const [token, setToken] = useState<IssuedToken | null>(null)
  const [jobs, setJobs] = useState({ server: "", completed: false, page: 1 })
  const detail = /^servers\/([A-Za-z0-9][A-Za-z0-9_.-]{0,79})$/.exec(hash)
  const available = navigation.filter(item => item.id !== "admin" || profile.is_admin)
  const active = detail ? "servers" : available.some(item => item.id === hash) ? hash : "servers"
  const current = available.find(item => item.id === active)!
  const heading = useRef<HTMLHeadingElement>(null)
  const previousHash = useRef(hash)
  const auth = useSession()
  useEffect(() => { setMobile(false); heading.current?.focus({ preventScroll: true }); if (previousHash.current !== hash) { previousHash.current = hash; void session.refreshProfile() } }, [hash])
  useEffect(() => {
    if (!["servers", "jobs", "environments"].includes(active)) return
    const refresh = () => { if (!document.hidden) void session.refreshProfile() }
    const timer = window.setInterval(refresh, 20000)
    document.addEventListener("visibilitychange", refresh)
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", refresh) }
  }, [active])
  const nav = <nav aria-label={t("서버 관리 메뉴")} className="space-y-1.5">{available.map(item => <a key={item.id} href={"#" + item.id} aria-current={active === item.id ? "page" : undefined} onClick={() => setMobile(false)} className={cn("flex min-h-11 items-center gap-3 rounded-lg px-3 py-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground", active === item.id && "bg-accent font-semibold text-primary")}><item.icon className="size-4" />{t(item.label)}</a>)}</nav>
  return <div id="portal" className="mx-auto grid max-w-[1540px] gap-6 px-4 py-6 md:grid-cols-[210px_minmax(0,1fr)] md:px-8 md:py-8 lg:gap-9">
    <aside className="sticky top-24 hidden self-start md:block"><p className="mb-5 px-3 text-[10px] font-semibold tracking-[.2em] text-muted-foreground">WORKSPACE</p>{nav}<p className="mt-8 px-3 text-xs text-muted-foreground">{t("계산은 MCP·CLI에서 제출하고 현황과 권한은 이곳에서 관리합니다.")}</p></aside>
    <main className="min-w-0"><div className="mb-7 flex items-start gap-3">
      <Sheet open={mobile} onOpenChange={setMobile}><SheetTrigger asChild><Button variant="outline" size="icon" className="md:hidden" aria-label={t("메뉴 열기")}><Menu /></Button></SheetTrigger><SheetContent side="left" className="w-[min(300px,85vw)]"><SheetHeader><SheetTitle>{t("Cowork 메뉴")}</SheetTitle></SheetHeader><div className="px-4">{nav}</div></SheetContent></Sheet>
      <div className="min-w-0"><h1 ref={heading} tabIndex={-1} className="text-2xl font-semibold tracking-tight outline-none">{detail ? t("{v0} 서버 작업", { v0: detail[1] }) : t(current.label)}</h1><p className="mt-2 text-sm text-muted-foreground">{detail ? t("이 서버를 함께 사용하는 사람들의 작업과 예약 정보입니다.") : t(current.description)}</p></div>
    </div>
    {auth.message && <div className="mb-6"><Notice error>{auth.message}</Notice></div>}
    {detail ? <ServerDetailPage key={detail[1]} id={detail[1]} /> : active === "servers" ? <ServersPage />
      : active === "jobs" ? <JobsPage profile={profile} settings={jobs} onChange={setJobs} />
      : active === "environments" ? <EnvironmentsPage profile={profile} />
      : active === "tokens" ? <TokensPage profile={profile} pendingToken={token} onToken={setToken} />
      : active === "notifications" ? <NotificationsPage />
      : active === "guide" ? <GuidePage profile={profile} pendingToken={token} onToken={setToken} /> : <AdminPage />}
    <footer className="mt-10 border-t pt-5 text-xs leading-6 text-muted-foreground">{t("사용 가능한 서버와 컨테이너는 관리자가 승인한 본인 권한을 따릅니다.")}</footer>
    </main>
  </div>
}
export default function App() {
  const auth = useSession(), language = useLanguage()
  useEffect(() => { document.documentElement.lang = language; document.title = t("서버 관리 · Cowork") }, [language])
  useEffect(() => { void session.start() }, [])
  return <><a href="#content" className="sr-only focus:not-sr-only focus:fixed focus:z-50 focus:bg-background focus:p-3">{t("본문으로 이동")}</a><header className="border-b bg-card"><div className="mx-auto flex min-h-18 max-w-[1540px] flex-wrap items-center justify-between gap-x-3 gap-y-1 px-4 py-3 sm:flex-nowrap md:px-8"><Brand />
      {auth.user && <span id="account-name" className="order-2 w-full min-w-0 text-right text-xs leading-5 text-muted-foreground [overflow-wrap:anywhere] sm:order-none sm:w-auto sm:flex-1">{auth.profile?.user_id || auth.user.email}</span>}
      <div className="ml-auto flex shrink-0 items-center gap-2">
        {auth.user && <Button id="logout" variant="ghost" onClick={session.logout} aria-label={t("로그아웃")}><LogOut className="size-4" /><span className="max-sm:hidden">{t("로그아웃")}</span></Button>}
        <label htmlFor="language" className="sr-only">{t("언어 선택")}</label><NativeSelect id="language" className="min-h-11 w-24 shrink-0 sm:w-28" value={language} onChange={event => setLanguage(event.target.value as Language)}><option value="ko" lang="ko">한국어</option><option value="en" lang="en">English</option></NativeSelect>
      </div>
    </div></header>
    <div id="content">{auth.status === "ready" && auth.profile ? <Portal key={auth.epoch} profile={auth.profile} />
      : auth.status === "onboarding" ? <OnboardingPage key={auth.epoch} />
      : <main className="mx-auto max-w-lg px-4 py-16"><Panel title={auth.user ? t("로그인되었습니다") : t("본인 계정으로 시작하기")} description={t("Google로 로그인한 뒤 본인 계정으로 서버와 작업을 관리하세요.")}><div className="space-y-5"><Notice error>{auth.message}</Notice>{["loading", "checking"].includes(auth.status) ? <><Loading /><p className="text-sm text-muted-foreground">{auth.status === "checking" ? t("계정과 서버 권한을 확인하고 있습니다.") : t("로그인을 준비하고 있습니다.")}</p></> : <><Button id="login" onClick={session.login} disabled={auth.busy || auth.status === "disabled"} className="w-full">{auth.busy ? t("로그인 중…") : auth.status === "disabled" ? t("로그인 준비 필요") : auth.user ? t("연결 다시 확인") : t("Google로 로그인")}</Button><p className="text-xs text-muted-foreground">{t("새 계정은 관리자 승인 후 이용할 수 있습니다.")}</p></>}</div></Panel></main>}</div>
  </>
}
