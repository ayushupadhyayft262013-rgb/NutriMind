"""Authentication utilities for JWT tokens and password hashing."""

import logging
from datetime import datetime, timedelta
import jwt
from passlib.context import CryptContext
from fastapi import Request, HTTPException, status
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

class TokenData(BaseModel):
    user_id: int | None = None

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str) -> TokenData | None:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            return None
        return TokenData(user_id=int(user_id))
    except jwt.PyJWTError:
        return None

async def get_current_user_from_cookie(request: Request) -> int | None:
    """Extract user_id from the session cookie for HTML routes."""
    token = request.cookies.get("session_token")
    if not token:
        return None
    token_data = decode_access_token(token)
    if not token_data:
        return None
    return token_data.user_id
