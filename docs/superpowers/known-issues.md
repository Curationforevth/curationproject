# Known Issues / Follow-ups

작업 중 발견된 사전 장애와 후속으로 처리할 항목.

---

## 🔴 Critical (보안/데이터 손실 위험)

(현재 없음)

---

## 🟡 Important

(현재 없음)

---

## 🟢 Low priority

### KI-003 — `similar_by_vector` exclude 로직 O(n²)
- **위치:** `recommendation-server/engine/index.py:65-67`
- **현상:** `self._desc_bid_order.index(ex)` 가 list linear scan. exclude_ids 작을 땐 무시 가능하지만 union 엔드포인트에서 5-10개 seed 면 O(n × |exclude|).
- **해결:** `build_desc_matrix()` 에서 `self._desc_bid_to_idx = {bid: i for i, bid in enumerate(...)}` 캐시. `add_book` 시 invalidate.
- **우선순위:** Low (현재 fleet 규모에선 noticeable 아님)
- **추정 크기:** XS

### KI-004 — Tier 2 가 매번 Tier 1 재호출
- **위치:** `scripts/data4library_discovery_collector.py` `main()` `--tier 2` 분기
- **현상:** `--tier 2` 실행 시 시드를 얻기 위해 `fetch_tier1()` 을 다시 실행 → 10 KDC × pages 정보나루 API 재호출. API 비용 두 배.
- **해결:** Tier 2 시드를 books 테이블의 `loan_count` desc top-N 에서 직접 가져옴
- **우선순위:** Low (API rate-limit 여유 있음)
- **추정 크기:** S

### KI-005 — `BATCH_SIZE_FALLBACKS = [50, 20, 5]` 첫 entry dead
- **위치:** `scripts/tier1_embedder.py`
- **현상:** 50 은 `_next_smaller_size(50)` 가 항상 다음 단계로 넘어가서 사용 안 됨. 가독성만 해침.
- **해결:** `[20, 5]` 로 줄이고 주석 추가
- **우선순위:** Trivial
- **추정 크기:** XS

### KI-006 — Pre-snapshot 반복 실패 시 short-circuit 부재
- **위치:** `scripts/pipeline_orchestrator.py::run_step`
- **현상:** Pre snapshot 이 첫 step 에서 실패하면 모든 후속 step 도 같은 인프라 이슈로 실패할 가능성 큰데, 매 step 마다 같은 에러 반복 출력
- **해결:** Pre snapshot 실패가 2회 이상 연속이면 orchestrator 자체를 abort
- **우선순위:** Low (실제 발생 빈도 낮음)
- **추정 크기:** S

---

## 작성 규칙

- 새 장애 발견 시 즉시 추가 (작업 블록하지 말 것)
- 해결 후 항목 삭제 (역사는 git log 로 추적)
- 우선순위는 데이터/보안 영향 기준
- 위치는 file:line 으로 정확히
