import { useCallback, useRef, useState } from 'react'
import './App.css'

const API = import.meta.env.VITE_API_URL ?? ''

function formatTime(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = (sec % 60).toFixed(1).padStart(4, '0')
  return `${m}:${s}`
}

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

interface Segment {
  index: number
  start: number
  end: number
  duration: number
  filename: string
}

interface ProcessResult {
  duration: number
  mode: string
  segments: Segment[]
  silences: { start: number; end: number }[]
  scene_times: number[]
  merged_path: string | null
}

interface Settings {
  mode: 'silence' | 'scene' | 'both'
  noise_db: number
  min_silence_duration: number
  scene_threshold: number
  silence_padding: number
}

type Phase = 'idle' | 'uploading' | 'processing' | 'done' | 'error'

export default function App() {
  const [file, setFile] = useState<File | null>(null)
  const [uploadId, setUploadId] = useState<string | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState(0)
  const [result, setResult] = useState<ProcessResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [settings, setSettings] = useState<Settings>({
    mode: 'both',
    noise_db: -35,
    min_silence_duration: 0.5,
    scene_threshold: 0.4,
    silence_padding: 0.1,
  })

  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFile = useCallback((f: File) => {
    setFile(f)
    setUploadId(null)
    setJobId(null)
    setResult(null)
    setError(null)
    setPhase('idle')
  }, [])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }, [handleFile])

  const onFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) handleFile(f)
  }, [handleFile])

  const uploadFile = async (f: File): Promise<string> => {
    const fd = new FormData()
    fd.append('file', f)
    const res = await fetch(`${API}/api/upload`, { method: 'POST', body: fd })
    if (!res.ok) throw new Error(`Upload failed: ${res.statusText}`)
    const data = await res.json()
    return data.upload_id
  }

  const startProcess = async (uid: string): Promise<string> => {
    const res = await fetch(`${API}/api/process`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ upload_id: uid, ...settings }),
    })
    if (!res.ok) throw new Error(`Process failed: ${res.statusText}`)
    const data = await res.json()
    return data.job_id
  }

  const watchProgress = (jid: string): Promise<ProcessResult> => {
    return new Promise((resolve, reject) => {
      const es = new EventSource(`${API}/api/progress/${jid}`)
      es.onmessage = (e) => {
        const data = JSON.parse(e.data)
        if (data.progress !== undefined) setProgress(data.progress)
        if (data.status === 'done') {
          es.close()
          resolve(data.result)
        } else if (data.status === 'error') {
          es.close()
          reject(new Error(data.error ?? 'Processing failed'))
        }
      }
      es.onerror = () => { es.close(); reject(new Error('Connection error')) }
    })
  }

  const handleProcess = async () => {
    if (!file) return
    setError(null)
    setProgress(0)

    try {
      let uid = uploadId
      if (!uid) {
        setPhase('uploading')
        uid = await uploadFile(file)
        setUploadId(uid)
      }

      setPhase('processing')
      const jid = await startProcess(uid)
      setJobId(jid)

      const res = await watchProgress(jid)
      setResult(res)
      setPhase('done')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setPhase('error')
    }
  }

  const setSetting = <K extends keyof Settings>(key: K, val: Settings[K]) => {
    setSettings(s => ({ ...s, [key]: val }))
  }

  const totalKept = result
    ? result.segments.reduce((s, seg) => s + seg.duration, 0)
    : 0

  const savedTime = result ? result.duration - totalKept : 0

  return (
    <div className="app">
      <header className="header">
        <span className="header-logo">✂️</span>
        <h1>動画自動カット</h1>
        <span className="header-sub">Silence &amp; Scene Detection</span>
      </header>

      <main className="main">
        {/* Upload */}
        <div className="card">
          <div className="card-title">動画ファイル</div>
          {file ? (
            <div className="upload-file-info">
              <span className="file-icon">🎬</span>
              <span className="file-name">{file.name}</span>
              <span className="file-size">{formatSize(file.size)}</span>
              <button
                className="download-btn"
                onClick={() => { setFile(null); setUploadId(null); setResult(null); setPhase('idle') }}
              >
                変更
              </button>
            </div>
          ) : (
            <div
              className={`upload-zone${dragOver ? ' drag-over' : ''}`}
              onDragOver={e => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDrop}
              onClick={() => fileInputRef.current?.click()}
              tabIndex={0}
              onKeyDown={e => e.key === 'Enter' && fileInputRef.current?.click()}
            >
              <div className="upload-icon">📁</div>
              <div className="upload-title">ファイルをドラッグ&ドロップ</div>
              <div className="upload-sub">または クリックして選択 · MP4, MOV, AVI, MKV, WebM</div>
            </div>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*"
            style={{ display: 'none' }}
            onChange={onFileChange}
          />
        </div>

        {/* Settings */}
        <div className="card">
          <div className="card-title">処理設定</div>
          <div className="settings-grid">
            {/* Mode */}
            <div className="field field-full">
              <label>カットモード</label>
              <div className="mode-buttons">
                {(['silence', 'scene', 'both'] as const).map(m => (
                  <button
                    key={m}
                    className={`mode-btn${settings.mode === m ? ' active' : ''}`}
                    onClick={() => setSetting('mode', m)}
                  >
                    <span className="mode-icon">
                      {m === 'silence' ? '🔇' : m === 'scene' ? '🎬' : '✨'}
                    </span>
                    {m === 'silence' ? '無音カット' : m === 'scene' ? 'シーン分割' : '両方'}
                  </button>
                ))}
              </div>
            </div>

            {/* Silence settings */}
            {settings.mode !== 'scene' && (
              <>
                <div className="field">
                  <label>
                    無音閾値
                    <span>{settings.noise_db} dB</span>
                  </label>
                  <input
                    type="range" min="-60" max="-10" step="1"
                    value={settings.noise_db}
                    onChange={e => setSetting('noise_db', Number(e.target.value))}
                  />
                </div>
                <div className="field">
                  <label>
                    最小無音時間
                    <span>{settings.min_silence_duration} 秒</span>
                  </label>
                  <input
                    type="range" min="0.1" max="3" step="0.1"
                    value={settings.min_silence_duration}
                    onChange={e => setSetting('min_silence_duration', Number(e.target.value))}
                  />
                </div>
                <div className="field">
                  <label>
                    前後の余白
                    <span>{settings.silence_padding} 秒</span>
                  </label>
                  <input
                    type="range" min="0" max="1" step="0.05"
                    value={settings.silence_padding}
                    onChange={e => setSetting('silence_padding', Number(e.target.value))}
                  />
                </div>
              </>
            )}

            {/* Scene settings */}
            {settings.mode !== 'silence' && (
              <div className="field">
                <label>
                  シーン変化感度
                  <span>{settings.scene_threshold}</span>
                </label>
                <input
                  type="range" min="0.1" max="0.9" step="0.05"
                  value={settings.scene_threshold}
                  onChange={e => setSetting('scene_threshold', Number(e.target.value))}
                />
              </div>
            )}
          </div>
        </div>

        {/* Process button */}
        <button
          className="process-btn"
          disabled={!file || phase === 'uploading' || phase === 'processing'}
          onClick={handleProcess}
        >
          {phase === 'uploading' ? 'アップロード中...' :
           phase === 'processing' ? '処理中...' : '▶ 処理開始'}
        </button>

        {/* Progress */}
        {(phase === 'uploading' || phase === 'processing') && (
          <div className="card progress-section">
            <div className="progress-label">
              {phase === 'uploading' ? 'アップロード中' : '動画を解析・処理中'}
            </div>
            <div className="progress-bar-track">
              <div
                className="progress-bar-fill"
                style={{ width: `${phase === 'uploading' ? 10 : progress}%` }}
              />
            </div>
            <div className="progress-dots">
              <span /><span /><span />
            </div>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="error-box">⚠ エラー: {error}</div>
        )}

        {/* Results */}
        {result && phase === 'done' && (
          <div className="card">
            <div className="card-title">処理結果</div>

            {/* Stats */}
            <div className="stats-row">
              <div className="stat-chip">
                <strong>{result.segments.length}</strong>クリップ
              </div>
              <div className="stat-chip">
                <strong>{formatTime(totalKept)}</strong>保持
              </div>
              {savedTime > 1 && (
                <div className="stat-chip">
                  <strong>{formatTime(savedTime)}</strong>カット
                </div>
              )}
              <div className="stat-chip">
                <strong>{Math.round((totalKept / result.duration) * 100)}%</strong>残存率
              </div>
            </div>

            {/* Timeline */}
            {result.duration > 0 && (
              <div className="timeline">
                <div className="timeline-label">タイムライン</div>
                <div className="timeline-track">
                  {result.silences?.map((s, i) => (
                    <div
                      key={i}
                      className="timeline-silence"
                      style={{
                        left: `${(s.start / result.duration) * 100}%`,
                        width: `${((s.end - s.start) / result.duration) * 100}%`,
                      }}
                    />
                  ))}
                  {result.segments.map((seg) => (
                    <div
                      key={seg.index}
                      className="timeline-segment"
                      style={{
                        left: `${(seg.start / result.duration) * 100}%`,
                        width: `${(seg.duration / result.duration) * 100}%`,
                      }}
                      title={`${formatTime(seg.start)} - ${formatTime(seg.end)}`}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* Merged download */}
            {result.merged_path && (
              <a
                className="download-all-btn"
                href={`${API}/api/download/${jobId}/${result.merged_path}`}
                download={result.merged_path}
              >
                ⬇ 統合動画をダウンロード（無音除去済み）
              </a>
            )}

            {/* Segment list */}
            <div style={{ marginTop: 16 }}>
              <div className="card-title" style={{ marginBottom: 10 }}>
                セグメント一覧
              </div>
              <div className="segments-list">
                {result.segments.map(seg => (
                  <div key={seg.index} className="segment-row">
                    <span className="segment-index">#{seg.index + 1}</span>
                    <span className="segment-time">
                      {formatTime(seg.start)} → {formatTime(seg.end)}
                    </span>
                    <span className="segment-duration">{seg.duration.toFixed(1)}s</span>
                    <a
                      className="download-btn"
                      href={`${API}/api/download/${jobId}/${seg.filename}`}
                      download={seg.filename}
                    >
                      ⬇ DL
                    </a>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
