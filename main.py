from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

# Configure logging
LOG_FILE_PATH = Path("logs/application.log")
LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

# Create logger
logger = logging.getLogger("main.py")
logger.setLevel(logging.INFO)

# Create rotating file handler (5MB max, keep 3 backup files)
file_handler = RotatingFileHandler(
    LOG_FILE_PATH,
    maxBytes=5*1024*1024,
    backupCount=3
)
file_handler.setLevel(logging.INFO)

# Create console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Create formatter
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Add handlers to logger
logger.addHandler(file_handler)
logger.addHandler(console_handler)

#####################################################################################################################
# Import routes
from routes import pages, api, health

#####################################################################################################################
# Lifespan context manager for startup/shutdown events

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create necessary directories
    directories = [
        Path("MRS/temp"),
        Path("MRS/media"),
        Path("MRS/cache"),
        Path("MRS/database"),
        Path("logs"),
    ]
    
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        logger.info(f"Directory created/verified: {directory}")

    # Compile the C Levenshtein kernel if it is missing or older than its
    # source. Idempotent and cheap -- after the first run this is a single
    # stat() call -- so it belongs here as well as in setup.bat: a clone that
    # was started with `python main.py` and never went through setup still gets
    # the fast path.
    #
    # Deliberately not fatal. ensure_built() returns False when no compiler is
    # installed, and src.Levenshtein falls back to the pure-Python
    # implementation. A missing toolchain should cost speed, not availability.
    from src.clevenshtein.build import ensure_built
    ensure_built(quiet=True)

    from src.Levenshtein import backend_report, using_native
    for line in backend_report().splitlines():
        (logger.info if using_native() else logger.warning)(line)

    logger.info("Application startup complete - all services initialized")
    
    yield  # Application runs here
    
    # Shutdown: Cleanup if needed
    logger.info("Application shutting down - performing cleanup")

#####################################################################################################################
# Initialize FastAPI app with lifespan

app = FastAPI(
    title="Medicine Recognition System",
    description="API for medicine image recognition and information retrieval",
    version="2.0.0",
    lifespan=lifespan
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

#####################################################################################################################
# Include routers

app.include_router(pages.router)
app.include_router(api.router)
app.include_router(health.router)

#####################################################################################################################
# Error Handlers

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Custom HTTP exception handler"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )

#####################################################################################################################

if __name__ == "__main__":
    PORT = 8000
    print("🚀 Starting MRS server!")
    print(f"🌐 Visit: http://localhost:{PORT}")
    print()
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        log_level="info"
    )