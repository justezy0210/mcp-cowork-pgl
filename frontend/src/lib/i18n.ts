import { useSyncExternalStore } from "react"
import { english } from "./translations/en"

export type Language = "ko" | "en"
const STORAGE_KEY = "cowork.language.v1"
const valid = (value: unknown): value is Language => value === "ko" || value === "en"
function initialLanguage(): Language {
  const query = new URLSearchParams(location.search).get("lang")
  if (valid(query)) {
    try { localStorage.setItem(STORAGE_KEY, query) } catch { /* Storage is optional. */ }
    return query
  }
  try { const saved = localStorage.getItem(STORAGE_KEY); if (valid(saved)) return saved } catch { /* Storage is optional. */ }
  return "ko"
}
let language = initialLanguage()
const listeners = new Set<() => void>()
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener) } }
export const useLanguage = () => useSyncExternalStore(subscribe, () => language)
export const localeTag = () => language === "en" ? "en-US" : "ko-KR"
export function setLanguage(value: Language) {
  if (!valid(value)) return
  language = value
  try { localStorage.setItem(STORAGE_KEY, value) } catch { /* Keep the in-memory choice. */ }
  try { const url = new URL(location.href); url.searchParams.set("lang", value); history.replaceState(history.state, "", url) } catch { /* Language switching still works. */ }
  listeners.forEach(listener => listener())
}
export function t(source: string, values: Record<string, string | number | null | undefined> = {}): string {
  const translated = language === "en" ? english[source] ?? source : source
  return translated.replace(/\{([a-zA-Z0-9_]+)\}/g, (match, key: string) => Object.hasOwn(values, key) ? String(values[key] ?? "—") : match)
}
