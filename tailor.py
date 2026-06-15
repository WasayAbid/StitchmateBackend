"""
Tailor application endpoints
"""
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel, EmailStr, validator, HttpUrl
from typing import Optional, List
from supabase_client import supabase
from auth import get_current_user

router = APIRouter(prefix="/api/tailor", tags=["tailor"])

class TailorApplicationRequest(BaseModel):
    fullName: str
    phoneNumber: str
    location: str  # This maps to shop_address in database
    tailoringExperience: int  # This maps to years_experience in database
    skills: Optional[str] = None  # Comma-separated string, maps to specializations array
    portfolioImages: Optional[List[str]] = None  # Array of URLs, we'll use first one as portfolio_url
    
    @validator('fullName')
    def validate_full_name(cls, v):
        if len(v.strip()) < 2:
            raise ValueError('Full name must be at least 2 characters long')
        return v.strip()
    
    @validator('phoneNumber')
    def validate_phone(cls, v):
        # Basic phone validation - at least 10 digits
        digits = ''.join(filter(str.isdigit, v))
        if len(digits) < 10:
            raise ValueError('Phone number must contain at least 10 digits')
        return v.strip()
    
    @validator('location')
    def validate_location(cls, v):
        if len(v.strip()) < 10:
            raise ValueError('Location/address must be at least 10 characters long')
        return v.strip()
    
    @validator('tailoringExperience')
    def validate_experience(cls, v):
        if v < 0:
            raise ValueError('Experience years cannot be negative')
        return v

@router.post("/apply")
async def apply_as_tailor(
    application: TailorApplicationRequest,
    user = Depends(get_current_user)
):
    """
    Submit a tailor application for the logged-in user.
    
    Required fields:
    - fullName: Full name of the applicant
    - phoneNumber: Contact phone number
    - location: Shop address/location
    - tailoringExperience: Years of experience
    
    Optional fields:
    - skills: Comma-separated list of specializations
    - portfolioImages: Array of portfolio image URLs (first one used as portfolio_url)
    
    Returns the created application with status "pending".
    """
    try:
        # Check if user already has a pending or approved application
        existing_app = supabase.table("tailor_applications").select("id, status").eq("user_id", user.id).execute()
        
        if existing_app.data:
            existing_status = existing_app.data[0].get("status")
            if existing_status == "pending":
                raise HTTPException(
                    status_code=400, 
                    detail="You already have a pending application. Please wait for admin review."
                )
            elif existing_status == "approved":
                raise HTTPException(
                    status_code=400,
                    detail="You are already an approved tailor."
                )
            # If rejected, allow re-application
        
        # Process skills - convert comma-separated string to array
        specializations = []
        if application.skills:
            specializations = [s.strip() for s in application.skills.split(",") if s.strip()]
        
        # Get portfolio URL from portfolioImages array (use first one)
        portfolio_url = None
        if application.portfolioImages and len(application.portfolioImages) > 0:
            portfolio_url = application.portfolioImages[0]
        
        # Update user profile with fullName and phone if needed
        profile_update = {}
        profile_data = supabase.table("profiles").select("*").eq("user_id", user.id).single().execute()
        
        if profile_data.data:
            current_profile = profile_data.data
            if not current_profile.get("full_name") or current_profile.get("full_name") != application.fullName:
                profile_update["full_name"] = application.fullName
            if not current_profile.get("phone") or current_profile.get("phone") != application.phoneNumber:
                profile_update["phone"] = application.phoneNumber
            
            if profile_update:
                supabase.table("profiles").update(profile_update).eq("user_id", user.id).execute()
        
        # Create tailor application
        # Note: The database uses shop_name, shop_address, years_experience, specializations
        # We map: location -> shop_address, tailoringExperience -> years_experience
        # For shop_name, we'll use fullName or generate one
        shop_name = f"{application.fullName}'s Tailoring"
        
        application_data = {
            "user_id": user.id,
            "shop_name": shop_name,
            "shop_address": application.location,
            "phone": application.phoneNumber,
            "years_experience": application.tailoringExperience,
            "specializations": specializations if specializations else None,
            "portfolio_url": portfolio_url,
            "status": "pending"
        }
        
        result = supabase.table("tailor_applications").insert(application_data).execute()
        
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to submit application")
        
        created_application = result.data[0]
        
        return {
            "message": "Tailor application submitted successfully",
            "application": {
                "id": created_application["id"],
                "userId": created_application["user_id"],
                "fullName": application.fullName,
                "phoneNumber": created_application["phone"],
                "location": created_application["shop_address"],
                "tailoringExperience": created_application["years_experience"],
                "skills": specializations,
                "portfolioImages": [portfolio_url] if portfolio_url else [],
                "applicationStatus": created_application["status"],
                "createdAt": created_application["created_at"]
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        if "duplicate" in error_msg.lower() or "unique" in error_msg.lower():
            raise HTTPException(
                status_code=400,
                detail="You already have an application. Please wait for admin review."
            )
        raise HTTPException(status_code=500, detail=f"Failed to submit application: {str(e)}")

@router.get("/application/status")
async def get_application_status(user = Depends(get_current_user)):
    """
    Get the current user's tailor application status.
    """
    try:
        result = supabase.table("tailor_applications").select("*").eq("user_id", user.id).order("created_at", desc=True).limit(1).execute()
        
        if not result.data or len(result.data) == 0:
            return {
                "hasApplication": False,
                "application": None
            }
        
        app = result.data[0]
        
        return {
            "hasApplication": True,
            "application": {
                "id": app["id"],
                "status": app["status"],
                "shopName": app["shop_name"],
                "shopAddress": app["shop_address"],
                "phone": app["phone"],
                "yearsExperience": app["years_experience"],
                "specializations": app["specializations"] or [],
                "portfolioUrl": app["portfolio_url"],
                "adminNotes": app["admin_notes"],
                "createdAt": app["created_at"],
                "reviewedAt": app["reviewed_at"]
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch application status: {str(e)}")
