import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import httpx
from fastapi import APIRouter, HTTPException

from app.config import get_effective_settings
from app.db import DB_PATH, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/duplicates", tags=["duplicates"])

# Known video extensions
VIDEO_EXTS = {
    ".mkv", ".mp4", ".avi", ".wmv", ".flv", ".mov", ".m4v",
    ".ts", ".webm", ".mpg", ".mpeg", ".divx", ".ogm",
}

# Patterns to strip from filenames for grouping
_STRIP_PATTERNS = [
    # Release group tags
    r"\[.*?\]",
    # Quality/codec tags
    r"(?i)\b(BluRay|BDRip|BRRip|WEBRip|WEB-DL|WEBDL|WEB|HDRip|DVDRip|HDTV|"
    r"PDTV|DVDScr|TS|CAM|R5|HC|REMUX|Blu-Ray)\b",
    r"(?i)\b(x264|x265|h264|h\.264|h265|h\.265|HEVC|AVC|XviD|DivX|AAC|AC3|"
    r"DTS|FLAC|MP3|DD5\.1|DD2\.0|Atmos|TrueHD|10bit)\b",
    # Resolution
    r"(?i)\b(2160p|1080p|720p|480p|4K|UHD|FHD|HD)\b",
    # Language/dub tags
    r"(?i)\b(CZ|SK|EN|ENG|DABING|dab|dabing|titulky|sub|subs|dubbed|dual|multi)\b",
    # Common noise
    r"(?i)\b(RARBG|YTS|YIFY|GalaxyRG|FGT|EVO|SPARKS|GECKOS|NTb|PSA|ION10)\b",
    r"[-._]",
]

# Year pattern
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")


def _normalize_title(filename: str) -> tuple[str, str]:
    """Extract normalized title and year from a video filename.

    Returns (normalized_title, year) where year may be empty string.
    """
    # Remove extension
    name = Path(filename).stem

    # Extract year
    year_match = _YEAR_RE.search(name)
    year = year_match.group(1) if year_match else ""

    # Strip everything after year (usually quality/codec info)
    if year_match:
        name = name[: year_match.start()]

    # Apply strip patterns
    for pattern in _STRIP_PATTERNS:
        name = re.sub(pattern, " ", name)

    # Normalize whitespace and case
    name = re.sub(r"\s+", " ", name).strip().lower()

    return name, year


def _detect_quality(filename: str) -> str:
    """Detect video quality from filename."""
    fn = filename.upper()
    if "2160P" in fn or "4K" in fn or "UHD" in fn:
        return "2160p"
    if "1080P" in fn or "FHD" in fn:
        return "1080p"
    if "720P" in fn or "HD" in fn:
        return "720p"
    if "480P" in fn or "SD" in fn:
        return "480p"
    return "unknown"


def _detect_language(filename: str) -> str:
    """Detect language/dub from filename."""
    fn = filename.upper()
    tags = []
    if "CZ" in fn or "DABING" in fn or "DAB" in fn:
        tags.append("CZ")
    if re.search(r"\bSK\b", fn):
        tags.append("SK")
    if re.search(r"\bEN\b", fn) or re.search(r"\bENG\b", fn):
        tags.append("EN")
    return "/".join(tags) if tags else "-"


GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


async def _ensure_table() -> None:
    """Create duplicates table if not exists."""
    db = await get_db()
    try:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS scanned_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                year TEXT NOT NULL DEFAULT '',
                size INTEGER NOT NULL DEFAULT 0,
                quality TEXT NOT NULL DEFAULT 'unknown',
                language TEXT NOT NULL DEFAULT '-',
                modified_at TEXT NOT NULL DEFAULT '',
                ai_group TEXT NOT NULL DEFAULT '',
                scanned_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_scanned_norm_title "
            "ON scanned_files (normalized_title, year)"
        )
        # Add ai_group column if table already exists without it
        try:
            await db.execute(
                "ALTER TABLE scanned_files ADD COLUMN ai_group TEXT NOT NULL DEFAULT ''"
            )
        except Exception:
            pass  # column already exists
        await db.commit()
    finally:
        await db.close()


@router.post("/scan")
async def scan_for_duplicates() -> dict:
    """Scan plex_media_dir for video files and detect duplicates."""
    cfg = await get_effective_settings()
    media_dir = cfg["plex_media_dir"]

    if not os.path.isdir(media_dir):
        raise HTTPException(400, f"Media directory not found: {media_dir}")

    await _ensure_table()

    # Walk the directory tree
    found_files: list[dict] = []
    for root, _dirs, files in os.walk(media_dir):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in VIDEO_EXTS:
                continue

            full_path = os.path.join(root, fname)
            try:
                stat = os.stat(full_path)
            except OSError:
                continue

            norm_title, year = _normalize_title(fname)
            if not norm_title:
                continue

            found_files.append({
                "path": full_path,
                "filename": fname,
                "normalized_title": norm_title,
                "year": year,
                "size": stat.st_size,
                "quality": _detect_quality(fname),
                "language": _detect_language(fname),
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            })

    # Upsert into DB
    db = await get_db()
    try:
        # Clear old entries that no longer exist on disk
        await db.execute("DELETE FROM scanned_files")

        for f in found_files:
            await db.execute(
                """
                INSERT INTO scanned_files
                    (path, filename, normalized_title, year, size, quality, language, modified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f["path"], f["filename"], f["normalized_title"],
                    f["year"], f["size"], f["quality"], f["language"],
                    f["modified_at"],
                ),
            )
        await db.commit()
    finally:
        await db.close()

    logger.info("Scanned %d video files in %s", len(found_files), media_dir)
    return {"scanned": len(found_files), "media_dir": media_dir}


@router.post("/ai-scan")
async def ai_scan_for_duplicates() -> dict:
    """Use AI (Groq) to find duplicate groups including translated titles etc."""
    cfg = await get_effective_settings()
    groq_key = cfg.get("groq_api_key", "")
    if not groq_key:
        raise HTTPException(400, "Groq API key not configured")

    await _ensure_table()

    # First do a normal scan to refresh file list
    media_dir = cfg["plex_media_dir"]
    if not os.path.isdir(media_dir):
        raise HTTPException(400, f"Media directory not found: {media_dir}")

    # Collect files from disk
    found_files: list[dict] = []
    for root, _dirs, files in os.walk(media_dir):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in VIDEO_EXTS:
                continue
            full_path = os.path.join(root, fname)
            try:
                stat = os.stat(full_path)
            except OSError:
                continue
            norm_title, year = _normalize_title(fname)
            if not norm_title:
                continue
            found_files.append({
                "path": full_path,
                "filename": fname,
                "normalized_title": norm_title,
                "year": year,
                "size": stat.st_size,
                "quality": _detect_quality(fname),
                "language": _detect_language(fname),
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            })

    if not found_files:
        return {"scanned": 0, "ai_groups": 0}

    # Build file list for AI — just index + filename
    file_lines = []
    for i, f in enumerate(found_files):
        file_lines.append(f'{i}. {f["filename"]}')
    file_list_text = "\n".join(file_lines)

    # Send to Groq in batches if needed (max ~200 per request)
    batch_size = 200
    all_ai_groups: list[list[int]] = []

    for batch_start in range(0, len(found_files), batch_size):
        batch_end = min(batch_start + batch_size, len(found_files))
        batch_lines = file_lines[batch_start:batch_end]

        system_prompt = """\
You are a strict file duplicate detector. Given a numbered list of video filenames, \
find files that are the EXACT SAME movie or TV episode — just different releases \
(different quality, codec, release group, or language of the same content).

STRICT RULES — read carefully:
- ONLY group files that are truly the same single movie or episode
- "Hunger Games (2012)" and "Hunger Games - Síla vzdoru (2014)" are DIFFERENT movies — NOT duplicates!
- Sequels are DIFFERENT movies (Part 1 vs Part 2, Roman numeral I vs II, etc.)
- Different seasons or episodes are DIFFERENT — NOT duplicates
- Different years almost always means different movies, even if the franchise name is similar
- A translated title is a duplicate ONLY if it refers to the same specific movie \
  (e.g. "Lhář, lhář (1997)" = "Liar Liar (1997)" — same movie, same year)
- When in doubt, do NOT group them — false negatives are much better than false positives

VALID duplicates examples:
- "Movie.2020.1080p.BluRay.mkv" and "Movie.2020.720p.WEB.mkv" (same movie, different quality)
- "Pelíšky (1999) CZ.avi" and "Cosy.Dens.1999.1080p.mkv" (same movie, translated title)

NOT duplicates:
- "Hunger Games (2012)" vs "Hunger Games Catching Fire (2013)" (different movies in a franchise)
- "Avatar (2009)" vs "Avatar The Way of Water (2022)" (sequel)
- "The Office S01E01" vs "The Office S01E02" (different episodes)

Return ONLY a valid JSON array of arrays, where each inner array contains \
file indices that are the same movie. No markdown, no explanation. \
Return [] if there are no duplicates.

Example: [[0, 5], [3, 7]]"""

        user_prompt = f"Files:\n{chr(10).join(batch_lines)}"

        try:
            # Retry with backoff for rate limits (Groq free tier ~30 req/min)
            resp = None
            for attempt in range(4):
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(
                        GROQ_API_URL,
                        headers={
                            "Authorization": f"Bearer {groq_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": "llama-3.1-8b-instant",
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                            "temperature": 0.1,
                            "max_tokens": 4096,
                        },
                    )
                    if resp.status_code == 429:
                        wait = (2 ** attempt) * 5  # 5s, 10s, 20s, 40s
                        logger.warning("Groq rate limited, waiting %ds (attempt %d/4)", wait, attempt + 1)
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                    break
            else:
                raise RuntimeError("Groq rate limit exceeded after 4 retries")

            content = resp.json()["choices"][0]["message"]["content"].strip()
            # Strip markdown fences
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[: content.rfind("```")]
            content = content.strip()

            groups = json.loads(content)
            if isinstance(groups, list):
                for group in groups:
                    if isinstance(group, list) and len(group) >= 2:
                        # Adjust indices for batch offset
                        adjusted = [idx + batch_start for idx in group
                                    if isinstance(idx, int) and 0 <= idx < batch_end - batch_start]
                        if len(adjusted) >= 2:
                            all_ai_groups.append(adjusted)
        except Exception as e:
            logger.error("AI duplicate scan failed: %s", e)
            raise HTTPException(500, f"AI scan failed: {e}")

    # Save to DB with ai_group labels
    db = await get_db()
    try:
        await db.execute("DELETE FROM scanned_files")

        for i, f in enumerate(found_files):
            # Find which AI group this file belongs to
            ai_group = ""
            for gi, group in enumerate(all_ai_groups):
                if i in group:
                    ai_group = f"ai_{gi}"
                    break

            await db.execute(
                """
                INSERT INTO scanned_files
                    (path, filename, normalized_title, year, size, quality,
                     language, modified_at, ai_group)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f["path"], f["filename"], f["normalized_title"],
                    f["year"], f["size"], f["quality"], f["language"],
                    f["modified_at"], ai_group,
                ),
            )
        await db.commit()
    finally:
        await db.close()

    logger.info(
        "AI scanned %d files, found %d duplicate groups",
        len(found_files), len(all_ai_groups),
    )
    return {
        "scanned": len(found_files),
        "ai_groups": len(all_ai_groups),
        "media_dir": media_dir,
    }


@router.get("")
async def get_duplicates(mode: str = "simple") -> dict:
    """Return groups of duplicate files.

    mode=simple: group by normalized title + year (mechanical)
    mode=ai: group by AI-detected groups (must run ai-scan first)
    """
    await _ensure_table()

    db = await get_db()
    try:
        if mode == "ai":
            # Use AI groups
            cursor = await db.execute(
                """
                SELECT ai_group, COUNT(*) as cnt
                FROM scanned_files
                WHERE ai_group != ''
                GROUP BY ai_group
                HAVING cnt > 1
                ORDER BY cnt DESC
                """
            )
            groups_raw = await cursor.fetchall()

            groups = []
            for g in groups_raw:
                ai_group = g["ai_group"]

                cursor2 = await db.execute(
                    """
                    SELECT id, path, filename, size, quality, language, modified_at
                    FROM scanned_files
                    WHERE ai_group = ?
                    ORDER BY size DESC
                    """,
                    (ai_group,),
                )
                files = [dict(row) for row in await cursor2.fetchall()]

                # Use the first file's title as display title
                if files:
                    norm, year = _normalize_title(files[0]["filename"])
                    display_title = norm.title()
                    if year:
                        display_title += f" ({year})"
                else:
                    display_title = ai_group

                groups.append({
                    "title": display_title,
                    "ai_group": ai_group,
                    "count": len(files),
                    "files": files,
                })

            return {"groups": groups, "total_groups": len(groups), "mode": "ai"}

        else:
            # Simple mode: group by normalized title + year
            cursor = await db.execute(
                """
                SELECT normalized_title, year, COUNT(*) as cnt
                FROM scanned_files
                GROUP BY normalized_title, year
                HAVING cnt > 1
                ORDER BY cnt DESC, normalized_title
                """
            )
            groups_raw = await cursor.fetchall()

            groups = []
            for g in groups_raw:
                title = g["normalized_title"]
                year = g["year"]

                cursor2 = await db.execute(
                    """
                    SELECT id, path, filename, size, quality, language, modified_at
                    FROM scanned_files
                    WHERE normalized_title = ? AND year = ?
                    ORDER BY size DESC
                    """,
                    (title, year),
                )
                files = [dict(row) for row in await cursor2.fetchall()]

                display_title = title.title()
                if year:
                    display_title += f" ({year})"

                groups.append({
                    "title": display_title,
                    "normalized_title": title,
                    "year": year,
                    "count": len(files),
                    "files": files,
                })

            return {"groups": groups, "total_groups": len(groups), "mode": "simple"}
    finally:
        await db.close()


@router.delete("/file/{file_id}")
async def delete_file(file_id: int) -> dict:
    """Delete a specific file from disk and remove from DB."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT path, filename FROM scanned_files WHERE id = ?", (file_id,)
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "File not found in scan database")

        file_path = row["path"]

        # Delete from disk
        try:
            os.remove(file_path)
            logger.info("Deleted file: %s", file_path)
        except FileNotFoundError:
            logger.warning("File already gone: %s", file_path)
        except OSError as e:
            raise HTTPException(500, f"Failed to delete file: {e}")

        # Remove from DB
        await db.execute("DELETE FROM scanned_files WHERE id = ?", (file_id,))
        await db.commit()

        return {"ok": True, "deleted": row["filename"]}
    finally:
        await db.close()
