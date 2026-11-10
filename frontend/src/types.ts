// Shared API types — mirror backend/app/schemas.py (v2 lean API).
export interface JobOut {
  id: string
  kind: string | null
  status: 'queued' | 'running' | 'done' | 'error' | string
  total: number
  done: number
  current: string | null
  error: string | null
  created: string | null
  updated: string | null
}

export interface VideoOut {
  video_id: string
  url: string | null
  title: string | null
  channel: string | null
  channel_id: string | null
  duration: number | null
  published: string | null
  thumbnail: string | null
  wiki_path: string | null
  transcript_source: string | null
  frame_count: number
  words: number
  reading_minutes: number
  status: string | null
  error: string | null
  added: string | null
  extracted: string | null
}

export interface ChannelOut {
  channel: string | null
  channel_id: string | null
  video_count: number
  last_added: string | null
}

export interface SourcesOut {
  videos: VideoOut[]
  channels: ChannelOut[]
  total: number
}

export interface ConfigOut {
  llm: boolean
  wiki_root: string
  videos: number
}

export interface IngestResponse {
  job_id: string
  accepted: number
  duplicates: string[]
  rejected: string[]
}

export interface DedupeOut {
  exists: boolean
  video: VideoOut | null
}
