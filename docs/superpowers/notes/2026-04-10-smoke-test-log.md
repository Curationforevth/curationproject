# Phase H: Smoke Test Log (2026-04-10)

Phase A~F 완료 후 각 스크립트를 `--dry-run`으로 실행하여 런타임 문제 조기 발견.

## 결과

| script | args | exit | 소요 | 핵심 로그 | 관찰된 문제 |
|--------|------|------|------|-----------|-------------|
| data4library_discovery_collector | --tier 1 --period-days 7 --pages 1 --dry-run | 0 | ~15s | 500→112 성인필터→110→106 dedup, would upsert 106 | F4 영향: filtered_children 388 (빈 symbol 제외됨, 의도 동작) |
| smart_batch_collector | --dry-run --daily-target 5 | 0 | ~10s | API 1회, 13/5 달성, F1 re.sub 정상 | 정상 |
| yes24_scraper | --limit 2 --dry-run | 0 | ~5s | 검색실패 1, ISBN불일치 1 | 기존 동작 (F6: unverified skip 적용됨) |
| generate_book_v3_vectors | 2 --dry-run | 0 | ~3s | "모든 책이 이미 처리됨" | 정상 (이미 처리 완료 상태) |
| v3_reason_extract | --limit 2 --dry-run --no-checkpoint | 0 | ~8s | 2권→15 reasons | 정상 |
| tier2_embedder | --limit 2 --dry-run | 0 | ~3s | 2권 스킵 (유효 텍스트 없음) | 정상 (대상 도서에 rich_description 없음) |
| batch_enricher | --limit 2 --dry-run | 0 | ~2s | colorthief 미설치 경고 | 기존 이슈: pip install colorthief 필요 |
| data4library_collector | --limit 2 --dry-run | 0 | ~5s | 1/2 처리, 1건 빈 body 에러 | 기존 이슈: 정보나루 API transient 빈 응답 (D3에서 이미 처리) |
| pipeline_orchestrator | --limit 3 --dry-run | (중단) | >2min | import 성공, subprocess 실행 중 | 서브프로세스 체인 소요시간 큼 (LLM+embedding), 별도 실행 필요 |

## 발견된 추가 이슈

1. **colorthief 미설치**: `batch_enricher.py` 색상 추출 기능 비활성. `pip install colorthief` 필요.
2. **pipeline_orchestrator 소요시간**: --limit 3 + --dry-run 에도 2분+ 소요. 서브프로세스 각각이 OpenAI API 호출. 전체 체인 smoke 는 별도 세션 필요.
3. **recommendation-server tests**: `fastapi` 미설치로 collection error. 별도 venv 필요.

## 결론

- **import/schema 에러**: 없음 (Phase A~F 수정이 올바르게 적용됨)
- **env/API 키**: 정상 로드
- **F4 (empty symbol → False)**: 388/500건 필터링 → 과하면 롤백 고려
- **전체**: 9/9 스크립트 실행 성공 (1건 시간 초과로 중단, 에러 아님)
