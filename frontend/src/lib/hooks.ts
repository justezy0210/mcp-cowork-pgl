import { useCallback, useEffect, useRef, useState } from "react"
import { api, session } from "./session"

export function useResource<T>(path: string | null, poll = false, cache = false) {
  const [revision, setRevision] = useState(0)
  const [state, setState] = useState<{ path: string | null; data: T | null; error: string; loading: boolean; updated: Date | null }>(() => {
    const saved = cache ? session.cachedResource<T>(path) : undefined
    return { path, data: saved?.data ?? null, error: "", loading: !saved, updated: saved?.updated ?? null }
  })
  const refresh = useCallback(() => setRevision(value => value + 1), [])
  useEffect(() => {
    const controller = new AbortController()
    if (!path) return
    const saved = cache && revision === 0 ? session.cachedResource<T>(path) : undefined
    if (saved) {
      setState({ path, ...saved, error: "", loading: false })
      return
    }
    setState(previous => ({ path, data: previous.path === path ? previous.data : null, error: "", loading: true, updated: previous.updated }))
    api<T>(path, { signal: controller.signal }).then(data => {
      if (!controller.signal.aborted) {
        const updated = new Date()
        if (cache) session.saveResource(path, data, updated)
        setState({ path, data, error: "", loading: false, updated })
      }
    }).catch(error => {
      if (!controller.signal.aborted) setState({ path, data: null, error: error.message, loading: false, updated: null })
    })
    return () => controller.abort()
  }, [path, revision, cache])
  useEffect(() => {
    if (!poll) return
    const update = () => { if (!document.hidden) refresh() }
    const timer = window.setInterval(update, 20000)
    document.addEventListener("visibilitychange", update)
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", update) }
  }, [poll, refresh])
  return { ...(state.path === path ? state : { data: null, error: "", loading: true, updated: null }), refresh }
}

export function useMutation() {
  const [pending, setPending] = useState(false), [error, setError] = useState("")
  const alive = useRef(true), locked = useRef(false)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const run = async (action: () => Promise<void>) => {
    if (locked.current) return
    locked.current = true; setPending(true); setError("")
    try { await action() } catch (failure) { if (alive.current) setError(failure instanceof Error ? failure.message : "요청을 처리하지 못했습니다.") }
    finally { locked.current = false; if (alive.current) setPending(false) }
  }
  return { pending, error, run, clearError: () => setError("") }
}

export function useHash() {
  const [hash, setHash] = useState(() => location.hash.slice(1) || "servers")
  useEffect(() => { const update = () => { setHash(location.hash.slice(1) || "servers"); window.scrollTo(0, 0) }; window.addEventListener("hashchange", update); return () => window.removeEventListener("hashchange", update) }, [])
  return hash
}
