from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 2000

# v3 스코어링 가중치 — H10_no_l1 (스펙 섹션 5.2)
# L1/L2는 binary처럼 작동 (cosine 1.0/0.3 양극화)하여 가중치 효과 없음.
# 18 페르소나 + 50 랜덤 검증에서 이 가중치가 최선으로 확인됨.
W_REASON = 2.0
W_DESC = 3.0
W_L1 = 0.0
W_L2 = 0.0
W_FB_DESC = 2.0

# 피드백 있는 책의 reason 가중치 감소 (피드백이 주 신호)
REASON_WEIGHT_WITH_FB = 0.5
REASON_WEIGHT_WITHOUT_FB = 1.0
FB_REASON_WEIGHT = 3.0

# 추천 화면 행동 신호 (2026-07-02, Eden 승인 — 담기/관심없음이 곧 취향 학습).
# 두 스테이지 모두 desc 공간 항으로만 반영(벡터 분리 원칙 — reason 공간은 실제
# 피드백 전용). 앵커: 두 스테이지 다 '좋았어요' desc 항이 3.0.
# - wishlist(읽고싶어요): 아직 안 읽은 약한 긍정 → good(3.0)의 1/3.
# - not_interested(관심없음): 안 읽고 거른 것 — 읽고 실망한 bad(-1.5)의 절반.
#   (해당 책 자체는 후보에서 완전 제외되고, 이 항은 '비슷한 결'을 낮추는 용도.)
W_WISHLIST_DESC = 1.0
W_NOT_INTERESTED_DESC = -0.75

# 후보 품질 등급별 down-weight (source_tier). 타이브레이크 수준 — niche 역전 방지(E2E 튜닝).
# positive-part 곱셈으로만 적용(음수 점수 미변경 → 부호 안전). rich=무감점.
SOURCE_TIER_PENALTY = {"rich": 1.0, "kakao_desc": 0.95, "minimal": 0.85}
# /similar(항상 보이는 정밀 surface)는 minimal tier 노출 제외. /recommend(커버리지)는 유지.
SIMILAR_MIN_TIER = "kakao_desc"

DEFAULT_RECOMMEND_LIMIT = 10
DEFAULT_SIMILAR_LIMIT = 10

# Two-stage 추천 파라미터
STAGE1_TOP_N = 700  # stage2 정밀 스코어링이 보는 후보 수. 2026-07-02 Eden 승인으로
# 150→700 확대: 실인덱스 80명 평가에서 GT(stage2 전권) 대비 recall@20 이
# 현실형(취향 뭉침) 95%→98.9%, 랜덤형 77%→91%. 비용은 s2 레이턴시(쓰기경로
# ~+2s, 읽기는 캐시라 무영향). 메모리는 과거 700 OOM(무분할 stage2, 후보가
# reason-rich 편향이라 transient 175MB 실측)이 STAGE2_CHUNK 블록 처리로 해소됨
# — 블록당 O(150) 고정이라 top_n 과 무관하게 안전.
CACHE_TOP_N = 50

_sb_client = None


def get_supabase():
    global _sb_client
    if _sb_client is None:
        from supabase import create_client
        _sb_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _sb_client
