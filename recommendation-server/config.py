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

DEFAULT_RECOMMEND_LIMIT = 10
DEFAULT_SIMILAR_LIMIT = 10

# Two-stage 추천 파라미터
STAGE1_TOP_N = 150  # 벡터화 stage2 의 후보 reason 스택 메모리(무료 512MB) 안전선.
# 700 은 후보 reason 임시할당 peak ~250MB → 인덱스 277MB 와 합쳐 OOM(실측 사고).
# 150 은 peak 델타 ~54MB → 인덱스 합 ~331MB 안전. 품질: full 대비 top-10 일치 10/10
# (W_DESC=3 이 최대라 고득점=desc 상위, 선필터 누락 없음).
CACHE_TOP_N = 50

_sb_client = None


def get_supabase():
    global _sb_client
    if _sb_client is None:
        from supabase import create_client
        _sb_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _sb_client
