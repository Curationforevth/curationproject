from __future__ import annotations

from fastapi import Header, HTTPException
from jose import jwt, JWTError
from config import SUPABASE_JWT_SECRET


def verify_jwt(authorization: str = Header(...)) -> str:
    """Authorization 헤더에서 Supabase JWT를 검증하고 user_id(sub) 반환."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid authorization header")
    token = authorization[7:]
    try:
        payload = jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"],
                             options={"verify_aud": False})
    except JWTError as e:
        raise HTTPException(401, f"JWT verification failed: {e}")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "JWT missing sub claim")
    return user_id
