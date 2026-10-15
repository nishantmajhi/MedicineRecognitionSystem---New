from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["Pages"])

@router.get("/")
async def landing_page():
    """Serve the home page"""
    return FileResponse("static/home.html")


@router.get("/favicon.ico", tags=["Icon"])
async def fav_icon():
    """Serve the favicon file"""
    return FileResponse("static/favicon.ico")