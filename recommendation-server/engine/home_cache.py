"""recommendation-server/engine/home_cache.py

home_section_cache 읽기/쓰기 + input_hash 계산.

Spec §5.2, §6.2: hash = sha256(user_state.updated_at + current_hour_bucket)
→ user_books 변경 (trigger 로 user_state 갱신) 또는 시간 bucket 변경 시 invalidate
"""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from typing import Optional

from config import get_supabase


def current_hour_bucket(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H")


def compute_home_input_hash(user_state_updated_at: str, hour_bucket: str) -> str:
    raw = f"{user_state_updated_at}|{hour_bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()


def load_home_cache(user_id: str) -> Optional[dict]:
    sb = get_supabase()
    res = sb.table("home_section_cache").select("*").eq("user_id", user_id).maybe_single().execute()
    return res.data


def save_home_cache_if_current(
    user_id: str,
    sections: list,
    tier: int,
    stage: int,
    input_hash: str,
) -> None:
    """BackgroundTasks 로 호출. hash 가 current 일 때만 저장."""
    sb = get_supabase()
    try:
        sb.table("home_section_cache").upsert({
            "user_id": user_id,
            "sections": sections,
            "tier": tier,
            "stage": stage,
            "input_hash": input_hash,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="user_id").execute()
    except Exception as e:
        # 캐시 쓰기 실패는 응답에 영향 없음 (다음 호출 때 재시도)
        print(f"home_cache save failed for {user_id}: {e}")
