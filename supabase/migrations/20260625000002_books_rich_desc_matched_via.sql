-- YES24 rich_description 가 어떤 경로로 매칭되어 저장됐는지 추적(audit).
--   'isbn'           = ISBN 완전일치 (신뢰 높음)
--   'title_fallback' = ISBN 불일치 → 제목+저자 강매칭 (오매칭 위험 상대적 높음)
-- 과거 저장분은 NULL(미상). 향후 audit 시 title_fallback 우선 재검증.
ALTER TABLE books ADD COLUMN IF NOT EXISTS rich_description_matched_via TEXT;
