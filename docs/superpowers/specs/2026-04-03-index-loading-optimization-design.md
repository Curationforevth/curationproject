# Index Loading Optimization Design

**Date:** 2026-04-03
**Status:** Draft
**Related:** recommendation-engine-v3, recommendation-server

## 문제 정의

추천 서버가 시작할 때마다 Supabase REST API로 전체 벡터 인덱스를 로드하는 구조에서 3가지 문제가 동시에 발생한다.

| 문제 | 현재 상태 | 영향 |
|------|-----------|------|
| REST 타임아웃 | 500건/페이지 × ~83회, Supabase 8초 제한에 빈번히 실패 | 서버 시작 실패 |
| 메모리 초과 | float32 인덱스 ~350MB × 4워커 = 1.4GB | Render 무료 512MB 초과 |
| 콜드스타트 | 성공해도 ~5분 소요, 컨테이너 재시작마다 반복 | 배포 후 서비스 불가 시간 |

### 데이터 규모

| 데이터 | 건수 | 차원 | float32 크기 |
|--------|------|------|-------------|
| reason 임베딩 | 33,824 | 2000 | ~261MB (76%) |
| desc 임베딩 | 2,510 | 2000 | ~19MB |
| l1/l2 genre | 5,020 | 2000 | ~38MB |
| desc_matrix | 2,510 | 2000 | ~19MB |
| genre_embs | 825 | 2000 | ~6MB |
| **합계** | | | **~343MB** + Python ~100MB |

> 모든 벡터는 text-embedding-3-large (2000차원) 공간에 존재한다. (v3 설계 원칙)

### 변경하지 않는 것

- v3 스코어링 알고리즘 (5축 가중합, maxsim)
- 개별 reason 벡터 유지 (조각 매칭 금지 원칙)
- 유저 데이터(user_books) 실시간 fetch 구조
- API 인터페이스 (/recommend, /similar, /feedback, /health)

## 솔루션

4가지를 함께 적용한다.

### 1. Build-time 인덱스 생성

서버 런타임에서 Supabase 벡터 로드를 제거한다. 대신 로컬에서 빌드 스크립트를 실행하여 pkl 파일을 생성하고, Docker 이미지에 포함한다.

**빌드 스크립트: `scripts/build_index.py`**

```
Supabase REST (retry + sleep) → VectorIndex + books_meta 구축 → data/index.pkl 저장
```

- 500건/페이지, 실패 시 3회 retry (backoff 10초)
- 페이지 간 sleep 1초 (Supabase rate limit 대응)
- 로컬 실행이라 시간 제약 없음 (~5-10분)
- 결과물: `recommendation-server/data/index.pkl`

**서버 로더: `engine/loader.py`**

```python
# 변경 전: Supabase REST 83회 호출
# 변경 후: 로컬 pkl 1회 로드
def load_index() -> tuple[VectorIndex, dict]:
    with open("data/index.pkl", "rb") as f:
        bundle = pickle.load(f)
    # bundle = {"index": VectorIndex, "meta": books_meta, "built_at": "2026-04-03T..."}
    return bundle["index"], bundle["meta"]
```

시작 시간: ~5분 → **~2초**

### 2. float16 양자화

모든 벡터를 float32 → float16으로 변환한다.

- **메모리 50% 절감**: ~343MB → ~172MB
- **정확도 영향 무시 가능**: L2-정규화 벡터 간 내적(cosine sim)에서 float16 오차는 ±0.001 수준
- 적용 지점: `build_index.py`에서 pkl 저장 시 float16으로 변환
- `VectorIndex`와 `scorer.py`는 numpy가 자동 캐스팅하므로 코드 변경 최소
- **float32↔float16 호환**: 요청 시 Supabase에서 가져오는 `feedback_embedding`(float32)과 인덱스(float16)는 numpy 내적 시 자동 업캐스팅으로 호환

### 3. 단일 워커

Dockerfile CMD를 `--workers 4` → `--workers 1`로 변경한다.

- **메모리**: 172MB + Python 100MB = **~272MB** (Render 512MB 내 여유 ~240MB)
- **동시성**: uvicorn async로 I/O 동시 처리. CPU-bound 스코어링은 2,510권 기준 ~10ms로 병목 아님
- MVP 트래픽(소수 사용자)에서 단일 워커로 충분

### 4. 데이터 갱신 플로우

인덱스 데이터는 배치 작업(새 책 추가, reason 재생성)으로만 변경된다. 유저 피드백은 인덱스에 포함되지 않으므로 갱신 불필요.

> **`/admin/reload` 폐기**: ARCHITECTURE.md에 있던 hot-reload 엔드포인트는 이 설계에서 제거한다. 인덱스 갱신은 rebuild+redeploy로 대체된다.

**MVP (수동):**
```
새 데이터 Supabase 반영
  → python scripts/build_index.py  (로컬, ~5-10분)
  → docker build + deploy           (Render 자동 배포)
```

**Phase 2 (자동화):**
```
GitHub Actions daily cron (KST 07:00)
  → build_index.py 실행
  → data/index.pkl을 Supabase Storage 업로드
  → Render deploy hook 트리거
```

**인덱스 신선도 모니터링:**
- `/health` 응답에 `index_built_at`, `total_books`, `total_reasons`, `version` 포함
- pkl bundle: `{"index": VectorIndex, "meta": books_meta, "built_at": ISO timestamp, "version": "v3-float16"}`
- `build_index.py`는 저장 전 벡터 차원 검증 (모든 벡터 dim=2000 assert)

## 파일 변경 목록

| 파일 | 변경 내용 |
|------|-----------|
| `scripts/build_index.py` | **신규** — 독립 실행 스크립트. 자체 retry(3회, backoff 10초) + sleep(1초/페이지) 포함. 기존 loader.py의 `_paginated_fetch`와 별개 |
| `engine/loader.py` | pkl 로드 방식으로 교체 |
| `engine/index.py` | float16 지원 (add_book에서 dtype 변환) |
| `Dockerfile` | `--workers 1`, `COPY data/ data/` 추가 |
| `main.py` | health에 `index_built_at` 추가 |
| `.gitignore` | `data/index.pkl` 추가 |
| `.dockerignore` | `data/index.pkl`을 ignore 목록에서 **빼서** Docker 이미지에 포함되게 함 |

## 데이터 플로우 (변경 후)

```
[빌드 타임 — 로컬]
  Supabase DB
    → scripts/build_index.py (retry + sleep, ~5-10분)
    → data/index.pkl (~170MB, float16)

[배포]
  docker build (pkl 포함)
    → Render/Fly.io

[런타임 — 서버]
  시작: data/index.pkl → 메모리 (~2초)
  요청: GET /recommend/{user_id}
    → Supabase에서 user_books만 fetch (1회)
    → 인메모리 인덱스로 스코어링
    → 응답
```

## 스케일 전망

| 규모 | reason 건수 | float16 메모리 | 1워커 총 | 대응 |
|------|-------------|----------------|----------|------|
| 현재 2,510권 | 33,824 | ~172MB | ~272MB | 이 설계로 충분 (Render 512MB) |
| 5,000권 | ~67,000 | ~335MB | ~435MB | Render 512MB 내 가능하나 빠듯 |
| 7,000권 | ~94,000 | ~470MB | ~570MB | Render 512MB 초과 → 유료 전환 |
| 10,000권+ | - | - | - | pgvector 서버사이드 검색으로 아키텍처 전환 |

## 리스크

| 리스크 | 심각도 | 대응 |
|--------|--------|------|
| build_index.py 실행 중 Supabase 다운 | 낮음 | retry 3회 + 수동 재실행. 서비스는 기존 pkl로 운영 중 |
| pkl 포함으로 Docker 이미지 커짐 (~170MB+) | 낮음 | Render/Fly.io 이미지 크기 제한 없음. 빌드 시간 약간 증가 |
| float16 정밀도 손실 | 무시 | 정규화 벡터 내적 오차 ±0.001, 랭킹 변동 없음 |
| data/index.pkl이 git에 없어 CI/CD 빌드 불가 | 중간 | Phase 2에서 GitHub Actions로 빌드 자동화, 또는 Supabase Storage에서 다운로드 |
| Phase 2 pkl 원격 다운로드 시 pickle 보안 | 중간 | pickle.load()는 임의 코드 실행 가능. Phase 2에서는 numpy .npz + JSON 포맷으로 전환하거나, pkl 파일 해시 서명 검증 추가 |
