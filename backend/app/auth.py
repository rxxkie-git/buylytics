import os
import json
from pydantic import BaseModel
from datetime import datetime, timedelta
import httpx
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .config import settings

security = HTTPBearer()

class ClerkUser(BaseModel):
    user_id: str
    email: str | None
    session_id: str

_jwks_cache = None
_jwks_cache_time = None

def _get_jwks():
    global _jwks_cache, _jwks_cache_time
    now = datetime.utcnow()
    if _jwks_cache and _jwks_cache_time and (now - _jwks_cache_time) < timedelta(hours=1):
        return _jwks_cache
    
    url = f"{settings.clerk_issuer.rstrip('/')}/.well-known/jwks.json"
    response = httpx.get(url, timeout=5.0)
    response.raise_for_status()
    _jwks_cache = response.json()
    _jwks_cache_time = now
    return _jwks_cache

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> ClerkUser:
    token = credentials.credentials
    try:
        jwks = _get_jwks()
        unverified_header = jwt.get_unverified_header(token)
        rsa_key = {}
        for key in jwks["keys"]:
            if key["kid"] == unverified_header["kid"]:
                rsa_key = key
                break
        
        if not rsa_key:
            raise HTTPException(status_code=401, detail="Invalid key")
        
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(rsa_key))
        
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"verify_aud": False},
            issuer=settings.clerk_issuer
        )
        
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token missing subject")
        
        return ClerkUser(
            user_id=user_id,
            email=payload.get("email"),
            session_id=payload.get("sid", "")
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")
