"""Pydantic models - مطابق با schema واقعی دیتابیس"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class AIModel(BaseModel):
    id: Optional[int] = None
    name: str
    display_name: str
    api_key: str  # ورودی
    base_url: str
    model_name: str
    is_default: bool = False
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AIModelCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    display_name: str = Field(..., min_length=1, max_length=100)
    api_key: str = Field(..., min_length=5)
    base_url: str = Field(..., min_length=8)
    model_name: str = Field(..., min_length=1)


class BotSetting(BaseModel):
    key: str
    value: str
    updated_at: Optional[datetime] = None


class UserOut(BaseModel):
    """خروجی API برای نمایش کاربر — مطابق جدول users ربات"""
    id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    is_admin: bool = False
    is_banned: bool = False
    messages_count: int = 0
    joined_date: Optional[str] = None
    last_active: Optional[str] = None


class DashboardStats(BaseModel):
    total_users: int
    total_messages: int
    active_models: int
    today_messages: int


class LoginPayload(BaseModel):
    username: str
    password: str
