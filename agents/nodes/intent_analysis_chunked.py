"""Chunked intent analysis node for large PRs.

This node is a degraded mode for corner-case PRs with too many changed files or
very large diffs. It avoids per-file LLM calls by:
1) grouping files by path depth=2,
2) chunking diffs by size,
3) selecting top-k high-signal chunks,
4) running intent analysis on diff-only chunks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field

from agents.prompts import render_prompt_template
from core.state import FileAnalysis, ReviewState
from util.diff_utils import extract_file_diff, parse_diff_with_line_numbers
from util.json_utils import extract_json_from_text
from util.runtime_utils import elapsed_seconds, elapsed_tag

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    v = (os.getenv(name) or "").strip()
    if not v:
        return int(default)
    try:
        return int(v)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    v = (os.getenv(name) or "").strip()
    if not v:
        return float(default)
    try:
        return float(v)
    except Exception:
        return float(default)


def _normalize_path(p: str) -> str:
    s = (p or "").strip()
    if s.startswith("a/") or s.startswith("b/"):
        s = s[2:]
    if s.startswith("/"):
        s = s[1:]
    return s


def _group_key_depth2(file_path: str) -> str:
    parts = [p for p in _normalize_path(file_path).split("/") if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "/".join(parts[:2])


_STRONG_DANGER_RE = re.compile(
    r"\b(eval|exec|token|secret|permission|acl|authorize|authorization|innerHTML|dangerouslySetInnerHTML)\b",
    re.IGNORECASE,
)

_DANGER_RE = re.compile(
    r"\b("
    r"auth|permission|acl|scope|role|token|secret|csrf|oauth|jwt|signature|"
    r"sql|select\s|insert\s|update\s|delete\s|"
    r"deserialize|pickle|yaml\.load|subprocess|shell|cmd|"
    r"async|await|promise|thread|lock|transaction|retry|queue|task|cron|"
    r"cache|session|cookie|feature\s*flag|singleton|global"
    r")\b",
    re.IGNORECASE,
)


def _file_type_weight(file_path: str) -> float:
    p = _normalize_path(file_path).lower()
    if "/test" in p or p.startswith("tests/") or p.endswith("_test.py") or p.endswith(".spec.ts") or p.endswith(".spec.tsx"):
        return 0.4
    if p.endswith((".md", ".rst", ".txt")):
        return 0.2
    if p.endswith((".yml", ".yaml", ".json", ".toml", ".ini", ".cfg")):
        return 0.6
    return 1.0


def _public_api_delta(diff_text: str) -> int:
    if not diff_text:
        return 0
    hits = 0
    for line in diff_text.splitlines():
        if not line.startswith("+"):
            continue
        s = line[1:]
        if re.search(r"\b(export|public|def|class|interface|type)\b", s):
            hits += 1
            if hits >= 6:
                break
    return hits


def _count_diff_danger_hits(diff_text: str) -> Tuple[int, bool]:
    if not diff_text:
        return (0, False)
    strong = bool(_STRONG_DANGER_RE.search(diff_text))
    hits = len(_DANGER_RE.findall(diff_text))
    return (hits, strong)


@dataclass(frozen=True)
class FileEntry:
    file_path: str
    group_key: str
    diff_text: str
    changed_lines: int
    diff_chars: int
    danger_hits: int
    strong_danger: bool
    public_api_delta_hits: int
    type_weight: float

    @property
    def score(self) -> float:
        churn = math.log1p(max(0, self.changed_lines))
        danger = min(6, self.danger_hits)
        api = min(6, self.public_api_delta_hits)
        base = 2.0 * churn + 0.6 * api + 0.9 * danger
        if self.strong_danger:
            base += 4.0
        return base * float(self.type_weight)


@dataclass
class Chunk:
    chunk_id: str
    group_key: str
    files: List[FileEntry]
    chunk_diff: str
    diff_chars: int
    changed_lines: int
    score: float
    must_include: bool


class ChunkedIntentResponse(BaseModel):
    file_analyses: List[FileAnalysis] = Field(default_factory=list)


def _build_file_entries(diff_context: str, changed_files: Sequence[str]) -> List[FileEntry]:
    # Parse diff once for line-number-based stats.
    contexts_raw = parse_diff_with_line_numbers(diff_context)
    contexts = {_normalize_path(k): v for k, v in contexts_raw.items()}

    entries: List[FileEntry] = []
    for fp in changed_files:
        norm = _normalize_path(fp)
        ctx = contexts.get(norm)
        changed_lines = 0
        if ctx is not None:
            try:
                changed_lines = len(getattr(ctx, "added_lines", set())) + len(getattr(ctx, "modified_lines", set()))
            except Exception:
                changed_lines = 0

        diff_text = extract_file_diff(diff_context, fp) or ""
        diff_chars = len(diff_text)
        danger_hits, strong = _count_diff_danger_hits(diff_text)
        api_hits = _public_api_delta(diff_text)
        entries.append(
            FileEntry(
                file_path=fp,
                group_key=_group_key_depth2(fp),
                diff_text=diff_text,
                changed_lines=int(changed_lines),
                diff_chars=int(diff_chars),
                danger_hits=int(danger_hits),
                strong_danger=bool(strong),
                public_api_delta_hits=int(api_hits),
                type_weight=float(_file_type_weight(fp)),
            )
        )
    return entries


def _format_files_list(paths: Sequence[str]) -> str:
    return "\n".join(f"- {p}" for p in paths)


def _pack_chunks(
    files: Sequence[FileEntry],
    *,
    max_chunk_chars: int,
    max_file_diff_chars: int,
) -> List[Chunk]:
    groups: Dict[str, List[FileEntry]] = {}
    for e in files:
        groups.setdefault(e.group_key, []).append(e)

    chunks: List[Chunk] = []
    for group_key, group_files in sorted(groups.items(), key=lambda kv: kv[0]):
        # Sort by file score within group so important files appear early when chunk is truncated.
        ordered = sorted(group_files, key=lambda f: (-f.score, -f.changed_lines, f.file_path))
        buf: List[FileEntry] = []
        buf_texts: List[str] = []
        buf_chars = 0
        idx = 0

        def flush() -> None:
            nonlocal idx, buf, buf_texts, buf_chars
            if not buf:
                return
            idx += 1
            chunk_id = f"{group_key}:{idx}"
            chunk_diff = "\n".join(buf_texts).strip()
            total_chars = len(chunk_diff)
            total_changed = sum(x.changed_lines for x in buf)
            total_score = sum(x.score for x in buf)
            must = any(x.strong_danger for x in buf)
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    group_key=group_key,
                    files=list(buf),
                    chunk_diff=chunk_diff,
                    diff_chars=int(total_chars),
                    changed_lines=int(total_changed),
                    score=float(total_score),
                    must_include=bool(must),
                )
            )
            buf = []
            buf_texts = []
            buf_chars = 0

        for e in ordered:
            raw = e.diff_text or ""
            if max_file_diff_chars > 0 and len(raw) > max_file_diff_chars:
                raw = raw[:max_file_diff_chars] + "\n...[truncated]..."
            section = f"=== FILE: {_normalize_path(e.file_path)} ===\n{raw}\n"
            section_chars = len(section)

            # If a single file exceeds max chunk size, it becomes its own chunk (still truncated).
            if not buf and section_chars > max_chunk_chars:
                buf.append(e)
                buf_texts.append(section[:max_chunk_chars] + "\n...[chunk-truncated]...")
                flush()
                continue

            if buf and (buf_chars + section_chars) > max_chunk_chars:
                flush()

            buf.append(e)
            buf_texts.append(section)
            buf_chars += section_chars

        flush()

    return chunks


def _select_topk_chunks(chunks: Sequence[Chunk]) -> Tuple[List[Chunk], List[Chunk]]:
    if not chunks:
        return ([], [])

    disable_topk_below = _env_int("INTENT_CHUNK_TOPK_DISABLE_BELOW", 4)
    if disable_topk_below > 0 and len(chunks) < int(disable_topk_below):
        # For small numbers of chunks, don't apply Top-K pruning to avoid unnecessary coverage loss.
        selected_all = sorted(chunks, key=lambda c: (-c.must_include, -c.score, c.chunk_id))
        return (selected_all, [])

    topk_max = _env_int("INTENT_CHUNK_TOPK_MAX", 10)
    topk_ratio = _env_float("INTENT_CHUNK_TOPK_RATIO", 0.3)
    min_k = _env_int("INTENT_CHUNK_TOPK_MIN", 4)
    sentinel = _env_int("INTENT_CHUNK_SENTINEL_SAMPLE", 0)

    n = len(chunks)
    k = int(math.ceil(float(n) * float(topk_ratio)))
    k = max(int(min_k), k)
    if topk_max > 0:
        k = min(int(topk_max), k)
    k = min(n, k)

    must = [c for c in chunks if c.must_include]
    rest = [c for c in chunks if not c.must_include]
    rest_sorted = sorted(rest, key=lambda c: (-c.score, -c.changed_lines, c.chunk_id))

    selected: List[Chunk] = []
    seen: set[str] = set()
    for c in must:
        if c.chunk_id in seen:
            continue
        selected.append(c)
        seen.add(c.chunk_id)

    for c in rest_sorted:
        if len(selected) >= k:
            break
        if c.chunk_id in seen:
            continue
        selected.append(c)
        seen.add(c.chunk_id)

    remaining = [c for c in chunks if c.chunk_id not in seen]

    if sentinel and remaining:
        # Optional "sentinel" sample from the tail to reduce blind spots.
        rnd = random.Random(_env_int("INTENT_CHUNK_SENTINEL_SEED", 1))
        pick = rnd.choice(remaining)
        selected.append(pick)
        seen.add(pick.chunk_id)
        remaining = [c for c in chunks if c.chunk_id not in seen]

    skipped = remaining
    selected_sorted = sorted(selected, key=lambda c: (-c.must_include, -c.score, c.chunk_id))
    skipped_sorted = sorted(skipped, key=lambda c: (-c.score, c.chunk_id))
    return (selected_sorted, skipped_sorted)


def _parse_chunk_response(text: str) -> Optional[ChunkedIntentResponse]:
    parser = PydanticOutputParser(pydantic_object=ChunkedIntentResponse)
    try:
        return parser.parse(text)
    except Exception:
        pass

    json_text = extract_json_from_text(text or "")
    if not json_text:
        return None
    try:
        data = json.loads(json_text)
    except Exception:
        return None
    try:
        return ChunkedIntentResponse(**data)
    except Exception:
        return None


async def intent_analysis_chunked_node(state: ReviewState) -> Dict[str, Any]:
    """Chunked diff-only intent analysis for corner-case PRs."""
    print("\n" + "="*80)
    meta_in = state.get("metadata") or {}
    print(f"📋 [节点1b] Intent Analysis (Chunked) - diff-only + top-k ({elapsed_tag(meta_in)})")
    print("="*80)

    llm: BaseChatModel = state.get("metadata", {}).get("llm")
    if not llm:
        logger.error("LLM not found in metadata")
        return {"file_analyses": []}

    config = state.get("metadata", {}).get("config")
    max_concurrent = config.system.max_concurrent_llm_requests if config else 5
    timeout_s = float(config.system.timeout_seconds) if config else 600.0

    diff_context = state.get("diff_context", "") or ""
    changed_files = state.get("changed_files", []) or []
    if not changed_files:
        print("  ⚠️  没有需要分析的文件")
        return {"file_analyses": []}

    # Build chunks from diff-only inputs.
    max_chunk_chars = _env_int("INTENT_CHUNK_MAX_CHARS", 30_000)
    max_file_diff_chars = _env_int("INTENT_CHUNK_MAX_FILE_DIFF_CHARS", 24_000)

    # Per-run budget: default to 25% of total SLA.
    budget_ratio = _env_float("INTENT_CHUNK_BUDGET_RATIO", 0.25)
    budget_s = _env_float("INTENT_CHUNK_BUDGET_SECONDS", max(30.0, timeout_s * float(budget_ratio)))
    soft_margin_s = _env_float("INTENT_CHUNK_SOFT_MARGIN_SECONDS", 60.0)

    entries = _build_file_entries(diff_context, changed_files)
    chunks = _pack_chunks(entries, max_chunk_chars=max_chunk_chars, max_file_diff_chars=max_file_diff_chars)
    selected, skipped = _select_topk_chunks(chunks)

    meta = dict(state.get("metadata") or {})
    meta["intent_mode"] = "chunked"
    meta["intent_chunk_depth"] = 2
    meta["intent_chunk_max_chars"] = int(max_chunk_chars)
    meta["intent_chunk_topk_selected"] = [c.chunk_id for c in selected]
    meta["intent_chunk_topk_skipped"] = [c.chunk_id for c in skipped]
    meta["intent_chunk_total"] = int(len(chunks))
    meta["intent_chunk_selected"] = int(len(selected))

    print(f"  📦 chunks: {len(selected)}/{len(chunks)} selected (max_chars={max_chunk_chars})")
    if skipped:
        print(f"  ⏭️  skipped chunks: {len(skipped)}")

    semaphore = asyncio.Semaphore(max_concurrent)
    parser = PydanticOutputParser(pydantic_object=ChunkedIntentResponse)

    async def analyze_chunk(chunk: Chunk) -> List[FileAnalysis]:
        async with semaphore:
            meta_now = state.get("metadata") or {}
            if elapsed_seconds(meta_now) >= budget_s:
                return []

            files_list = _format_files_list([f.file_path for f in chunk.files])
            prompt = render_prompt_template(
                "intent_analysis_chunked",
                chunk_id=chunk.chunk_id,
                group_key=chunk.group_key,
                files_list=files_list,
                chunk_diff=chunk.chunk_diff,
            )
            messages = [
                SystemMessage(content="You are an expert code reviewer analyzing PR diffs."),
                HumanMessage(content=prompt + "\n\n" + parser.get_format_instructions()),
            ]
            try:
                response = await llm.ainvoke(messages, temperature=0.3)
            except Exception as e:
                logger.error(f"Chunk LLM call failed ({chunk.chunk_id}): {type(e).__name__}: {e}")
                return []

            text = response.content if hasattr(response, "content") else str(response)
            parsed = _parse_chunk_response(text)
            if not parsed:
                logger.warning(f"Failed to parse chunk response: {chunk.chunk_id}")
                return []

            allowed = {f.file_path for f in chunk.files}
            out: List[FileAnalysis] = []
            for fa in parsed.file_analyses or []:
                if fa.file_path not in allowed:
                    logger.warning(f"Chunk {chunk.chunk_id} returned unexpected file_path: {fa.file_path}")
                    continue
                out.append(fa)
            return out

    print(f"\n  🚀 start chunked intent analysis: {len(selected)} chunks (concurrency={max_concurrent})")
    tasks = [asyncio.create_task(analyze_chunk(c)) for c in selected]
    pending = set(tasks)
    results: List[FileAnalysis] = []
    cancelled = 0

    while pending:
        meta_now = state.get("metadata") or {}
        remaining = max(0.0, float(budget_s - elapsed_seconds(meta_now)))
        if remaining <= soft_margin_s:
            for t in pending:
                t.cancel()
            cancelled = len(pending)
            break

        done, pending = await asyncio.wait(pending, timeout=min(1.0, remaining), return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            try:
                batch = await t
                if batch:
                    results.extend(batch)
            except asyncio.CancelledError:
                cancelled += 1
            except Exception as e:
                logger.error(f"Chunk task failed: {type(e).__name__}: {e}")

    if cancelled:
        meta["intent_chunk_cancelled_tasks"] = int(cancelled)

    # Build output as dicts to match ReviewState expectations.
    file_analyses_dicts = [fa.model_dump() for fa in results]
    print(f"\n  ✅ Chunked intent done ({elapsed_tag(state.get('metadata') or {})})")
    print(f"     - file analyses: {len(file_analyses_dicts)}")
    print(f"     - cancelled tasks: {cancelled}")
    print("=" * 80)

    return {"file_analyses": file_analyses_dicts, "metadata": meta}
