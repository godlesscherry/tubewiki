import type { VideoOut } from '../types'
import {
  IconAlert,
  IconClose,
  IconExternal,
  IconFile,
  IconPlay,
  IconRefresh,
  IconSearch,
  IconTrash,
  IconYouTube,
} from './icons'
import {
  EmptyState,
  ErrorBanner,
  SkeletonLines,
  Spinner,
  StatusPill,
  formatDate,
  formatDuration,
  formatNumber,
  formatReadingTime,
} from './ui'

function savedPath(video: VideoOut): string | null {
  if (!video.wiki_path) return null
  const raw = video.wiki_path.replace(/^\/+/, '')
  return raw.startsWith('forge-wiki/') ? raw : `forge-wiki/${raw}`
}

function transcriptClass(source: string | null): string {
  if (!source) return ''
  if (source === 'captions') return ' src-chip--captions'
  if (source === 'none') return ' src-chip--none'
  return ''
}

export default function LibraryTable({
  videos,
  loading,
  filtering,
  error,
  onRetry,
  query,
  onQueryChange,
  onDelete,
  deletingId,
  totalDocs,
  exampleUrl,
  onUseExample,
}: {
  videos: VideoOut[]
  loading: boolean
  filtering: boolean
  error: string | null
  onRetry: () => void
  query: string
  onQueryChange: (value: string) => void
  onDelete: (video: VideoOut) => void
  deletingId: string | null
  totalDocs: number
  exampleUrl: string
  onUseExample: () => void
}) {
  const showSkeleton = loading && videos.length === 0
  const showErrorOnly = !!error && videos.length === 0
  const noResults = !loading && !showErrorOnly && videos.length === 0
  const filteredView = query.trim().length > 0
  const globalEmpty = noResults && !filteredView && totalDocs === 0

  return (
    <section className="library" aria-labelledby="library-title">
      <div className="library-head">
        <div className="library-title">
          <h2 id="library-title">Library</h2>
          <span className="count-badge">{videos.length}</span>
          {totalDocs > videos.length ? <span className="library-total">of {totalDocs}</span> : null}
        </div>
        <div className="library-tools">
          <div className={`filter${query ? ' has-value' : ''}`}>
            <IconSearch size={14} className="filter-icon" />
            <input
              type="search"
              className="filter-input"
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') onQueryChange('')
              }}
              placeholder="Filter documents…"
              aria-label="Filter documents"
              spellCheck={false}
              autoComplete="off"
            />
            {filtering ? (
              <Spinner size={13} />
            ) : query ? (
              <button type="button" className="icon-btn filter-clear" onClick={() => onQueryChange('')} aria-label="Clear filter">
                <IconClose size={12} />
              </button>
            ) : null}
          </div>
          <button
            type="button"
            className="icon-btn"
            onClick={onRetry}
            disabled={loading || filtering}
            title="Refresh library"
            aria-label="Refresh library"
          >
            <IconRefresh size={15} />
          </button>
        </div>
      </div>

      {error ? <ErrorBanner message={error} onRetry={onRetry} /> : null}

      {showSkeleton ? (
        <div className="card table-card skeleton-card">
          <SkeletonLines lines={7} />
        </div>
      ) : globalEmpty ? (
        <div className="card">
          <EmptyState
            icon={<IconFile size={23} />}
            title="No documents yet"
            className="empty-hero"
            action={
              <div className="empty-example">
                <span className="example-label">Example link</span>
                <div className="example-row">
                  <code className="example-url">{exampleUrl}</code>
                  <button type="button" className="btn btn-ghost btn-sm" onClick={onUseExample}>
                    Use this link
                  </button>
                </div>
              </div>
            }
          >
            <p>
              Paste a YouTube link above and Forge writes a structured markdown document into{' '}
              <code className="inline-code">forge-wiki/</code> — TL;DR, timestamped chapters with frames, key takeaways
              and a transcript appendix.
            </p>
            <p className="empty-sub">Nothing to configure. No categories, no folders, no browsing — just documents.</p>
          </EmptyState>
        </div>
      ) : noResults ? (
        <div className="card">
          <EmptyState icon={<IconSearch size={21} />} title={`No documents match “${query.trim()}”`}>
            <p>Filtering is by title, channel and saved path. Try a shorter term.</p>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => onQueryChange('')}>
              Clear filter
            </button>
          </EmptyState>
        </div>
      ) : (
        <div className="card table-card">
          <div className="table-wrap">
            <table className="library-table">
              <thead>
                <tr>
                  <th className="col-thumb" scope="col">
                    <span className="sr-only">Preview</span>
                  </th>
                  <th scope="col">Document</th>
                  <th scope="col" className="col-duration">
                    Duration
                  </th>
                  <th scope="col" className="col-date">
                    Extracted
                  </th>
                  <th scope="col" className="col-words">
                    Words
                  </th>
                  <th scope="col" className="col-frames">
                    Frames
                  </th>
                  <th scope="col" className="col-transcript">
                    Transcript
                  </th>
                  <th scope="col" className="col-status">
                    Status
                  </th>
                  <th scope="col" className="actions-cell">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {videos.map((video) => {
                  const path = savedPath(video)
                  const title = video.title ?? video.video_id
                  return (
                    <tr key={video.video_id} className={video.status === 'error' ? 'row-is-error' : undefined}>
                      <td className="thumb-cell">
                        <div className="thumb">
                          <span className="thumb-ph" aria-hidden="true">
                            <IconPlay size={14} />
                          </span>
                          {video.thumbnail ? (
                            <img
                              src={video.thumbnail}
                              alt=""
                              loading="lazy"
                              onError={(e) => {
                                e.currentTarget.style.visibility = 'hidden'
                              }}
                            />
                          ) : null}
                        </div>
                      </td>
                      <td className="title-cell">
                        <div className="doc-title-row">
                          <span className="doc-title" title={title}>
                            {title}
                          </span>
                          {video.url ? (
                            <a
                              className="doc-link"
                              href={video.url}
                              target="_blank"
                              rel="noreferrer noopener"
                              aria-label="Open on YouTube"
                              title="Open on YouTube"
                            >
                              <IconExternal size={11} />
                            </a>
                          ) : null}
                        </div>
                        <div className="doc-meta">
                          <span className="doc-channel">
                            <IconYouTube size={11} />
                            {video.channel ?? 'Unknown channel'}
                          </span>
                        </div>
                        {path ? (
                          <div className="doc-path" title={path}>
                            <IconFile size={10} />
                            <span>{path}</span>
                          </div>
                        ) : (
                          <div className="doc-path doc-path--missing">not saved yet</div>
                        )}
                        {video.error ? (
                          <div className="doc-error">
                            <IconAlert size={11} />
                            <span>{video.error}</span>
                          </div>
                        ) : null}
                      </td>
                      <td className="col-duration num-cell">{formatDuration(video.duration)}</td>
                      <td className="col-date date-cell">
                        <span
                          className="date-main"
                          title={`Added ${formatDate(video.added)} · Extracted ${formatDate(video.extracted)}`}
                        >
                          {formatDate(video.extracted ?? video.added)}
                        </span>
                      </td>
                      <td className="col-words words-cell">
                        <span className="words-main">{formatNumber(video.words)}</span>
                        <span className="words-sub">{formatReadingTime(video.reading_minutes)}</span>
                      </td>
                      <td className="col-frames num-cell">{video.frame_count > 0 ? formatNumber(video.frame_count) : '—'}</td>
                      <td className="col-transcript">
                        {video.transcript_source ? (
                          <span className={`src-chip${transcriptClass(video.transcript_source)}`}>
                            {video.transcript_source}
                          </span>
                        ) : (
                          <span className="dash">—</span>
                        )}
                      </td>
                      <td className="col-status">
                        <StatusPill status={video.status} />
                      </td>
                      <td className="actions-cell">
                        <button
                          type="button"
                          className="icon-btn icon-btn--danger"
                          onClick={() => onDelete(video)}
                          disabled={deletingId === video.video_id}
                          aria-label={`Delete ${title}`}
                          title="Delete from library"
                        >
                          {deletingId === video.video_id ? <Spinner size={13} /> : <IconTrash size={14} />}
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  )
}
