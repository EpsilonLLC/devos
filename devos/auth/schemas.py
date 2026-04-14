import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


class SignupRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Email is required")
        v = v.strip()
        if "@" not in v:
            raise ValueError("Email must contain '@' and a valid domain")
        parts = v.split("@")
        if len(parts) != 2 or not parts[1] or "." not in parts[1]:
            raise ValueError("Email must contain '@' and a valid domain")
        return v

    @field_validator("password")
    @classmethod
    def validate_password_length(cls, v: str) -> str:
        if not v:
            raise ValueError("Password is required")
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email_present(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Email is required")
        return v.strip()

    @field_validator("password")
    @classmethod
    def validate_password_present(cls, v: str) -> str:
        if not v:
            raise ValueError("Password is required")
        return v


class UserResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: str
    created_at: datetime


class SignupResponse(BaseModel):
    user: UserResponse
    token: str


class LoginResponse(BaseModel):
    user: UserResponse
    token: str


class SessionResponse(BaseModel):
    user: UserResponse
