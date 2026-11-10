// Small shared UI kit + formatting helpers for the lean extraction UI.
import type { ReactNode } from 'react'
import { IconAlert, IconClose } from './icons'

export interface ToastData {
  kind: 'ok' | 'error' | 'info'
  message: string
}

/** Accepts newline / comma / whitespace separated URLs, keeps order, drops duplicates. */
export function parseUrls(text: string): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of text.split(/[\s,]+/)) {
    const token = raw.trim()
    if (!token || seen.has(token)) continue
    seen.add(token)
    out.push(token)
  }
  return out
}

export function errMsg(e: unknown): string {
  if (e instanceof Error) {
    const status = (e as Error & { status?: number }).status
    return status ? `${e.message} (${status})` : e.message
  }
  if (typeof e === 'string') return e
  try {
    return JSON.stringify(e)
  } catch {
    return 'Unknown error'
  }
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value.length >= 10 ? value.slice(0, 10) : value
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds <= 0) return '—'
  const total = Math.round(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toLocaleString()
}

export function formatReadingTime(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes) || minutes <= 0) return '—'
  return `${minutes} min read`
}

export function Spinner({ size = 15, className }: { size?: number; className?: string }) {
  return (
    <span
      className={`spinner${className ? ` ${className}` : ''}`}
      style={{ width: size, height: size }}
      role="status"
      aria-label="Loading"
    />
  )
}

const STATUS_TONE: Record<string, 'mint' | 'accent' | 'orange' | 'red' | 'muted'> = {
  done: 'mint',
  extracting: 'accent',
  running: 'accent',
  queued: 'orange',
  pending: 'orange',
  error: 'red',
}

export function StatusPill({ status, label }: { status: string | null | undefined; label?: string }) {
  const s = (status ?? 'unknown').toLowerCase()
  const tone = STATUS_TONE[s] ?? 'muted'
  const pulse = s === 'extracting' || s === 'running' || s === 'queued'
  return (
    <span className={`pill pill--${tone}${pulse ? ' pill--pulse' : ''}`}>
      <span className="pill-dot" />
      {label ?? (s === 'unknown' ? 'unknown' : s)}
    </span>
  )
}

export function ErrorBanner({
  message,
  onRetry,
  compact = false,
}: {
  message: string
  onRetry?: () => void
  compact?: boolean
}) {
  return (
    <div className={`error-banner${compact ? ' error-banner--compact' : ''}`} role="alert">
      <IconAlert size={compact ? 14 : 16} className="error-banner-icon" />
      <div className="error-banner-text">{message}</div>
      {onRetry ? (
        <button type="button" className="btn btn-ghost btn-sm" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  )
}

export function EmptyState({
  icon,
  title,
  children,
  action,
  className = '',
}: {
  icon?: ReactNode
  title: string
  children?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={`empty-state${className ? ` ${className}` : ''}`}>
      <div className="empty-icon">{icon}</div>
      <h3>{title}</h3>
      {children ? <div className="empty-text">{children}</div> : null}
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  )
}

export function SkeletonLines({ lines = 5 }: { lines?: number }) {
  return (
    <div className="skeleton-lines">
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skeleton skeleton-line" />
      ))}
    </div>
  )
}

export function ToastView({ toast, onClose }: { toast: ToastData; onClose: () => void }) {
  const glyph = toast.kind === 'ok' ? '✓' : toast.kind === 'error' ? '!' : '·'
  return (
    <div className={`toast toast--${toast.kind}`} role="status">
      <span className="toast-icon">{glyph}</span>
      <span className="toast-msg">{toast.message}</span>
      <button type="button" className="icon-btn toast-close" onClick={onClose} aria-label="Dismiss notification">
        <IconClose size={13} />
      </button>
    </div>
  )
}
