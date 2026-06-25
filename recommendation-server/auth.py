from __future__ import annotations

import threading
import requests
from fastapi import Header, HTTPException
from jose import jwt, JWTError
from config import SUPABASE_JWT_SECRET, SUPABASE_URL

# Supabase 가 비대칭 JWT 서명키(ES256)로 이전 → access_token 은 ES256 으로 서명된다.
# 레거시 HS256 공유시크릿으론 검증 불가하므로 JWKS 공개키로 검증한다. (구 HS256
# 토큰 호환을 위해 alg=HS256 은 기존 시크릿 폴백 유지 — 단 시크릿이 설정된 경우만.)
_JWKS_URL = f"{SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
_jwks_lock = threading.Lock()
_jwks_cache = None


def _load_jwks(force: bool = False):
    global _jwks_cache
    with _jwks_lock:
        if _jwks_cache is None or force:
            resp = requests.get(_JWKS_URL, timeout=10)
            resp.raise_for_status()
            _jwks_cache = resp.json()
        return _jwks_cache


def _find_key(kid: str):
    for k in _load_jwks().get("keys", []):
        if k.get("kid") == kid:
            return k
    # kid 미발견 → 서명키 회전 가능성. 1회 강제 새로고침 후 재시도.
    for k in _load_jwks(force=True).get("keys", []):
        if k.get("kid") == kid:
            return k
    return None


def verify_jwt(authorization: str = Header(...)) -> str:
    """Authorization 헤더의 Supabase JWT 를 검증하고 user_id(sub) 반환.

    ES256(비대칭, JWKS 공개키) + HS256(레거시 공유시크릿) 둘 다 지원한다.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid authorization header")
    token = authorization[7:]

    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise HTTPException(401, f"Invalid token header: {e}")

    alg = header.get("alg", "")
    try:
        if alg == "HS256":
            # 빈 시크릿이면 거부 — "" 로 서명한 위조 토큰이 통과하는 우회 방지.
            if not SUPABASE_JWT_SECRET:
                raise HTTPException(401, "HS256 tokens not accepted")
            payload = jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"],
                                 options={"verify_aud": False})
        elif alg in ("ES256", "RS256"):
            key = _find_key(header.get("kid", ""))
            if key is None:
                raise HTTPException(401, "Unknown signing key (kid)")
            payload = jwt.decode(token, key, algorithms=[alg],
                                 options={"verify_aud": False})
        else:
            raise HTTPException(401, f"Unsupported token alg: {alg}")
    except JWTError as e:
        raise HTTPException(401, f"JWT verification failed: {e}")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "JWT missing sub claim")
    return user_id
