export type Profile = {
  user_id: string; is_admin: boolean; allowed_nodes: string[]
  identity: { configured: boolean; uid?: number; gid?: number }
  notification: NotificationStatus
}
export type NotificationStatus = { configured: boolean; channel_id?: string }
export type Page<T> = { items: T[]; total: number; page: number; page_size: number }
export type GPU = { id: string; model: string; memory_mib: number; reserved: boolean }
export type Server = {
  id: string; cpus: number; memory_mib: number; enabled: boolean
  reserved_cpus: number; reserved_memory_mib: number; gpus: GPU[]
  recent_report: boolean; last_seen: number | null; job_counts: Record<string, number>
}
export type Job = {
  id: string; number?: number; name: string; user_id: string; node_id: string | null
  state: string; reason?: string; cpus: number; memory_mib: number; gpu_count: number
  created_at: number; started_at?: number | null; multiple_candidates?: boolean
}
export type Environment = {
  id: string; name: string; user_id: string; node_id: string; ssh_target: string
  workdir: string; uid: number | null; gid: number | null; allowed: boolean; status: string
}
export type Connector = { id: string; name: string; online: boolean }
export type Registration = {
  id: string; target: { node_id: string; user: string; host: string; port: number; workdir: string }
  state: string; error_code?: string; created_at: number
}
export type Enrollment = {
  id: string; state: string; account_name?: string; email?: string
  uid?: number; gid?: number; channel_id?: string
}
export type ManagedUser = {
  id: string; enabled: boolean; uid: number | null; gid: number | null
  allowed_nodes: string[]; notification: NotificationStatus
}
export type UsersPage = Page<ManagedUser> & { notification_registration_available: boolean }
export type Audit = { id: string; actor: string; action: string; target: string; created_at: number; before_value: unknown; after_value: unknown }
export type Token = { id: string; name: string; created_at: number; last_used_at: number | null; revoked_at: number | null }
export type IssuedToken = Token & { token: string }
export type SSHAddress = { user: string; host: string; port: number }
