import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import type { ConfigOut, IngestResponse, JobOut, SourcesOut, VideoOut } from './types'
import IngestCard from './components/IngestCard'
import JobCard from './components/JobCard'
import HistoryTable from './components/HistoryTable'
import SettingsPage from './components/SettingsPage'
import {
  IconBrand,
  IconChevronLeft,
  IconFile,
  IconFolder,
  IconGear,
  IconSparkle,
} from './components/icons'
import { ErrorBanner, ToastView, errMsg, parseUrls, type ToastData } from './components/ui'

type View = 'main' | 'settings'

const EXAMPLE_URL = 'https://www.youtube.com/watch?v=aircAruvnKk'
const EMPTY_SOURCES: SourcesOut = { videos: [], channels: [], total: 0 }

function isActiveJob(job: JobOut): boolean {
  return job.status === 'queued' || job.status === 'running'
}

function normalizeSources(next: SourcesOut | null | undefined): SourcesOut {
  if (!next || !Array.isArray(next.videos)) return EMPTY_SOURCES
  return next
}

function folderName(path: string | undefined): string | null {
  if (!path) return null
  const parts = path.split('/').filter(Boolean)
  return parts.length > 0 ? parts[parts.length - 1] : null
}

export default function App() {
  const [config, setConfig] = useState<ConfigOut | null>(null)
  const [configError, setConfigError] = useState<string | null>(null)

  const [sources, setSources] = useState<SourcesOut | null>(null)
  const [sourcesLoading, setSourcesLoading] = useState(true)
  const [sourcesError, setSourcesError] = useState<string | null>(null)
  const [sourcesRefresh, setSourcesRefresh] = useState(0)
  const [knownTotal, setKnownTotal] = useState(0)

  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')

  const [inputText, setInputText] = useState('')
  const [force, setForce] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [ingestResult, setIngestResult] = useState<IngestResponse | null>(null)

  const [activeJobs, setActiveJobs] = useState<JobOut[]>([])
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [toast, setToast] = useState<ToastData | null>(null)
  const [view, setView] = useState<View>('main')

  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  const activeJobIdsRef = useRef<Set<string>>(new Set())
  const jobProgressRef = useRef('')

  const urls = useMemo(() => parseUrls(inputText), [inputText])
  const docCount = Math.max(config?.videos ?? 0, knownTotal)
  const wikiFolder = folderName(config?.wiki_root)

  // ----------------------------------------------------------------- loaders
  const loadConfig = useCallback(async () => {
    try {
      const next = await api.config()
      setConfig(next)
      setConfigError(null)
    } catch (e) {
      setConfigError(errMsg(e))
    }
  }, [])

  const loadSources = useCallback(async (q: string) => {
    setSourcesLoading(true)
    try {
      const next = await api.sources(q ? { q } : {})
      const normalized = normalizeSources(next)
      setSources(normalized)
      setSourcesError(null)
      if (!q) setKnownTotal(normalized.total)
    } catch (e) {
      setSourcesError(errMsg(e))
    } finally {
      setSourcesLoading(false)
    }
  }, [])

  // Initial config + resume any job that was already running.
  useEffect(() => {
    let cancelled = false
    void loadConfig()
    api
      .jobs()
      .then((list) => {
        if (cancelled || !Array.isArray(list)) return
        const active = list.filter(isActiveJob)
        if (active.length === 0) return
        activeJobIdsRef.current = new Set(active.map((job) => job.id))
        setActiveJobs(active)
      })
      .catch(() => {
        /* job resume is best-effort */
      })
    return () => {
      cancelled = true
    }
  }, [loadConfig])

  // Debounced history filter (~250 ms), then fetch via the q param.
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    void loadSources(debouncedQuery)
  }, [debouncedQuery, sourcesRefresh, loadSources])

  // Poll jobs every 1500 ms while any is queued/running.
  const hasActiveJobs = activeJobs.length > 0
  useEffect(() => {
    if (!hasActiveJobs) return
    let cancelled = false

    const tick = async () => {
      try {
        const list = await api.jobs()
        if (cancelled || !Array.isArray(list)) return
        const nowActive = list.filter(isActiveJob)
        const previousIds = activeJobIdsRef.current
        const finishedIds = [...previousIds].filter((id) => !nowActive.some((job) => job.id === id))

        activeJobIdsRef.current = new Set(nowActive.map((job) => job.id))
        setActiveJobs(nowActive)

        const progressKey = nowActive
          .map((job) => `${job.id}:${job.done}:${job.total}:${job.status}`)
          .join('|')
        if (progressKey !== jobProgressRef.current) {
          jobProgressRef.current = progressKey
          if (nowActive.length > 0) setSourcesRefresh((n) => n + 1)
        }

        if (finishedIds.length > 0) {
          for (const id of finishedIds) {
            let finished: JobOut | null = list.find((job) => job.id === id) ?? null
            if (!finished) {
              try {
                finished = await api.job(id)
              } catch {
                finished = null
              }
            }
            if (!finished) {
              setToast({ kind: 'ok', message: 'Extraction finished' })
            } else if (finished.status === 'error') {
              setToast({
                kind: 'error',
                message: `Extraction failed${finished.error ? `: ${finished.error}` : ''}`,
              })
            } else {
              setToast({
                kind: 'ok',
                message: `Extraction complete — ${finished.done} of ${finished.total} document${
                  finished.total === 1 ? '' : 's'
                } written`,
              })
            }
          }
          jobProgressRef.current = ''
          void loadConfig()
          setSourcesRefresh((n) => n + 1)
        }
      } catch {
        /* transient network error — keep polling */
      }
    }

    const timer = window.setInterval(() => void tick(), 1500)
    void tick()
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [hasActiveJobs, loadConfig])

  // Toast auto-dismiss.
  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), 5200)
    return () => window.clearTimeout(timer)
  }, [toast])

  // ----------------------------------------------------------------- handlers
  const handleExtract = async () => {
    if (submitting || urls.length === 0) return
    setSubmitting(true)
    setIngestResult(null)
    try {
      const response = await api.ingest(urls, force)
      setIngestResult(response)
      if (response.accepted > 0) setInputText('')
      if (response.job_id) {
        const placeholder: JobOut = {
          id: response.job_id,
          kind: 'ingest',
          status: 'queued',
          total: response.accepted,
          done: 0,
          current: null,
          error: null,
          created: new Date().toISOString(),
          updated: null,
        }
        setActiveJobs((prev) => [...prev.filter((job) => job.id !== placeholder.id), placeholder])
        activeJobIdsRef.current = new Set([...activeJobIdsRef.current, placeholder.id])
        jobProgressRef.current = ''
        setToast({
          kind: 'info',
          message: `Queued ${response.accepted} document${response.accepted === 1 ? '' : 's'} for extraction`,
        })
      } else if (response.accepted > 0) {
        setToast({
          kind: 'ok',
          message: `Extracted ${response.accepted} document${response.accepted === 1 ? '' : 's'}`,
        })
      }
      void loadConfig()
      setSourcesRefresh((n) => n + 1)
    } catch (e) {
      setIngestResult(null)
      setToast({ kind: 'error', message: `Ingest failed: ${errMsg(e)}` })
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async (video: VideoOut) => {
    const label = video.title ?? video.video_id
    const confirmed = window.confirm(
      `Delete “${label}” from the history?\n\nThe markdown document in vault/ is left on disk.`,
    )
    if (!confirmed) return
    setDeletingId(video.video_id)
    try {
      await api.deleteSource(video.video_id)
      setToast({ kind: 'ok', message: `Deleted “${label}” from the history` })
      void loadConfig()
      setSourcesRefresh((n) => n + 1)
    } catch (e) {
      setToast({ kind: 'error', message: `Delete failed: ${errMsg(e)}` })
    } finally {
      setDeletingId(null)
    }
  }

  const retryAll = useCallback(() => {
    void loadConfig()
    setSourcesRefresh((n) => n + 1)
  }, [loadConfig])

  const useExample = () => {
    setInputText(EXAMPLE_URL)
    window.setTimeout(() => {
      inputRef.current?.focus()
      inputRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 0)
  }

  const llmTone = configError ? 'offline' : !config ? 'loading' : config.llm ? 'on' : 'off'
  const llmLabel = configError ? 'API offline' : !config ? 'Connecting…' : config.llm ? 'LLM on' : 'No LLM'
  const llmTitle = configError
    ? 'The TubeWiki API is unreachable'
    : config?.llm
      ? 'An LLM is configured — documents are synthesised and detailed'
      : 'No LLM key configured — documents are extractive (works fully offline)'

  return (
    <div className="shell">
      <header className="site-header">
        <div className="header-inner">
          <div className="brand">
            <div className="brand-mark" aria-hidden="true">
              <IconBrand size={17} />
            </div>
            <div className="brand-copy">
              <div className="brand-name">TubeWiki</div>
              <div className="brand-sub">YouTube → detailed markdown documents</div>
            </div>
          </div>
          <div className="header-meta">
            {wikiFolder ? (
              <span className="chip chip--path" title={config?.wiki_root}>
                <IconFolder size={12} />
                {wikiFolder}/
              </span>
            ) : null}
            <span className={`chip chip--llm chip--${llmTone}`} title={llmTitle}>
              <span className="chip-dot" />
              {llmLabel}
            </span>
            <span className="chip chip--count" title={`${docCount} document${docCount === 1 ? '' : 's'} in the history`}>
              <IconFile size={12} />
              <b>{docCount}</b>
              <span className="chip-label">doc{docCount === 1 ? '' : 's'}</span>
            </span>
            <button
              type="button"
              className={`settings-link${view === 'settings' ? ' is-active' : ''}`}
              onClick={() => setView((current) => (current === 'settings' ? 'main' : 'settings'))}
              aria-current={view === 'settings' ? 'page' : undefined}
              aria-label={view === 'settings' ? 'Back to extraction' : 'Open settings'}
              title={view === 'settings' ? 'Back to extraction' : 'Open settings'}
            >
              {view === 'settings' ? <IconChevronLeft size={15} /> : <IconGear size={15} />}
              <span className="settings-link-label">{view === 'settings' ? 'Back' : 'Settings'}</span>
            </button>
          </div>
        </div>
      </header>

      <main className="page">
        {view === 'settings' ? (
          <SettingsPage onBack={() => setView('main')} onSaved={() => void loadConfig()} />
        ) : (
          <>
            {configError ? (
              <ErrorBanner
                message={`Can't reach the TubeWiki API — ${configError}. Is the backend running on 127.0.0.1:8787?`}
                onRetry={retryAll}
              />
            ) : null}

            <IngestCard
              value={inputText}
              onChange={setInputText}
              urlCount={urls.length}
              force={force}
              onForceChange={setForce}
              onExtract={() => void handleExtract()}
              submitting={submitting}
              result={ingestResult}
              onDismissResult={() => setIngestResult(null)}
              inputRef={inputRef}
            />

            <JobCard jobs={activeJobs} />

            <HistoryTable
              videos={sources?.videos ?? []}
              loading={sources === null && sourcesLoading}
              filtering={sourcesLoading && sources !== null}
              error={sourcesError}
              onRetry={retryAll}
              query={query}
              onQueryChange={setQuery}
              onDelete={(video) => void handleDelete(video)}
              deletingId={deletingId}
              totalDocs={docCount}
              exampleUrl={EXAMPLE_URL}
              onUseExample={useExample}
            />
          </>
        )}

        <footer className="site-footer">
          <span className="footer-note">
            <IconSparkle size={11} />
            Local-first · plain markdown is the source of truth
          </span>
          {config?.wiki_root ? (
            <code className="footer-path" title={config.wiki_root}>
              {config.wiki_root}
            </code>
          ) : null}
        </footer>
      </main>

      {toast ? <ToastView toast={toast} onClose={() => setToast(null)} /> : null}
    </div>
  )
}
