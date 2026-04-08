# Book Data Pipeline

수집부터 similar 인덱스까지의 end-to-end 플로우.

## 개요

```
[discovery_collector]  ── books (title/author/cover/isbn)
         ↓
[yes24_scraper]        ── books.rich_description
         ↓
[generate_book_v3_vectors] ── book_v3_vectors
         ↓
[reason_extractor]     ── book_reasons
         ↓
[tier1_embedder]       ── book_embeddings
         ↓
[build_index]          ── recommendation-server/data/index.pkl
         ↓
[Render 재배포]         (수동 또는 CD 훅)
```

## 자동 실행 (권장)

수집 + enrich 한 번에:

```bash
# 정보나루 3-tier 수집 후 orchestrator 자동 트리거
python3 scripts/data4library_discovery_collector.py --tier 1 --pages 3 --with-enrich
python3 scripts/data4library_discovery_collector.py --tier 2 --tier2-seeds 100 --with-enrich
python3 scripts/data4library_discovery_collector.py --tier 3 --with-enrich
```

수집만:

```bash
python3 scripts/data4library_discovery_collector.py --tier 1 --pages 3
```

그 다음 수동으로 enrich:

```bash
python3 scripts/pipeline_orchestrator.py
```

## 상태 확인

```bash
# 수집 현황
python3 scripts/data4library_discovery_collector.py --status

# 파이프라인 각 stage 현황
python3 scripts/pipeline_orchestrator.py --status
```

## 부분 실행

```bash
# 단일 step
python3 scripts/pipeline_orchestrator.py --step yes24_scraper

# 중간부터 재개 (실패 복구)
python3 scripts/pipeline_orchestrator.py --from reason_extractor

# 소량만 테스트
python3 scripts/pipeline_orchestrator.py --limit 20 --dry-run
```

## 각 Step 직접 호출

Orchestrator 없이 개별 실행도 가능 (기존 방식):

```bash
python3 scripts/yes24_scraper.py --limit 50
python3 scripts/generate_book_v3_vectors.py 50
python3 scripts/reason_extractor.py --limit 50
python3 scripts/tier1_embedder.py --limit 50
cd recommendation-server && python3 scripts/build_index.py
```

## 실패 복구

Orchestrator 가 특정 step 에서 실패하면 로그 확인 후:

1. 문제 수정 (API 키, rate limit, 네트워크 등)
2. `--from <failed_step>` 으로 재개

Idempotency: 모든 enrich 스크립트는 이미 처리된 책은 건너뜀. 중복 실행 안전.

## 서버 반영

`build_index` 는 `recommendation-server/data/index.pkl` 을 생성한다. 이 파일이 git 에 커밋되거나 Render 에서 직접 생성되어야 서버가 새 인덱스를 로드한다. 현재 운영은 Render 자동 배포 → 서버 기동 시 `build_index.py` 실행 방식 (별도 세팅).
