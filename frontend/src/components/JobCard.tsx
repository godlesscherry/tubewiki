import type { JobOut } from '../types'
import { IconAlert, IconCheck, IconExtract } from './icons'
import { Spinner, StatusPill } from './ui'

function JobIcon({ status }: { status: string }) {
  if (status === 'done') return <IconCheck size={16} />
  if (status === 'error') return <IconAlert size={16} />
  if (status === 'queued') return <IconExtract size={16} />
  return <Spinner size={15} />
}

function labelFor(job: JobOut): string {
  const total = Math.max(job.total, 0)
  if (job.status === 'queued') return 'Queued'
  if (job.status === 'error') return total > 0 ? `Failed after ${job.done}/${total}` : 'Failed'
  if (job.status === 'done') return total > 0 ? `Written ${job.done}/${total}` : 'Finished'
  if (total > 0) return `Extracting ${Math.min(job.done, total)}/${total}`
  return 'Extracting'
}

export default function JobCard({ jobs }: { jobs: JobOut[] }) {
  if (jobs.length === 0) return null
  return (
    <div className="job-cards">
      {jobs.map((job) => {
        const total = Math.max(job.total, 0)
        const done = total > 0 ? Math.min(Math.max(job.done, 0), total) : 0
        const pct = total > 0 ? Math.round((done / total) * 100) : job.status === 'queued' ? 4 : 30
        const tone = job.status === 'error' ? 'red' : job.status === 'done' ? 'mint' : 'accent'
        return (
          <section className={`card job-card job-card--${tone}`} key={job.id} aria-live="polite">
            <div className={`job-icon job-icon--${tone}`}>
              <JobIcon status={job.status} />
            </div>
            <div className="job-body">
              <div className="job-line">
                <span className="job-label">{labelFor(job)}</span>
                <StatusPill status={job.status} />
                {job.current ? (
                  <span className="job-current" title={job.current}>
                    {job.current}
                  </span>
                ) : null}
              </div>
              <div
                className="progress"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={total || 100}
                aria-valuenow={total > 0 ? done : undefined}
                aria-label="Extraction progress"
              >
                <div className={`progress-fill progress-fill--${tone}`} style={{ width: `${pct}%` }} />
              </div>
              {job.error ? <div className="job-error">{job.error}</div> : null}
            </div>
          </section>
        )
      })}
    </div>
  )
}
