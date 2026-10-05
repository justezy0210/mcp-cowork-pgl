import { useSyncExternalStore } from "react"
import { errors } from "./errors"
import type { Profile, Enrollment } from "./types"

type GoogleUser = { uid: string; email?: string; getIdToken(): Promise<string> }
type Config = { enabled: boolean; api_base_url?: string; firebase: { projectId: string; [key: string]: unknown } }
type AuthSDK = {
  initializeAuth(app: unknown, options: { persistence: unknown; popupRedirectResolver: unknown }): unknown
  browserLocalPersistence: unknown; browserPopupRedirectResolver: unknown
  onAuthStateChanged(auth: unknown, callback: (user: GoogleUser | null) => void): () => void
  signOut(auth: unknown): Promise<void>
  signInWithPopup(auth: unknown, provider: unknown): Promise<unknown>
  GoogleAuthProvider: new () => { setCustomParameters(parameters: Record<string, string>): void }
}
type State = {
  status: "loading" | "login" | "checking" | "ready" | "onboarding" | "error" | "disabled"
  profile: Profile | null; user: GoogleUser | null; enrollment: Enrollment | null
  message: string; epoch: number; busy: boolean
}
export class APIError extends Error {
  constructor(message: string, public code = "", public status = 0) { super(message) }
}
const CACHE = "cowork.profile.v1", MAX_AGE = 30 * 60 * 1000
const clearCache = () => {
  try { localStorage.removeItem(CACHE) } catch { /* Optional storage. */ }
  try { sessionStorage.removeItem(CACHE) } catch { /* Clear the previous tab-only cache. */ }
}
const initial: State = { status: "loading", profile: null, user: null, enrollment: null, message: "", epoch: 0, busy: false }

class Session {
  private state = initial
  private listeners = new Set<() => void>()
  private sdk?: AuthSDK
  private auth: unknown
  private base = ""
  private scope = ""
  private started?: Promise<void>
  private generation = 0
  private profileRequest = 0
  private profilePending?: { generation: number; promise: Promise<void> }
  private resources = new Map<string, { data: unknown; updated: Date }>()
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  snapshot = () => this.state
  private update(patch: Partial<State>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener()) }
  private cached(user: GoogleUser): Profile | null {
    try {
      const saved = JSON.parse(localStorage.getItem(CACHE) || "null"), p = saved?.profile
      const age = Date.now() - saved?.savedAt
      if (saved?.scope === this.scope && saved.uid === user.uid && age >= 0 && age < MAX_AGE
        && typeof p?.user_id === "string" && typeof p.is_admin === "boolean"
        && (p.can_view_catalog === undefined || typeof p.can_view_catalog === "boolean")
        && Array.isArray(p.allowed_nodes) && p.allowed_nodes.every((n: unknown) => typeof n === "string")
        && typeof p.identity?.configured === "boolean" && typeof p.notification?.configured === "boolean"
        && (!p.identity.configured || (Number.isInteger(p.identity.uid) && Number.isInteger(p.identity.gid)))) return p
    } catch { /* Missing or malformed cache. */ }
    clearCache(); return null
  }
  private save(user: GoogleUser, profile: Profile) {
    const { user_id, is_admin, allowed_nodes, identity, notification, can_view_catalog } = profile
    try { localStorage.setItem(CACHE, JSON.stringify({ scope: this.scope, uid: user.uid, savedAt: Date.now(),
      profile: { user_id, is_admin, allowed_nodes, identity, notification: { configured: notification.configured }, can_view_catalog } })) } catch { /* Optional storage. */ }
  }
  cachedResource<T>(path: string | null): { data: T; updated: Date } | undefined {
    const saved = path ? this.resources.get(path) : undefined
    if (saved && Date.now() - saved.updated.getTime() < 30000) return saved as { data: T; updated: Date }
    if (path) this.resources.delete(path)
  }
  saveResource(path: string, data: unknown, updated: Date) {
    this.resources.delete(path)
    this.resources.set(path, { data, updated })
    if (this.resources.size > 50) this.resources.delete(this.resources.keys().next().value!)
  }
  start = () => this.started ||= this.initialize()
  private async initialize() {
    try {
      const response = await fetch("./config.json", { cache: "no-store", redirect: "error" })
      if (!response.ok) throw new Error("로그인 설정을 불러오지 못했습니다.")
      const config: Config = await response.json()
      if (!config.enabled) { this.update({ status: "disabled", message: "관리자가 로그인 설정을 준비하면 이용할 수 있습니다." }); return }
      const local = (host: string) => ["localhost", "127.0.0.1", "[::1]"].includes(host)
      const base = new URL(config.api_base_url || location.origin)
      if ((location.protocol !== "https:" && !local(location.hostname)) || (base.protocol !== "https:" && !(base.protocol === "http:" && local(base.hostname)))) throw new Error("관리자가 제공한 HTTPS 주소로 접속하세요.")
      this.base = base.href.replace(/\/$/, "")
      this.scope = JSON.stringify([config.firebase.projectId, base.href])
      const appUrl = "https://www.gstatic.com/firebasejs/12.19.0/firebase-app.js"
      const authUrl = "https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js"
      const [app, sdk] = await Promise.all([import(/* @vite-ignore */ appUrl), import(/* @vite-ignore */ authUrl)])
      this.sdk = sdk as AuthSDK
      this.auth = this.sdk.initializeAuth(app.initializeApp(config.firebase), {
        persistence: this.sdk.browserLocalPersistence, popupRedirectResolver: this.sdk.browserPopupRedirectResolver,
      })
      this.sdk.onAuthStateChanged(this.auth, user => { void this.changeUser(user) })
      window.addEventListener("pagehide", () => {
        this.generation++
        this.resources.clear()
        this.update({ profile: null, enrollment: null, epoch: this.state.epoch + 1, status: this.state.user ? "checking" : "login" })
      })
      window.addEventListener("pageshow", event => { if (event.persisted && this.state.user) void this.changeUser(this.state.user) })
    } catch (error) { this.update({ status: "disabled", message: error instanceof Error ? error.message : "로그인 준비에 실패했습니다." }) }
  }
  private async changeUser(user: GoogleUser | null) {
    this.generation++
    this.resources.clear()
    this.update({ user, profile: null, enrollment: null, epoch: this.state.epoch + 1, message: "", busy: false, status: user ? "checking" : "login" })
    if (!user) { clearCache(); return }
    const cached = this.cached(user)
    if (cached) this.update({ profile: cached, status: "ready" })
    await this.refreshProfile()
  }
  request = async <T>(path: string, options: RequestInit = {}): Promise<T> => {
    const user = this.state.user, generation = this.generation
    if (!user) throw new APIError(errors.UNAUTHENTICATED, "UNAUTHENTICATED")
    const valid = () => user === this.state.user && generation === this.generation
    let token: string
    try { token = await user.getIdToken() } catch { throw new APIError(errors.UNAUTHENTICATED, "UNAUTHENTICATED") }
    if (!valid()) throw new APIError("이전 로그인 세션의 요청입니다.", "STALE_SESSION")
    let response: Response
    try {
      const timeout = AbortSignal.timeout(20000)
      response = await fetch(this.base + "/v1/web" + path, { ...options, cache: "no-store", credentials: "omit", redirect: "error",
        signal: options.signal ? AbortSignal.any([timeout, options.signal]) : timeout,
        headers: { Authorization: "Bearer " + token, ...(options.body ? { "Content-Type": "application/json" } : {}) } })
    } catch (error) {
      if (options.signal?.aborted) throw error
      throw new APIError("허브에 연결할 수 없습니다. 네트워크와 허브 상태를 확인하세요.")
    }
    let body
    try { body = await response.json() } catch { throw new APIError("허브 응답을 확인할 수 없습니다.") }
    if (!valid()) throw new APIError("이전 로그인 세션의 요청입니다.", "STALE_SESSION")
    if (!response.ok) {
      const code = body?.error?.code || ""
      const error = new APIError(errors[code] || "요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.", code, response.status)
      if (response.status === 401 || response.status === 403) this.resources.clear()
      if (response.status === 401) { void this.logout() }
      throw error
    }
    return body as T
  }
  refreshProfile = () => {
    if (this.profilePending?.generation === this.generation) return this.profilePending.promise
    const pending = { generation: this.generation, promise: this.loadProfile() }
    this.profilePending = pending
    void pending.promise.finally(() => { if (this.profilePending === pending) this.profilePending = undefined })
    return pending.promise
  }
  private loadProfile = async () => {
    const user = this.state.user, request = ++this.profileRequest
    let generation = this.generation
    if (!user) return
    const valid = () => user === this.state.user && generation === this.generation && request === this.profileRequest
    try {
      const profile = await this.request<Profile>("/profile")
      if (!valid()) return
      if (profile.can_view_catalog === false) this.resources.clear()
      this.save(user, profile)
      this.update({ profile, status: "ready", message: "", enrollment: null })
    } catch (error) {
      if (!valid()) return
      if (this.state.profile && !(error instanceof APIError && [401, 403].includes(error.status))) {
        this.update({ message: error instanceof Error ? error.message : "최신 권한을 확인하지 못했습니다." })
        return
      }
      generation = ++this.generation
      this.resources.clear()
      clearCache()
      this.update({ profile: null, enrollment: null, epoch: this.state.epoch + 1, status: "error", message: error instanceof Error ? error.message : "계정 확인에 실패했습니다." })
      if (error instanceof APIError && error.code === "WEB_ACCOUNT_NOT_LINKED") {
        this.update({ status: "onboarding", message: "" })
        try {
          const enrollment = await this.request<Enrollment>("/onboarding")
          if (valid()) this.update({ enrollment })
        } catch (failure) { if (valid()) this.update({ message: failure instanceof Error ? failure.message : "등록 상태를 확인하지 못했습니다." }) }
      }
    }
  }
  setEnrollment = (enrollment: Enrollment) => this.update({ enrollment })
  login = async () => {
    if (this.state.user) { await this.refreshProfile(); return }
    if (!this.sdk || this.state.busy) return
    this.update({ busy: true, message: "" })
    try {
      const provider = new this.sdk.GoogleAuthProvider()
      provider.setCustomParameters({ prompt: "select_account" })
      await this.sdk.signInWithPopup(this.auth, provider)
    } catch (error) {
      if ((error as { code?: string }).code !== "auth/popup-closed-by-user") this.update({ message: "Google 로그인을 완료하지 못했습니다. 팝업 허용과 로그인 설정을 확인하세요." })
    } finally { this.update({ busy: false }) }
  }
  logout = async () => {
    await this.changeUser(null)
    try { await this.sdk?.signOut(this.auth) } catch { this.update({ message: "로그아웃을 완료하지 못했습니다. 페이지를 새로고침한 뒤 다시 시도해 주세요." }) }
  }
}
export const session = new Session()
export const useSession = () => useSyncExternalStore(session.subscribe, session.snapshot)
export const api = session.request
export const post = <T>(path: string, body: unknown) => api<T>(path, { method: "POST", body: JSON.stringify(body) })
