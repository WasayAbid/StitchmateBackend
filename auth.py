"""
Authentication endpoints for user signup, login, and Google OAuth
"""
from fastapi import APIRouter, HTTPException, Depends, Header, Body
from pydantic import BaseModel, EmailStr, validator
from typing import Optional
from supabase_client import supabase
import os
import time

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _resolve_primary_role(roles: list[str]) -> str:
    """Match frontend authRoles.ts: admin > tailor > rider > user."""
    s = {r for r in roles if r}
    if "admin" in s:
        return "admin"
    if "tailor" in s:
        return "tailor"
    if "rider" in s:
        return "rider"
    return "user"


def _fetch_primary_role(user_id: str) -> str:
    role_result = supabase.table("user_roles").select("role").eq("user_id", user_id).execute()
    rows = role_result.data or []
    if not rows:
        return "user"
    return _resolve_primary_role([r["role"] for r in rows if r.get("role")])

# Request Models
class SignUpRequest(BaseModel):
    fullName: str
    username: Optional[str] = None
    email: EmailStr
    password: str
    
    @validator('password')
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        if not any(char.isdigit() for char in v):
            raise ValueError('Password must contain at least one number')
        if not any(char.isupper() for char in v):
            raise ValueError('Password must contain at least one uppercase letter')
        return v
    
    @validator('fullName')
    def validate_full_name(cls, v):
        if len(v.strip()) < 2:
            raise ValueError('Full name must be at least 2 characters long')
        return v.strip()

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class GoogleSignInRequest(BaseModel):
    access_token: Optional[str] = None
    id_token: Optional[str] = None

# Dependency to get current user from JWT token
async def get_current_user(authorization: Optional[str] = Header(None)):
    """
    Extract and validate JWT token from Authorization header
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    
    try:
        # Extract token from "Bearer <token>"
        token = authorization.replace("Bearer ", "")
        
        # Verify token with Supabase
        user_response = supabase.auth.get_user(token)
        
        if not user_response.user:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        
        return user_response.user
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")

@router.post("/signup")
async def signup(request: SignUpRequest):
    """
    Register a new user with fullName, username, email, and password.
    Passwords are automatically hashed by Supabase.
    """
    try:
        # Check if username already exists (if provided)
        if request.username:
            try:
                existing_profile = supabase.table("profiles").select("username").eq("username", request.username).execute()
                if existing_profile.data:
                    raise HTTPException(status_code=400, detail="Username already taken")
            except Exception as e:
                error_msg = str(e)
                if "PGRST205" in error_msg or "schema cache" in error_msg.lower():
                    raise HTTPException(
                        status_code=500,
                        detail="Database schema not found. Please ensure migrations have been applied in Supabase. "
                               "Run the migrations from bolt-stichmate/supabase/migrations/ in your Supabase SQL Editor, "
                               "or refresh the PostgREST schema cache in Supabase Dashboard → Settings → API."
                    )
                raise
        
        # Sign up user with Supabase Auth
        # Supabase automatically hashes passwords
        signup_response = supabase.auth.sign_up({
            "email": request.email,
            "password": request.password,
            "options": {
                "data": {
                    "full_name": request.fullName,
                    "username": request.username or None
                }
            }
        })
        
        if not signup_response.user:
            raise HTTPException(status_code=400, detail="Failed to create user account")
        
        # Wait a moment for the trigger to create the profile
        time.sleep(0.5)
        
        # Update profile with username if provided
        if request.username:
            try:
                supabase.table("profiles").update({
                    "username": request.username
                }).eq("user_id", signup_response.user.id).execute()
            except Exception as e:
                error_msg = str(e)
                if "PGRST205" in error_msg or "schema cache" in error_msg.lower() or "Could not find the table" in error_msg:
                    raise HTTPException(
                        status_code=500,
                        detail="Database schema not found. Please ensure migrations have been applied in Supabase. "
                               "See DATABASE_SETUP.md for instructions. "
                               "Run the migrations from bolt-stichmate/supabase/migrations/ in your Supabase SQL Editor, "
                               "then refresh the PostgREST schema cache in Supabase Dashboard → Settings → API."
                    )
                raise
        
        # Get user profile data
        try:
            profile_data = supabase.table("profiles").select("*").eq("user_id", signup_response.user.id).single().execute()
        except Exception as e:
            error_msg = str(e)
            if "PGRST205" in error_msg or "schema cache" in error_msg.lower() or "Could not find the table" in error_msg:
                raise HTTPException(
                    status_code=500,
                    detail="Database schema not found. Please ensure migrations have been applied in Supabase. "
                           "See DATABASE_SETUP.md for instructions. "
                           "Run the migrations from bolt-stichmate/supabase/migrations/ in your Supabase SQL Editor, "
                           "then refresh the PostgREST schema cache in Supabase Dashboard → Settings → API."
                )
            # If profile doesn't exist yet, wait a bit more and retry
            time.sleep(1)
            profile_data = supabase.table("profiles").select("*").eq("user_id", signup_response.user.id).single().execute()
        
        # Get user role
        try:
            role_data = supabase.table("user_roles").select("role").eq("user_id", signup_response.user.id).single().execute()
            role = role_data.data["role"] if role_data.data else "user"
        except Exception as e:
            error_msg = str(e)
            if "PGRST205" in error_msg or "schema cache" in error_msg.lower() or "Could not find the table" in error_msg:
                raise HTTPException(
                    status_code=500,
                    detail="Database schema not found. Please ensure migrations have been applied in Supabase. "
                           "See DATABASE_SETUP.md for instructions."
                )
            role = "user"  # Default role if role table doesn't exist yet
        
        return {
            "message": "Signup successful",
            "user": {
                "id": signup_response.user.id,
                "email": signup_response.user.email,
                "fullName": request.fullName,
                "username": request.username,
                "role": role
            },
            "access_token": signup_response.session.access_token if signup_response.session else None,
            "refresh_token": signup_response.session.refresh_token if signup_response.session else None
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Signup failed: {str(e)}")

@router.post("/login")
async def login(request: LoginRequest):
    """
    Login user with email and password.
    Returns JWT access token and refresh token.
    """
    try:
        # Sign in with Supabase Auth
        # Supabase handles password verification
        login_response = supabase.auth.sign_in_with_password({
            "email": request.email,
            "password": request.password
        })
        
        if not login_response.user:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        if not login_response.session:
            raise HTTPException(status_code=401, detail="Session creation failed")
        
        # Get user profile data (use limit(1) to avoid PGRST116 when 0 rows)
        profile_result = supabase.table("profiles").select("*").eq("user_id", login_response.user.id).limit(1).execute()
        profile = profile_result.data[0] if profile_result.data and len(profile_result.data) > 0 else None
        
        # Create profile if it doesn't exist (e.g., user created before migrations)
        if not profile:
            try:
                full_name = (login_response.user.user_metadata or {}).get("full_name") or login_response.user.email or "User"
                supabase.table("profiles").insert({
                    "user_id": login_response.user.id,
                    "full_name": full_name
                }).execute()
                profile = {"full_name": full_name, "username": None}
            except Exception:
                profile = {"full_name": login_response.user.email or "User", "username": None}
        
        role_result = supabase.table("user_roles").select("role").eq("user_id", login_response.user.id).execute()
        role = _resolve_primary_role(
            [r["role"] for r in (role_result.data or []) if r.get("role")]
        ) if role_result.data else "user"

        # Create role if it doesn't exist
        if not role_result.data or len(role_result.data) == 0:
            try:
                supabase.table("user_roles").insert({
                    "user_id": login_response.user.id,
                    "role": "user"
                }).execute()
                role = "user"
            except Exception:
                role = "user"
        
        return {
            "message": "Login successful",
            "user": {
                "id": login_response.user.id,
                "email": login_response.user.email,
                "fullName": profile.get("full_name", "") if isinstance(profile, dict) else "",
                "username": profile.get("username") if isinstance(profile, dict) else None,
                "role": role
            },
            "access_token": login_response.session.access_token,
            "refresh_token": login_response.session.refresh_token
        }
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        if "Invalid login credentials" in error_msg or "Email not confirmed" in error_msg:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        raise HTTPException(status_code=500, detail=f"Login failed: {str(e)}")

@router.post("/google")
async def google_signin(request: GoogleSignInRequest = Body(default=GoogleSignInRequest(access_token=None, id_token=None))):
    """
    Sign in with Google OAuth.
    Returns OAuth URL for frontend to redirect to.
    """
    try:
        # Get redirect URL - frontend runs on 8080 by default
        redirect_url = os.getenv("GOOGLE_REDIRECT_URL", "http://localhost:8080/auth/callback")
        
        # Method 1: Using Supabase OAuth redirect flow
        oauth_response = supabase.auth.sign_in_with_oauth({
            "provider": "google",
            "options": {
                "redirect_to": redirect_url
            }
        })
        
        # Method 2: Direct token verification (if using Google Sign-In SDK)
        if request and request.id_token:
            # Verify Google ID token and create/get user
            # This is a simplified version - you may need Google API client library
            try:
                # For now, we'll use Supabase's OAuth flow
                # In production, decode id_token, extract email, and create/get user
                return {
                    "message": "Google OAuth initiated",
                    "url": oauth_response.url if hasattr(oauth_response, 'url') else None,
                    "note": "Use Supabase OAuth redirect flow for full implementation"
                }
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Google token verification failed: {str(e)}"
                )
        
        # Extract URL from response (structure may vary by Supabase client version)
        oauth_url = None
        if hasattr(oauth_response, 'url'):
            oauth_url = oauth_response.url
        elif hasattr(oauth_response, 'model_dump'):
            data = oauth_response.model_dump()
            oauth_url = data.get('url') if isinstance(data, dict) else None
        elif isinstance(oauth_response, dict):
            oauth_url = oauth_response.get('url')
        
        return {
            "message": "Google sign-in initiated",
            "url": oauth_url
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Google sign-in failed: {str(e)}")

@router.get("/me")
async def get_current_user_info(user = Depends(get_current_user)):
    """
    Get current authenticated user's information.
    Requires valid JWT token in Authorization header.
    """
    try:
        # Get user profile data (use limit(1) to avoid PGRST116 when 0 rows)
        profile_result = supabase.table("profiles").select("*").eq("user_id", user.id).limit(1).execute()
        profile = profile_result.data[0] if profile_result.data and len(profile_result.data) > 0 else {}
        
        role = _fetch_primary_role(user.id)

        return {
            "id": user.id,
            "email": user.email,
            "fullName": profile.get("full_name", ""),
            "username": profile.get("username"),
            "role": role,
            "phone": profile.get("phone"),
            "avatar_url": profile.get("avatar_url")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch user info: {str(e)}")

@router.post("/refresh")
async def refresh_token(refresh_token: str):
    """
    Refresh access token using refresh token.
    """
    try:
        refresh_response = supabase.auth.refresh_session(refresh_token)
        
        if not refresh_response.session:
            raise HTTPException(status_code=401, detail="Token refresh failed")
        
        return {
            "access_token": refresh_response.session.access_token,
            "refresh_token": refresh_response.session.refresh_token
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Token refresh failed: {str(e)}")
