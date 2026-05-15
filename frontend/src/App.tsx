import { useCallback, useRef, useState } from 'react'
import './App.css'

const API = import.meta.env.VITE_API_URL ?? ''

const fmt = (sec: number) => {
  const m = Math.floor(sec / 60)
  const s = (sec % 60).toFixed(1).padStart(4, '0')
  return `${m}:${s}`
}
const fmtSize = (b: number) => b < 1e6 ? `${(b / 1024).toFixed(0)} KB` : `${(b / 1e6).toFixed(1)} MB`

interface FileInfo { id: string; name: string; size: number }

interface Settings {
  doSilenceCut: boolean
  noiseDb: number
  minSilenceDuration: number
  silencePadding: number
  bgmId: string | null
  bgmName: string | null
  bgmVolume: number
  doSubtitles: boolean
  subtitleLanguage: string
  burnSubs: boolean
  doThumbnail: boolean
  thumbnailTitle: string
  thumbnailTimestamp: number
}

interface ProcessResult {
  duration: number
  files: Record<string, string>
  silences: { start: number; end: number }[]
  keep_segments: { start: number; end: number }[]
}

type Phase = 'idle' | 'uploading' | 'processing' | 'done' | 'error'

const DEFAULT: Settings = {
  doSilenceCut: true,
  noiseDb: -35,
  minSilenceDuration: 0.5,
  silencePadding: 0.1,
  bgmId: null,
  bgmName: null,
  bgmVolume: 0.15,
  doSubtitles: false,
  subtitleLanguage: 'ja',
  burnSubs: false,
  doThumbnail: false,
  thumbnailTitle: '',
  thumbnailTimestamp: 5,
}

export default function App() {
  const [video, setVideo] = useState<FileInfo | null>(null)
  const [settings, setSettings] = useState<Settings>(DEFAULT)
  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState(0)
  const [message, setMessage] = useState('')
  const [result, setResult] = useState<ProcessResult | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const videoInputRef = useRef<HTMLInputElement>(null)
  const bgmInputRef = useRef<HTMLInputElement>(null)

  const set = <K extends keyof Settings>(k: K, v: Settings[K]) =>
    setSettings(s => ({ ...s, [k]: v }))

  // ── Upload helpers ──
  const uploadFile = async (file: File, endpoint: string): Promise<string> => {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`${API}${endpoint}`, { method: 'POST', body: fd })
    if (!res.ok) throw new Error(`Upload failed: ${res.statusText}`)
    return (await res.json()).upload_id
  }

  const handleVideoFile = useCallback(async (file: File) => {
    setPhase('uploading')
    setError(null)
    setResult(null)
    try {
      const id = await uploadFile(file, '/api/upload/video')
      setVideo({ id, name: file.name, size: file.size })
      setPhase('idle')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setPhase('error')
    }
  }, [])

  const handleBgmFile = async (file: File) => {
    try {
      const id = await uploadFile(file, '/api/upload/bgm')
      set('bgmId', id)
      set('bgmName', file.name)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const f = e.dataTransfer.files[0]
    if (f) handleVideoFile(f)
  }, [handleVideoFile])

  // ── Processing ──
  const handleProcess = async () => {
    if (!video) return
    setError(null)
    setProgress(0)
    setMessage('開始中...')
    setPhase('processing')

    try {
      const body = {
        upload_id: video.id,
        do_silence_cut: settings.doSilenceCut,
        noise_db: settings.noiseDb,
        min_silence_duration: settings.minSilenceDuration,
        silence_padding: settings.silencePadding,
        bgm_upload_id: settings.bgmId,
        bgm_volume: settings.bgmVolume,
        do_subtitles: settings.doSubtitles,
        subtitle_language: settings.subtitleLanguage,
        burn_subs: settings.burnSubs,
        do_thumbnail: settings.doThumbnail,
        thumbnail_title: settings.thumbnailTitle,
        thumbnail_timestamp: settings.thumbnailTimestamp,
      }
      const res = await fetch(`${API}/api/process`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(await res.text())
      const { job_id } = await res.json()
      setJobId(job_id)

      await new Promise<void>((resolve, reject) => {
        const es = new EventSource(`${API}/api/progress/${job_id}`)
        es.onmessage = (e) => {
          const data = JSON.parse(e.data)
          if (data.progress !== undefined) setProgress(data.progress)
          if (data.message) setMessage(data.message)
          if (data.status === 'done') { es.close(); setResult(data.result); resolve() }
          else if (data.status === 'error') { es.close(); reject(new Error(data.error)) }
        }
        es.onerror = () => { es.close(); reject(new Error('接続エラー')) }
      })
      setPhase('done')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setPhase('error')
    }
  }

  const downloadUrl = (filename: string) => `${API}/api/download/${jobId}/${filename}`

  const totalKept = result?.keep_segments?.reduce((s, seg) => s + (seg.end - seg.start), 0) ?? 0
  const saved = result ? result.duration - totalKept : 0

  return (
    <div className="app">
      <header className="header">
        <span className="header-logo">🎬</span>
        <h1>動画自動編集</h1>
        <span className="header-badge">YouTube実況・Vlog対応</span>
      </header>

      <main className="main">

        {/* ── Video Upload ── */}
        <div className="card">
          <div className="card-header">
            <span className="card-icon">📁</span>
            <span className="card-title">動画ファイル</span>
          </div>
          {video ? (
            <div className="file-chip">
              <span className="file-icon">🎥</span>
              <span className="file-name">{video.name}</span>
              <span className="file-meta">{fmtSize(video.size)}</span>
              <button className="clear-btn" onClick={() => { setVideo(null); setResult(null); setPhase('idle') }}>✕</button>
            </div>
          ) : (
            <div
              className={`upload-zone${dragOver ? ' drag-over' : ''}`}
              onDragOver={e => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => videoInputRef.current?.click()}
              tabIndex={0}
              onKeyDown={e => e.key === 'Enter' && videoInputRef.current?.click()}
            >
              <div className="upload-icon">📂</div>
              <div className="upload-title">動画をドラッグ＆ドロップ</div>
              <div className="upload-sub">MP4・MOV・AVI・MKV・WebM</div>
            </div>
          )}
          <input ref={videoInputRef} type="file" accept="video/*" style={{ display: 'none' }}
            onChange={e => { const f = e.target.files?.[0]; if (f) handleVideoFile(f) }} />
        </div>

        {/* ── Silence Cut ── */}
        <div className={`card${!settings.doSilenceCut ? ' disabled' : ''}`}>
          <div className="card-header">
            <span className="card-icon">✂️</span>
            <span className="card-title">無音カット</span>
            <label className="card-toggle">
              <input type="checkbox" checked={settings.doSilenceCut}
                onChange={e => set('doSilenceCut', e.target.checked)} />
              <span className="toggle-track" />
            </label>
          </div>
          <div className="settings-grid">
            <div className="fields-row">
              <div className="field">
                <label>無音閾値 <span className="val">{settings.noiseDb} dB</span></label>
                <input type="range" min="-60" max="-10" step="1"
                  value={settings.noiseDb} onChange={e => set('noiseDb', +e.target.value)} />
              </div>
              <div className="field">
                <label>最小無音時間 <span className="val">{settings.minSilenceDuration}s</span></label>
                <input type="range" min="0.1" max="3" step="0.1"
                  value={settings.minSilenceDuration} onChange={e => set('minSilenceDuration', +e.target.value)} />
              </div>
            </div>
            <div className="field">
              <label>前後の余白 <span className="val">{settings.silencePadding}s</span></label>
              <input type="range" min="0" max="1" step="0.05"
                value={settings.silencePadding} onChange={e => set('silencePadding', +e.target.value)} />
            </div>
          </div>
        </div>

        {/* ── BGM ── */}
        <div className="card">
          <div className="card-header">
            <span className="card-icon">🎵</span>
            <span className="card-title">BGM挿入</span>
          </div>
          {settings.bgmName ? (
            <div className="file-chip" style={{ marginBottom: 12 }}>
              <span className="file-icon">🎶</span>
              <span className="file-name">{settings.bgmName}</span>
              <button className="clear-btn" onClick={() => { set('bgmId', null); set('bgmName', null) }}>✕</button>
            </div>
          ) : (
            <button className="upload-zone" style={{ width: '100%', marginBottom: 12, padding: '20px' }}
              onClick={() => bgmInputRef.current?.click()}>
              <div style={{ fontSize: 12, color: '#7878a0' }}>＋ BGMファイルを選択（MP3・WAV・AAC）</div>
            </button>
          )}
          <input ref={bgmInputRef} type="file" accept="audio/*" style={{ display: 'none' }}
            onChange={e => { const f = e.target.files?.[0]; if (f) handleBgmFile(f) }} />
          <div className="field">
            <label>BGM音量（動画音声に対する割合）<span className="val">{Math.round(settings.bgmVolume * 100)}%</span></label>
            <input type="range" min="0.01" max="0.5" step="0.01"
              value={settings.bgmVolume} onChange={e => set('bgmVolume', +e.target.value)}
              disabled={!settings.bgmId} />
          </div>
        </div>

        {/* ── Subtitles ── */}
        <div className={`card${!settings.doSubtitles ? ' disabled' : ''}`}>
          <div className="card-header">
            <span className="card-icon">💬</span>
            <span className="card-title">字幕自動生成（Whisper）</span>
            <label className="card-toggle">
              <input type="checkbox" checked={settings.doSubtitles}
                onChange={e => set('doSubtitles', e.target.checked)} />
              <span className="toggle-track" />
            </label>
          </div>
          <div className="fields-row">
            <div className="field">
              <label>言語</label>
              <select value={settings.subtitleLanguage} onChange={e => set('subtitleLanguage', e.target.value)}>
                <option value="ja">日本語</option>
                <option value="en">英語</option>
                <option value="zh">中国語</option>
                <option value="ko">韓国語</option>
              </select>
            </div>
            <div className="field" style={{ justifyContent: 'flex-end' }}>
              <label style={{ cursor: 'pointer', userSelect: 'none' }}>
                <input type="checkbox" checked={settings.burnSubs}
                  onChange={e => set('burnSubs', e.target.checked)} style={{ marginRight: 6 }} />
                動画に字幕を焼き込む
              </label>
            </div>
          </div>
        </div>

        {/* ── Thumbnail ── */}
        <div className={`card${!settings.doThumbnail ? ' disabled' : ''}`}>
          <div className="card-header">
            <span className="card-icon">🖼️</span>
            <span className="card-title">サムネイル生成</span>
            <label className="card-toggle">
              <input type="checkbox" checked={settings.doThumbnail}
                onChange={e => set('doThumbnail', e.target.checked)} />
              <span className="toggle-track" />
            </label>
          </div>
          <div className="settings-grid">
            <div className="field">
              <label>タイトルテキスト</label>
              <input type="text" placeholder="例: 【衝撃】最強武器で無双してみた" maxLength={60}
                value={settings.thumbnailTitle} onChange={e => set('thumbnailTitle', e.target.value)} />
            </div>
            <div className="field">
              <label>フレーム取得位置 <span className="val">{settings.thumbnailTimestamp}s</span></label>
              <input type="range" min="1" max="60" step="0.5"
                value={settings.thumbnailTimestamp} onChange={e => set('thumbnailTimestamp', +e.target.value)} />
            </div>
          </div>
        </div>

        {/* ── Process Button ── */}
        <button className="process-btn"
          disabled={!video || phase === 'uploading' || phase === 'processing'}
          onClick={handleProcess}>
          {phase === 'uploading' ? 'アップロード中...' :
           phase === 'processing' ? '編集処理中...' : '▶ 自動編集を開始'}
        </button>

        {/* ── Progress ── */}
        {phase === 'processing' && (
          <div className="card progress-card">
            <div className="progress-step">{message}</div>
            <div className="progress-track">
              <div className="progress-fill" style={{ width: `${progress}%` }} />
            </div>
            <div className="progress-pct">{progress}%</div>
          </div>
        )}

        {/* ── Error ── */}
        {error && <div className="error-box">⚠ {error}</div>}

        {/* ── Result ── */}
        {result && phase === 'done' && (
          <div className="card">
            <div className="card-header">
              <span className="card-icon">✅</span>
              <span className="card-title">編集完了</span>
            </div>

            {/* Stats */}
            <div className="stats-row">
              <div className="stat">
                <strong>{fmt(result.duration)}</strong>
                <span>元の長さ</span>
              </div>
              {settings.doSilenceCut && (
                <>
                  <div className="stat">
                    <strong>{fmt(totalKept)}</strong>
                    <span>カット後</span>
                  </div>
                  <div className="stat">
                    <strong>-{fmt(saved)}</strong>
                    <span>削除した無音</span>
                  </div>
                  <div className="stat">
                    <strong>{Math.round((totalKept / result.duration) * 100)}%</strong>
                    <span>残存率</span>
                  </div>
                </>
              )}
            </div>

            {/* Timeline */}
            {result.duration > 0 && (
              <div className="timeline">
                <div className="timeline-label">タイムライン（紫＝保持、暗＝カット）</div>
                <div className="timeline-track">
                  {result.silences?.map((s, i) => (
                    <div key={i} className="tl-silence" style={{
                      left: `${(s.start / result.duration) * 100}%`,
                      width: `${((s.end - s.start) / result.duration) * 100}%`,
                    }} />
                  ))}
                  {result.keep_segments?.map((seg, i) => (
                    <div key={i} className="tl-keep" style={{
                      left: `${(seg.start / result.duration) * 100}%`,
                      width: `${((seg.end - seg.start) / result.duration) * 100}%`,
                    }} title={`${fmt(seg.start)} → ${fmt(seg.end)}`} />
                  ))}
                </div>
              </div>
            )}

            {/* Download buttons */}
            <div className="download-grid">
              {result.files.output && (
                <a className="dl-btn" href={downloadUrl(result.files.output)} download>
                  <span className="dl-icon">🎬</span>
                  <span className="dl-label">完成動画<br/>ダウンロード</span>
                </a>
              )}
              {result.files.srt && (
                <a className="dl-btn" href={downloadUrl(result.files.srt)} download="subtitles.srt">
                  <span className="dl-icon">💬</span>
                  <span className="dl-label">字幕ファイル<br/>(.srt)</span>
                </a>
              )}
              {result.files.thumbnail && (
                <a className="dl-btn" href={downloadUrl(result.files.thumbnail)} download="thumbnail.jpg">
                  <span className="dl-icon">🖼️</span>
                  <span className="dl-label">サムネイル<br/>ダウンロード</span>
                </a>
              )}
            </div>

            {/* Thumbnail preview */}
            {result.files.thumbnail && (
              <div className="thumbnail-preview">
                <img src={downloadUrl(result.files.thumbnail)} alt="サムネイルプレビュー" />
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  )
}
