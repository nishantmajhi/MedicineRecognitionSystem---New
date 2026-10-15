from fastapi import APIRouter
from datetime import datetime

router = APIRouter(tags=["System"])

@router.get("/health")
async def health_check():
    """
    Health check endpoint for monitoring
    
    Returns:
        JSON with status and timestamp
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0"
    }