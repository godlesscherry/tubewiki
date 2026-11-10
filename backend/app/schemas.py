"""Pydantic request/response models."""
from __future__ import annotations

from pydantic import BaseModel, Field


class JobOut(BaseModel):
    id: str
    kind: str | None = None
    status: str
    total: int = 0
    done: int = 0
    current: str | None = None
    error: str | None = None
    created: str | None = None
    updated: str | None = None


class IngestRequest(BaseModel):
    urls: list[str] = Field(min_length=1)
    force: bool = False


class IngestResponse(BaseModel):
    job_id: str
    accepted: int
    duplicates: list[str] = []
    rejected: list[str] = []


class VideoOut(BaseModel):
    video_id: str
    url: str | None = None
    title: str | None = None
    channel: str | None = None
    channel_id: str | None = None
    duration: int | None = None
    published: str | None = None
    thumbnail: str | None = None
    wiki_path: str | None = None
    transcript_source: str | None = None
    frame_count: int = 0
    words: int = 0
    reading_minutes: int = 0
    status: str | None = None
    error: str | None = None
    added: str | None = None
    extracted: str | None = None


class ChannelOut(BaseModel):
    channel: str | None = None
    channel_id: str | None = None
    video_count: int = 0
    last_added: str | None = None


class SourcesOut(BaseModel):
    videos: list[VideoOut] = []
    channels: list[ChannelOut] = []
    total: int = 0


class DedupeOut(BaseModel):
    exists: bool = False
    video: VideoOut | None = None


class ConfigOut(BaseModel):
    llm: bool
    wiki_root: str
    videos: int
