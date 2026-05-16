import { useCallback, useRef, useState } from 'react'
import './App.css'

const API = import.meta.env.VITE_API_URL ?? ''

const fmtSize = (b: number) => b < 1e6 ? `${(b / 1024).toFixed(0)} KB` : `${(b / 1e6).toFixed(1)} MB`

interface FileInfo { id: string; name: string; size: number }

interface Clip {
  index: number
  filename: string
  start: number
  duration: number
  srt: string | null
}

interface ProcessResult {
  duration: number
  clips: Clip[]
  energy: [number, number][]
}

type Phase = 'idle' | 'uploading' | 'processing' | 'done' | 'error'

const DURATIONS = [15, 30, 60]

export default function App() {
  const [video, setVideo] = useState<FileInfo | null>(null)
  const [bgm, setBgm] = useState<FileInfo | null>(null)
  const [numClips, setNumClips] = useState(3)
  const [clipDuration, setClipDuration] = useState(60)
  const [doTelop, setDoTelop] = useState(true)
  const [telopLang, setTelopLang] = useState('ja')
  const [bgmVolume, setBgmVolume] = useState(0.2)
  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState(0)
  const [message, setMessage] = useState('')
  const [result, setResult] = useState<ProcessResult | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)

  const videoRef = useRef<HTMLInputElement>(null)
  const bgmRef = useRef<HTMLInputElement>(null)

  const uploadFile = async (file: File, endpoint: string) => {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`${API}${endpoint}`, { method: 'POST', body: fd })
    if (!res.ok) throw new Error(`アップロード失敗: ${res.statusText}`)
    return await res.json()
  }

  const handleVideo = useCallback(async (file: File) => {
    setError(null); setResult(null); setPhase('uploading')
    try {
      const data = await uploadFile(file, '/api/upload/video')
      setVideo({ id: data.upload_id, name: file.name, size: file.size })
      setPhase('idle')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e)); setPhase('error')
    }
  }, [])

  const handleBgm = async (file: File) => {
    try {
      const data = await uploadFile(file, '/api/upload/bgm')
      setBgm({ id: data.upload_id, name: file.name, size: file.size })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault(); setDragOver(false)
    const f = e.dataTransfer.files[0]
    if (f) handleVideo(f)
  }, [handleVideo])

  const handleProcess = async () => {
    if (!video) return
    setError(null); setProgress(0); setMessage('開始中...'); setPhase('processing')
    try {
      const res = await fetch(`${API}/api/process`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          upload_id: video.id,
          num_clips: numClips,
          clip_duration: clipDuration,
          do_telop: doTelop,
          telop_language: telopLang,
          bgm_upload_id: bgm?.id ?? null,
          bgm_volume: bgmVolume,
        }),
      })
      if (!res.ok) throw new Error(await res.text())
      const { job_id } = await res.json()
      setJobId(job_id)

      await new Promise<void>((resolve, reject) => {
        const es = new EventSource(`${API}/api/progress/${job_id}`)
        es.onmessage = e => {
          const d = JSON.parse(e.data)
          if (d.progress !== undefined) setProgress(d.progress)
          if (d.message) setMessage(d.message)
          if (d.status === 'done') { es.close(); setResult(d.result); resolve() }
          else if (d.status === 'error') { es.close(); reject(new Error(d.error)) }
        }
        es.onerror = () => { es.close(); reject(new Error('接続エラー')) }
      })
      setPhase('done')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e)); setPhase('error')
    }
  }

  const dlUrl = (filename: string) => `${API}/api/download/${jobId}/${filename}`

  // Energy waveform bars
  const energyBars = result?.energy?.map(([, rms]) => rms) ?? []
  const eMin = Math.min(...energyBars, -60)
  const eMax = Math.max(...energyBars, -10)
  const eRange = eMax - eMin || 1

  return (
    <div className="app">
      <header className="header">
        <span className="header-logo">⚡</span>
        <h1>Shorts ジェネレーター</h1>
        <div className="header-platform">
          <span className="yt-icon">▶</span>
          YouTube Shorts
        </div>
      </header>

      <main className="main">

        {/* ── 動画アップロード ── */}
        <div className="card">
          <div className="card-header">
            <span className="card-icon">🎬</span>
            <span className="card-title">元動画（長尺）</span>
          </div>
          {video ? (
            <div className="file-chip">
              <span className="fi">🎥</span>
              <span className="fn">{video.name}</span>
              <span className="fm">{fmtSize(video.size)}</span>
              <button className="rm-btn" onClick={() => { setVideo(null); setResult(null); setPhase('idle') }}>✕</button>
            </div>
          ) : (
            <button
              className={`upload-zone${dragOver ? ' drag-over' : ''}`}
              onDragOver={e => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => videoRef.current?.click()}
            >
              <div className="upload-icon">📹</div>
              <div className="upload-title">動画をドラッグ＆ドロップ</div>
              <div className="upload-sub">MP4・MOV・AVI・MKV・WebM に対応</div>
            </button>
          )}
          <input ref={videoRef} type="file" accept="video/*" style={{ display: 'none' }}
            onChange={e => { const f = e.target.files?.[0]; if (f) handleVideo(f) }} />
        </div>

        {/* ── 本数・秒数 ── */}
        <div className="card">
          <div className="card-header">
            <span className="card-icon">📊</span>
            <span className="card-title">生成するショート動画</span>
          </div>
          <div className="settings-stack">
            <div className="field">
              <label>本数</label>
              <div className="count-chips">
                {[1, 2, 3, 5].map(n => (
                  <button key={n} className={`count-chip${numClips === n ? ' active' : ''}`}
                    onClick={() => setNumClips(n)}>{n}本</button>
                ))}
              </div>
            </div>
            <div className="field">
              <label>長さ（最大60秒）</label>
              <div className="dur-chips">
                {DURATIONS.map(d => (
                  <button key={d} className={`dur-chip${clipDuration === d ? ' active' : ''}`}
                    onClick={() => setClipDuration(d)}>
                    <span>{d}</span>秒
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* ── テロップ ── */}
        <div className={`card${!doTelop ? ' disabled' : ''}`}>
          <div className="card-header">
            <span className="card-icon">💬</span>
            <span className="card-title">テロップ自動生成（Whisper AI）</span>
            <div className="toggle-wrap">
              <label className="toggle">
                <input type="checkbox" checked={doTelop} onChange={e => setDoTelop(e.target.checked)} />
                <span className="toggle-track" />
              </label>
            </div>
          </div>
          <div className="field">
            <label>言語</label>
            <select value={telopLang} onChange={e => setTelopLang(e.target.value)}>
              <option value="ja">日本語</option>
              <option value="en">英語</option>
              <option value="zh">中国語</option>
              <option value="ko">韓国語</option>
            </select>
          </div>
        </div>

        {/* ── BGM ── */}
        <div className="card">
          <div className="card-header">
            <span className="card-icon">🎵</span>
            <span className="card-title">BGM</span>
          </div>
          {bgm ? (
            <div className="file-chip" style={{ marginBottom: 12 }}>
              <span className="fi">🎶</span>
              <span className="fn">{bgm.name}</span>
              <button className="rm-btn" onClick={() => setBgm(null)}>✕</button>
            </div>
          ) : (
            <button className="bgm-upload-btn" style={{ marginBottom: 12 }}
              onClick={() => bgmRef.current?.click()}>
              ＋ BGMファイルを追加（MP3・WAV・AAC）
            </button>
          )}
          <input ref={bgmRef} type="file" accept="audio/*" style={{ display: 'none' }}
            onChange={e => { const f = e.target.files?.[0]; if (f) handleBgm(f) }} />
          {bgm && (
            <div className="field">
              <label>BGM音量 <span className="val">{Math.round(bgmVolume * 100)}%</span></label>
              <input type="range" min="0.01" max="0.5" step="0.01"
                value={bgmVolume} onChange={e => setBgmVolume(+e.target.value)} />
            </div>
          )}
        </div>

        {/* ── 処理開始 ── */}
        <button className="process-btn"
          disabled={!video || phase === 'uploading' || phase === 'processing'}
          onClick={handleProcess}>
          {phase === 'uploading' ? 'アップロード中...' :
           phase === 'processing' ? `ショート動画を生成中...` :
           `⚡ ${numClips}本のショート動画を自動生成`}
        </button>

        {/* ── 進捗 ── */}
        {phase === 'processing' && (
          <div className="card progress-card">
            <div className="progress-msg">{message}</div>
            <div className="progress-track">
              <div className="progress-fill" style={{ width: `${progress}%` }} />
            </div>
            <div className="progress-pct">{progress}%</div>
          </div>
        )}

        {/* ── エラー ── */}
        {error && <div className="error-box">⚠ {error}</div>}

        {/* ── 結果 ── */}
        {result && phase === 'done' && (
          <div className="card">
            <div className="result-header">
              <span style={{ fontSize: 20 }}>🎉</span>
              <span className="result-title">生成完了</span>
              <span className="result-count">{result.clips.length}本</span>
            </div>

            {/* Energy waveform */}
            {energyBars.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 11, color: '#4a4a62', marginBottom: 5 }}>
                  音声エネルギー（ハイライト検出に使用）
                </div>
                <div className="waveform">
                  {energyBars.map((e, i) => (
                    <div key={i} className="wf-bar"
                      style={{ height: `${((e - eMin) / eRange) * 100}%` }} />
                  ))}
                </div>
              </div>
            )}

            {/* Clips grid */}
            <div className="clips-grid">
              {result.clips.map(clip => (
                <div key={clip.index} className="clip-card">
                  <div className="clip-thumb">
                    <span className="thumb-icon">📱</span>
                    <span style={{ fontSize: 12 }}>9:16 · {clip.duration}秒</span>
                    <span className="clip-num">#{clip.index + 1}</span>
                    <span className="clip-badge">{clip.duration}s</span>
                  </div>
                  <div className="clip-body">
                    <div className="clip-meta">
                      元動画 <span>{Math.floor(clip.start / 60)}:{String(Math.floor(clip.start % 60)).padStart(2, '0')}</span> 〜
                    </div>
                    <div className="clip-actions">
                      <a className="dl-btn primary" href={dlUrl(clip.filename)} download={clip.filename}>
                        ⬇ ショート動画をDL
                      </a>
                      {clip.srt && (
                        <a className="dl-btn" href={dlUrl(clip.srt)} download={clip.srt}>
                          💬 字幕(.srt)をDL
                        </a>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
