import type { RefObject } from 'react'
import type { IngestResponse } from '../types'
import { IconCheck, IconClose, IconExtract, IconSparkle } from './icons'
import { Spinner } from './ui'

function UrlList({ label, urls, tone }: { label: string; urls: string[]; tone: 'orange' | 'red' }) {
  if (urls.length === 0) return null
  return (
    <details className="result-details">
      <summary>
        <span className={`pill pill--${tone}`}>
          <span className="pill-dot" />
          {urls.length} {label}
        </span>
        <span className="result-summary-hint">view</span>
      </summary>
      <ul className="mono-list">
        {urls.map((url) => (
          <li key={url}>{url}</li>
        ))}
      </ul>
    </details>
  )
}

export default function IngestCard({
  value,
  onChange,
  urlCount,
  force,
  onForceChange,
  onExtract,
  submitting,
  result,
  onDismissResult,
  inputRef,
}: {
  value: string
  onChange: (value: string) => void
  urlCount: number
  force: boolean
  onForceChange: (force: boolean) => void
  onExtract: () => void
  submitting: boolean
  result: IngestResponse | null
  onDismissResult: () => void
  inputRef: RefObject<HTMLTextAreaElement | null>
}) {
  const canExtract = urlCount > 0 && !submitting
  const nothingExtracted = !!result && result.accepted === 0 && !result.job_id

  return (
    <section className="card ingest-card" aria-labelledby="ingest-title">
      <div className="ingest-head">
        <div className="ingest-copy">
          <div className="eyebrow">
            <IconExtract size={12} />
            Extract
          </div>
          <h2 id="ingest-title">Paste YouTube links</h2>
          <p>
            One per line, or separated by commas and spaces. Each video becomes a detailed markdown document in{' '}
            <code className="inline-code">vault/</code>.
          </p>
        </div>
        {urlCount > 0 ? (
          <span className="url-count">
            <span className="url-count-num">{urlCount}</span>
            link{urlCount === 1 ? '' : 's'}
          </span>
        ) : null}
      </div>

      <div className="textarea-wrap">
        <textarea
          ref={inputRef}
          className="ingest-input"
          rows={4}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
              e.preventDefault()
              onExtract()
            }
          }}
          placeholder={'https://www.youtube.com/watch?v=…\nhttps://youtu.be/…'}
          aria-label="YouTube URLs, one per line"
          spellCheck={false}
          autoComplete="off"
        />
        {value ? (
          <button type="button" className="icon-btn textarea-clear" onClick={() => onChange('')} aria-label="Clear URLs">
            <IconClose size={13} />
          </button>
        ) : null}
      </div>

      <div className="ingest-actions">
        <label className="check">
          <input type="checkbox" checked={force} onChange={(e) => onForceChange(e.target.checked)} />
          <span className="check-box" aria-hidden="true">
            <IconCheck size={11} />
          </span>
          <span className="check-text">
            Re-extract already saved
            <span className="check-hint">duplicates are skipped by default</span>
          </span>
        </label>
        <button type="button" className="btn btn-primary extract-btn" onClick={onExtract} disabled={!canExtract}>
          {submitting ? <Spinner size={14} /> : <IconExtract size={15} />}
          {submitting ? 'Submitting…' : 'Extract documents'}
          {!submitting && urlCount > 0 ? <span className="btn-count">{urlCount}</span> : null}
        </button>
      </div>

      {result ? (
        <div className="ingest-result" role="status">
          <div className="result-row">
            <span className="pill pill--mint">
              <IconCheck size={11} />
              {result.accepted} accepted
            </span>
            <UrlList label="duplicate" urls={result.duplicates} tone="orange" />
            <UrlList label="rejected" urls={result.rejected} tone="red" />
            <span className="result-note">
              {result.accepted > 0 ? (
                <>
                  <IconSparkle size={11} /> Extraction queued — progress appears below.
                </>
              ) : nothingExtracted ? (
                <>Nothing new to extract — links were already saved or rejected. Tick “Re-extract” to force.</>
              ) : (
                'No new documents were queued.'
              )}
            </span>
            <button
              type="button"
              className="icon-btn result-close"
              onClick={onDismissResult}
              aria-label="Dismiss extraction result"
            >
              <IconClose size={13} />
            </button>
          </div>
        </div>
      ) : null}
    </section>
  )
}
