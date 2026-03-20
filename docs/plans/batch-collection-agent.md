# 배치 수집 에이전트 — 전체 플랜

> 작성일: 2026-03-20
> 상태: 스크립트 완성, 실행 + 에이전트화 필요

---

## 1. 배경 & 문제

- MVP 릴리즈에 **~90,000권** 필요. 현재 DB에 **92권** (베스트셀러만).
- 기존 `batch_collect_aladin.py`는 베스트셀러 100권만 반복 수집 → 돌려도 새 책이 안 쌓임.
- 수집 스크립트를 사람이 매번 수동으로 챙기는 건 잘못된 접근.

---

## 2. Eden이 제시한 조건

1. **인기순 수집**: 사람들이 많이 찾는 순서로 가져와야 함. 아무도 안 읽는 책은 의미 없음.
2. **API 낭비 금지**: 중복이 많아서 API콜을 낭비해선 안 됨.
3. **수집 시 필터링**: 어차피 제거할 것(문제집/수험서)은 가져오지 않도록 설계.
4. **인라인 정제**: 가져오면서 제목 정제도 함께 수행 (별도 후처리 스크립트 불필요).
5. **자율 실행**: 사람이 직접 챙기지 않아도 돌아가는 구조여야 함.

---

## 3. 완료된 작업

### 3-1. 스마트 배치 수집 스크립트

3단계로 인기 도서를 점진적 수집하는 `smart_batch_collector.py` 완성.

| Phase | 방식 | 예상 수집량 | API콜 |
|-------|------|------------|-------|
| 1. ItemList 스윕 | 17카테고리 × 5 QueryType × 4페이지 | ~8,000~10,000권 | ~340콜 |
| 2. 저자 검색 | DB 저자 추출 + 큐레이션 저자, SalesPoint순 | ~30,000~40,000권 | ~2,000+콜/일 |
| 3. 키워드 검색 | 문학상/시리즈/장르/트렌드, SalesPoint순 | 나머지 → 9만권 | ~2,000+콜/일 |

### 3-2. 인라인 파이프라인 (조건 1~4 충족)

```
API 응답 → ISBN 없으면 스킵
         → in-memory ISBN set으로 중복 체크 (DB 라운드트립 절약)  ← 조건2
         → 문제집/수험서 필터 (is_non_book — 확장 키워드)        ← 조건3
         → 제목 정제 (clean_title — 특별판/굿즈 정보 제거)       ← 조건4
         → 50건씩 배치 upsert
```
- SalesPoint(판매량) 정렬로 인기순 수집 ← 조건1
- 새 책 0권이면 조기 종료 → API 절약 ← 조건2

### 3-3. 상태 추적 시스템

- `batch_collection_state` Supabase 테이블 생성 완료
- 스크립트 중단/재시작 시 이어서 수집
- 완료된 소스 조합 자동 스킵
- 일일 API 한도(4,900콜) 도달 시 자동 중단

### 3-4. dry-run 테스트 통과

- Phase 1 테스트: 카테고리당 40~50권씩 새 책 확인
- 필터/정제/중복체크 정상 동작

### 3-5. 파일 구조

```
scripts/
  smart_batch_collector.py       # 메인 오케스트레이터 ✅
  lib/
    __init__.py                  ✅
    aladin_client.py             # API 클라이언트 (레이트리밋, 재시도) ✅
    book_filter.py               # 문제집 필터 (기존 + 확장) ✅
    title_cleaner.py             # 제목 정제 (기존 로직 추출) ✅
    state_manager.py             # 상태 추적 CRUD ✅
  data/
    search_keywords.json         # Phase 3 키워드 ✅

supabase/
  002_batch_state.sql            # 상태 추적 테이블 ✅ (DB에 생성 완료)
```

### 3-6. 실행 명령어

```bash
python3 scripts/smart_batch_collector.py                        # 전체 실행
python3 scripts/smart_batch_collector.py --phase item_list      # Phase 1만
python3 scripts/smart_batch_collector.py --phase author_search  # Phase 2만
python3 scripts/smart_batch_collector.py --phase keyword_search # Phase 3만
python3 scripts/smart_batch_collector.py --status               # 진행 현황
python3 scripts/smart_batch_collector.py --dry-run              # DB 저장 없이 테스트
```

---

## 4. 다음 단계: 커스텀 스킬로 에이전트화 (조건 5)

### 4-1. 왜 스킬인가?

| 접근 | 문제점 |
|------|--------|
| Claude cron | 세션 종료 시 사라짐 (최대 3일) |
| Supabase Edge Function | 별도 인프라 셋업/관리 필요, Python 포팅 |
| GitHub Actions | secrets 관리, 사람이 설정해야 함 |
| macOS launchd | Mac 꺼지면 안 돌아감 |
| **superpowers 커스텀 스킬** | **Claude 자체가 에이전트. 세션 시작마다 자동 판단/실행** |

PM(Eden)은 매일 Claude를 사용하므로, 세션 시작 시 자동으로 배치 수집을 판단/실행하는 스킬이 가장 실용적.

### 4-2. 스킬 설계 초안

**위치**: `~/.claude/skills/batch-collection-agent/SKILL.md` 또는 프로젝트 내

```yaml
name: batch-collection-agent
description: Use when working on the curation project and books DB has not reached the 90K target
```

**트리거**: curation 프로젝트에서 작업 시작 시

**판단 → 실행 → 완료 흐름**:
1. `--status`로 DB 현황 체크
2. 9만권 미달이면 실행 제안
3. 같은 날 이미 한도 소진했으면 스킵
4. `smart_batch_collector.py` 실행 (상태 추적으로 자동 이어하기)
5. 일일 한도 도달 시 자동 중단
6. 9만권 도달 시 완료 보고 + 스킬 비활성화

### 4-3. superpowers writing-skills TDD 방법론 적용

1. **RED (baseline)**: 스킬 없이 세션 시작 → Claude가 DB 수집을 자발적으로 제안하는지 확인 (안 할 것)
2. **GREEN (write skill)**: 스킬 작성 → 세션 시작 시 자동으로 status 체크 + 실행 제안하는지 확인
3. **REFACTOR (close loopholes)**: 엣지 케이스 보완
   - 한도 이미 소진된 상태
   - API 장애
   - 목표 도달 후 불필요한 실행 방지
   - "다른 작업 중인데 배치도 돌려야 하나?" 판단

---

## 5. 고려사항 & 리스크

| 항목 | 내용 |
|------|------|
| 알라딘 API 일 5,000콜 한도 | 스크립트에 4,900콜 안전 마진 내장 |
| 예상 소요 기간 | 7~12일 (매일 실행 기준) |
| 카테고리 깊은 페이지 퀄리티 | Phase 1은 최대 4페이지만 (인기도서 밀도 높은 구간) |
| API 장애/타임아웃 | exponential backoff 3회 재시도 구현 완료 |
| 스킬 세션 의존성 | PM이 Claude 세션을 아예 안 열면 실행 안 됨 — 수용 가능 (매일 사용하니까) |
| superpowers 적용 | 이번 세션에서 설치해서 바로 안 됨 → **다음 세션에서 writing-skills TDD로 스킬 작성** |
| 스크립트 미실행 상태 | 아직 실제 실행은 안 함 (dry-run만 통과). 다음 세션에서 Phase 1부터 실행 필요 |
