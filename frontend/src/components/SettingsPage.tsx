import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type {
  DocMode,
  LLMSettingsUpdate,
  SettingsOut,
  SettingsUpdate,
  TestLLMOut,
} from '../types'
import {
  IconAlert,
  IconCheck,
  IconChevronLeft,
  IconFolder,
  IconGear,
  IconRefresh,
  IconSparkle,
} from './icons'
import { ErrorBanner, SkeletonLines, Spinner, errMsg } from './ui'

const DOC_MODES: { value: DocMode; label: string }[] = [
  { value: 'auto', label: 'Auto (recommended)' },
  { value: 'extractive', label: 'Extractive only (offline)' },
  { value: 'llm', label: 'Always use LLM' },
]

const MODE_HINTS: Record<DocMode, string> = {
  auto: 'Use the LLM when an API key is configured; otherwise fall back to fast extractive documents.',
  extractive: 'Never call an LLM — deterministic documents built from captions. Works fully offline.',
  llm: 'Always call the LLM for every document. Extraction fails if the provider is unreachable.',
}

function normalizeMode(value: string | null | undefined): DocMode {
  return value === 'extractive' || value === 'llm' ? value : 'auto'
}

/** Defensive normalisation: the backend may be mid-deploy and omit fields. */
function normalizeSettings(next: SettingsOut | null | undefined): SettingsOut {
  const llm = next?.llm
  const defaults = next?.defaults
  return {
    llm: {
      available: !!llm?.available,
      api_key_set: !!llm?.api_key_set,
      api_key_hint: llm?.api_key_hint ?? '',
      base_url: llm?.base_url ?? '',
      model: llm?.model ?? '',
      vision_model: llm?.vision_model ?? '',
      doc_mode: normalizeMode(llm?.doc_mode),
    },
    output_dir: next?.output_dir ?? '',
    defaults: {
      output_dir: defaults?.output_dir ?? '',
      base_url: defaults?.base_url ?? '',
      model: defaults?.model ?? '',
      doc_mode: normalizeMode(defaults?.doc_mode),
    },
    settings_file: next?.settings_file ?? '',
  }
}

function normalizeTest(next: TestLLMOut | null | undefined): TestLLMOut {
  return {
    ok: !!next?.ok,
    message: next?.message ?? (next?.ok ? 'Connection ok.' : 'Connection failed.'),
    model: next?.model ?? '',
    latency_ms: Number.isFinite(next?.latency_ms) ? Number(next?.latency_ms) : 0,
  }
}

function BackLink({ onBack, className = '' }: { onBack: () => void; className?: string }) {
  return (
    <button type="button" className={`back-link${className ? ` ${className}` : ''}`} onClick={onBack}>
      <IconChevronLeft size={14} />
      Back to extraction
    </button>
  )
}

export default function SettingsPage({
  onBack,
  onSaved,
}: {
  onBack: () => void
  onSaved: () => void
}) {
  const [settings, setSettings] = useState<SettingsOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [reloadToken, setReloadToken] = useState(0)

  const [apiKeyInput, setApiKeyInput] = useState('')
  const [keyCleared, setKeyCleared] = useState(false)
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [visionModel, setVisionModel] = useState('')
  const [docMode, setDocMode] = useState<DocMode>('auto')
  const [outputDir, setOutputDir] = useState('')

  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<TestLLMOut | null>(null)
  const [testError, setTestError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<{ baseUrl?: string; outputDir?: string }>({})
  const [savedNotice, setSavedNotice] = useState(false)

  const hydrate = (next: SettingsOut) => {
    setSettings(next)
    setApiKeyInput('')
    setKeyCleared(false)
    setBaseUrl(next.llm.base_url)
    setModel(next.llm.model)
    setVisionModel(next.llm.vision_model)
    setDocMode(next.llm.doc_mode)
    setOutputDir(next.output_dir)
    setFormError(null)
    setFieldErrors({})
    setTestResult(null)
    setTestError(null)
  }

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoadError(null)
    api
      .settings()
      .then((next) => {
        if (cancelled) return
        hydrate(normalizeSettings(next))
        setSavedNotice(false)
        setLoading(false)
      })
      .catch((e) => {
        if (cancelled) return
        setLoadError(errMsg(e))
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadToken])

  const keySet = !!settings?.llm.api_key_set
  const willClearKey = keyCleared && keySet
  const maskedHint = settings?.llm.api_key_hint ?? ''
  const savingDisabled = saving || testing

  const dirty = useMemo(() => {
    if (!settings) return false
    if (apiKeyInput.trim() !== '' || keyCleared) return true
    return (
      baseUrl.trim() !== settings.llm.base_url.trim() ||
      model.trim() !== settings.llm.model.trim() ||
      visionModel.trim() !== settings.llm.vision_model.trim() ||
      docMode !== settings.llm.doc_mode ||
      outputDir.trim() !== settings.output_dir.trim()
    )
  }, [settings, apiKeyInput, keyCleared, baseUrl, model, visionModel, docMode, outputDir])

  const buildUpdate = (): {
    update: SettingsUpdate
    errors: { baseUrl?: string; outputDir?: string }
  } => {
    const update: SettingsUpdate = {}
    const errors: { baseUrl?: string; outputDir?: string } = {}
    if (!settings) return { update, errors }
    const llm: LLMSettingsUpdate = {}
    let llmChanged = false

    if (keyCleared && settings.llm.api_key_set) {
      llm.api_key = ''
      llmChanged = true
    } else if (apiKeyInput.trim() !== '') {
      llm.api_key = apiKeyInput
      llmChanged = true
    }

    const nextBase = baseUrl.trim()
    if (nextBase !== settings.llm.base_url.trim()) {
      if (nextBase && !/^https?:\/\//i.test(nextBase)) {
        errors.baseUrl = 'Base URL must start with http:// or https://'
      } else {
        llm.base_url = nextBase
        llmChanged = true
      }
    }

    const nextModel = model.trim()
    if (nextModel !== settings.llm.model.trim()) {
      llm.model = nextModel
      llmChanged = true
    }

    const nextVision = visionModel.trim()
    if (nextVision !== settings.llm.vision_model.trim()) {
      llm.vision_model = nextVision
      llmChanged = true
    }

    if (docMode !== settings.llm.doc_mode) {
      llm.doc_mode = docMode
      llmChanged = true
    }
    if (llmChanged) update.llm = llm

    const nextOutput = outputDir.trim()
    if (!nextOutput) errors.outputDir = 'Output directory cannot be empty.'
    else if (nextOutput !== settings.output_dir.trim()) update.output_dir = nextOutput

    return { update, errors }
  }

  const handleSave = async () => {
    if (!settings || saving) return
    const { update, errors } = buildUpdate()
    setFieldErrors(errors)
    setSavedNotice(false)
    if (Object.keys(errors).length > 0) {
      setFormError('Fix the highlighted fields before saving.')
      return
    }
    setFormError(null)
    if (!update.llm && update.output_dir === undefined) {
      setSavedNotice(true)
      return
    }
    setSaving(true)
    setTestResult(null)
    setTestError(null)
    try {
      const next = normalizeSettings(await api.updateSettings(update))
      hydrate(next)
      setSavedNotice(true)
      onSaved()
    } catch (e) {
      setFormError(errMsg(e))
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    if (testing || saving) return
    setTesting(true)
    setTestResult(null)
    setTestError(null)
    try {
      setTestResult(normalizeTest(await api.testLLM()))
    } catch (e) {
      setTestError(errMsg(e))
    } finally {
      setTesting(false)
    }
  }

  const handleReset = () => {
    if (!settings) return
    hydrate(settings)
    setSavedNotice(false)
  }

  const resetOutputDir = () => {
    if (!settings) return
    setOutputDir(settings.defaults.output_dir)
    setSavedNotice(false)
  }

  const clearKey = () => {
    setKeyCleared(true)
    setApiKeyInput('')
    setSavedNotice(false)
  }

  const statusPill = loading ? (
    <span className="pill pill--muted">
      <span className="pill-dot" />
      Loading…
    </span>
  ) : loadError ? (
    <span className="pill pill--red">
      <span className="pill-dot" />
      Unavailable
    </span>
  ) : dirty ? (
    <span className="pill pill--orange">
      <span className="pill-dot" />
      Unsaved changes
    </span>
  ) : (
    <span className="pill pill--mint">
      <IconCheck size={11} />
      {savedNotice ? 'Settings saved' : 'All changes saved'}
    </span>
  )

  return (
    <section className="settings-page" aria-labelledby="settings-title">
      <div className="settings-head">
        <div>
          <div className="eyebrow">
            <IconGear size={12} />
            Configuration
          </div>
          <h1 id="settings-title">Settings</h1>
          <p>Configure the LLM that writes your documents and where the markdown vault lives.</p>
        </div>
        <div className="settings-status">{statusPill}</div>
      </div>

      {loadError ? (
        <div className="card settings-error">
          <ErrorBanner message={loadError} onRetry={() => setReloadToken((n) => n + 1)} />
          <BackLink onBack={onBack} />
        </div>
      ) : loading || !settings ? (
        <div className="settings-skeleton">
          <div className="card settings-card">
            <SkeletonLines lines={3} />
          </div>
          <div className="card settings-card">
            <SkeletonLines lines={5} />
          </div>
          <div className="card settings-card">
            <SkeletonLines lines={2} />
          </div>
        </div>
      ) : (
        <>
          <div className="settings-grid">
            <section className="card settings-card" aria-labelledby="settings-llm-title">
              <header className="settings-card-head">
                <div className="settings-card-icon" aria-hidden="true">
                  <IconSparkle size={16} />
                </div>
                <div className="settings-card-copy">
                  <h2 id="settings-llm-title">LLM</h2>
                  <p>Optional. Without a key, documents are built extractively and work offline.</p>
                </div>
                <span className={`pill ${settings.llm.available ? 'pill--mint' : 'pill--muted'}`}>
                  <span className="pill-dot" />
                  {settings.llm.available ? 'Connected' : keySet ? 'Key saved' : 'No key'}
                </span>
              </header>

              <div className="settings-body">
                <div className="field">
                  <label className="field-label" htmlFor="settings-api-key">
                    API key
                    {keySet ? (
                      <span className="field-badge field-badge--ok">saved</span>
                    ) : (
                      <span className="field-badge">not set</span>
                    )}
                  </label>
                  <div className="field-input-row">
                    <input
                      id="settings-api-key"
                      className="input input--mono"
                      type="password"
                      value={apiKeyInput}
                      onChange={(e) => {
                        setApiKeyInput(e.target.value)
                        if (e.target.value) setKeyCleared(false)
                        setSavedNotice(false)
                      }}
                      placeholder={
                        willClearKey
                          ? 'Key will be cleared on save'
                          : keySet
                            ? 'Saved — type to replace'
                            : 'sk-… (OpenAI or compatible key)'
                      }
                      autoComplete="new-password"
                      spellCheck={false}
                      aria-describedby="settings-api-key-hint"
                    />
                    {keySet && !keyCleared ? (
                      <button type="button" className="btn btn-ghost btn-sm" onClick={clearKey}>
                        Clear key
                      </button>
                    ) : null}
                    {willClearKey ? (
                      <button type="button" className="btn btn-ghost btn-sm" onClick={() => setKeyCleared(false)}>
                        Keep key
                      </button>
                    ) : null}
                  </div>
                  <p className="field-hint" id="settings-api-key-hint">
                    {willClearKey ? (
                      <>The saved key will be removed when you save.</>
                    ) : keySet ? (
                      <>
                        A key is saved (<code className="inline-code">{maskedHint || '••••'}</code>). The raw value is
                        never shown. Type a new key to replace it.
                      </>
                    ) : (
                      'No key saved. Extraction runs in extractive mode until one is added.'
                    )}
                  </p>
                </div>

                <div className="field-grid">
                  <div className={`field${fieldErrors.baseUrl ? ' field--invalid' : ''}`}>
                    <label className="field-label" htmlFor="settings-base-url">
                      Base URL
                    </label>
                    <input
                      id="settings-base-url"
                      className="input input--mono"
                      type="text"
                      value={baseUrl}
                      onChange={(e) => {
                        setBaseUrl(e.target.value)
                        setSavedNotice(false)
                      }}
                      placeholder={settings.defaults.base_url || 'https://api.openai.com/v1'}
                      spellCheck={false}
                      aria-invalid={!!fieldErrors.baseUrl}
                      aria-describedby="settings-base-url-hint"
                    />
                    {fieldErrors.baseUrl ? (
                      <p className="field-error" id="settings-base-url-hint">
                        {fieldErrors.baseUrl}
                      </p>
                    ) : null}
                  </div>
                  <div className="field">
                    <label className="field-label" htmlFor="settings-model">
                      Model
                    </label>
                    <input
                      id="settings-model"
                      className="input input--mono"
                      type="text"
                      value={model}
                      onChange={(e) => {
                        setModel(e.target.value)
                        setSavedNotice(false)
                      }}
                      placeholder={settings.defaults.model || 'gpt-4o-mini'}
                      spellCheck={false}
                    />
                  </div>
                </div>

                <div className="field">
                  <label className="field-label" htmlFor="settings-vision-model">
                    Vision model <span className="field-optional">optional</span>
                  </label>
                  <input
                    id="settings-vision-model"
                    className="input input--mono"
                    type="text"
                    value={visionModel}
                    onChange={(e) => {
                      setVisionModel(e.target.value)
                      setSavedNotice(false)
                    }}
                    placeholder="same as model"
                    spellCheck={false}
                  />
                  <p className="field-hint">
                    Used to describe video frames when an LLM is available. Blank means the main model.
                  </p>
                </div>

                <div className="field">
                  <label className="field-label" htmlFor="settings-doc-mode">
                    Document mode
                  </label>
                  <select
                    id="settings-doc-mode"
                    className="select"
                    value={docMode}
                    onChange={(e) => {
                      setDocMode(normalizeMode(e.target.value))
                      setSavedNotice(false)
                    }}
                  >
                    {DOC_MODES.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <p className="field-hint">{MODE_HINTS[docMode]}</p>
                </div>

                <div className="field">
                  <span className="field-label">Connection</span>
                  <div className="settings-test-row">
                    <button
                      type="button"
                      className="btn btn-ghost"
                      onClick={() => void handleTest()}
                      disabled={!settings.llm.api_key_set || keyCleared || testing || saving}
                      title={
                        !settings.llm.api_key_set
                          ? 'Save an API key first'
                          : 'Test the saved configuration'
                      }
                    >
                      {testing ? <Spinner size={13} /> : <IconRefresh size={14} />}
                      {testing ? 'Testing…' : 'Test connection'}
                    </button>
                    {testResult ? (
                      <div
                        className={`test-result test-result--${testResult.ok ? 'ok' : 'fail'}`}
                        role="status"
                      >
                        {testResult.ok ? <IconCheck size={13} /> : <IconAlert size={13} />}
                        <span>{testResult.message}</span>
                        {testResult.model ? <code>{testResult.model}</code> : null}
                        {testResult.latency_ms > 0 ? <code>{testResult.latency_ms} ms</code> : null}
                      </div>
                    ) : null}
                    {testError ? (
                      <div className="test-result test-result--fail" role="alert">
                        <IconAlert size={13} />
                        <span>{testError}</span>
                      </div>
                    ) : null}
                  </div>
                  <p className="field-hint">
                    Tests the last saved configuration — save pending changes first.
                  </p>
                </div>

                <div className="settings-note">
                  <IconSparkle size={14} />
                  <div>
                    Works with OpenAI or any OpenAI-compatible endpoint. For local{' '}
                    <strong>Ollama</strong>, use base URL <code className="inline-code">http://localhost:11434/v1</code>{' '}
                    and model <code className="inline-code">llama3.1:8b</code> with any placeholder API key.
                  </div>
                </div>
              </div>
            </section>

            <section className="card settings-card" aria-labelledby="settings-output-title">
              <header className="settings-card-head">
                <div className="settings-card-icon settings-card-icon--orange" aria-hidden="true">
                  <IconFolder size={16} />
                </div>
                <div className="settings-card-copy">
                  <h2 id="settings-output-title">Output</h2>
                  <p>Where documents and frame assets are written.</p>
                </div>
              </header>

              <div className="settings-body">
                <div className={`field${fieldErrors.outputDir ? ' field--invalid' : ''}`}>
                  <label className="field-label" htmlFor="settings-output-dir">
                    Output directory
                  </label>
                  <input
                    id="settings-output-dir"
                    className="input input--mono"
                    type="text"
                    value={outputDir}
                    onChange={(e) => {
                      setOutputDir(e.target.value)
                      setSavedNotice(false)
                    }}
                    placeholder={settings.defaults.output_dir || '/path/to/vault'}
                    spellCheck={false}
                    aria-invalid={!!fieldErrors.outputDir}
                    aria-describedby="settings-output-dir-hint"
                  />
                  {fieldErrors.outputDir ? (
                    <p className="field-error" id="settings-output-dir-hint">
                      {fieldErrors.outputDir}
                    </p>
                  ) : null}
                  <div className="field-input-row field-input-row--end">
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={resetOutputDir}
                      disabled={outputDir.trim() === settings.defaults.output_dir.trim()}
                    >
                      Reset to default
                    </button>
                  </div>
                  <p className="field-hint">
                    Markdown documents plus <code className="inline-code">assets/&lt;video_id&gt;/</code> frames are
                    written here. The folder is created if it doesn’t exist.
                  </p>
                  {settings.defaults.output_dir ? (
                    <p className="field-hint">
                      Default: <code className="inline-code">{settings.defaults.output_dir}</code>
                    </p>
                  ) : null}
                </div>

                <div className="settings-note">
                  <IconFolder size={14} />
                  <div>
                    Existing documents are not moved when the output directory changes — point it somewhere new and
                    future extractions land there.
                  </div>
                </div>
              </div>
            </section>
          </div>

          {formError ? <ErrorBanner message={formError} /> : null}

          {savedNotice ? (
            <div className="settings-saved" role="status">
              <IconCheck size={14} />
              Settings saved.
            </div>
          ) : null}

          <div className="card settings-actions">
            <div className="settings-actions-left">
              <BackLink onBack={onBack} />
            </div>
            <div className="settings-actions-right">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={handleReset}
                disabled={!dirty || savingDisabled}
              >
                Reset changes
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void handleSave()}
                disabled={!dirty || savingDisabled}
              >
                {saving ? <Spinner size={13} /> : <IconCheck size={14} />}
                {saving ? 'Saving…' : 'Save settings'}
              </button>
            </div>
          </div>

          {settings.settings_file ? (
            <div className="settings-meta">
              <span>Stored in</span>
              <code>{settings.settings_file}</code>
            </div>
          ) : null}
        </>
      )}
    </section>
  )
}
