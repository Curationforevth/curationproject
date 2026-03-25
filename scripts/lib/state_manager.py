"""
배치 수집 상태 관리 모듈
Supabase batch_collection_state 테이블 CRUD
"""
from datetime import datetime, timezone
from .retry import with_retry


class StateManager:
    def __init__(self, supabase_client):
        self.sb = supabase_client
        self.table = "batch_collection_state"

    def get_state(self, source_type, query_type=None, category_id=None, search_keyword=None):
        """특정 수집 소스의 상태 조회"""
        q = self.sb.table(self.table).select("*").eq("source_type", source_type)

        # Supabase PostgREST: is_.("field", "null") → "field=is.null"
        for field, value in [
            ("query_type", query_type),
            ("category_id", category_id),
            ("search_keyword", search_keyword),
        ]:
            if value is not None:
                q = q.eq(field, value)
            else:
                q = q.is_(field, "null")

        result = with_retry(lambda: q.execute())
        return result.data[0] if result.data else None

    def upsert_state(self, source_type, query_type=None, category_id=None,
                     search_keyword=None, last_page_fetched=0,
                     total_items_found=0, unique_items_saved=0,
                     completed=False):
        """상태 생성 또는 업데이트"""
        row = {
            "source_type": source_type,
            "query_type": query_type,
            "category_id": category_id,
            "search_keyword": search_keyword,
            "last_page_fetched": last_page_fetched,
            "total_items_found": total_items_found,
            "unique_items_saved": unique_items_saved,
            "completed": completed,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        with_retry(lambda: self.sb.table(self.table).upsert(
            row,
            on_conflict="source_type,query_type,category_id,search_keyword"
        ).execute())

    def reset_expired_states(self, days=30):
        """Phase 2-3의 completed 상태를 days일 경과 시 리셋.
        Phase 1 (item_list)은 영구 완료이므로 제외."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        result = with_retry(lambda: (
            self.sb.table(self.table)
            .update({"completed": False})
            .eq("completed", True)
            .neq("source_type", "item_list")
            .lt("updated_at", cutoff)
            .execute()
        ))
        count = len(result.data) if result.data else 0
        if count > 0:
            print(f"  ♻ {count}개 소스 상태 리셋 (30일 경과)")
        return count

    def get_all_states(self):
        """전체 상태 조회 (진행 현황 리포트용)"""
        result = with_retry(lambda: self.sb.table(self.table).select("*").execute())
        return result.data

    def get_summary(self):
        """소스 타입별 요약"""
        states = self.get_all_states()
        summary = {}
        for s in states:
            st = s["source_type"]
            if st not in summary:
                summary[st] = {"total": 0, "completed": 0, "unique_saved": 0}
            summary[st]["total"] += 1
            summary[st]["unique_saved"] += s.get("unique_items_saved", 0)
            if s.get("completed"):
                summary[st]["completed"] += 1
        return summary
