from fastapi import APIRouter, File, UploadFile, Request, HTTPException
from fastapi.responses import JSONResponse
from datetime import datetime
from pathlib import Path
import cv2
import numpy as np
import logging

# Import utility classes
from utils import ImageProcessor
from utils.helper import HashGenerator, DrugMatcher, DrugInformation
from utils.helper.uploads import (
    MAX_UPLOAD_BYTES,
    content_name,
    safe_join,
    temporary_name,
    validate_upload,
)

# Get logger
logger = logging.getLogger("main.py")

# Initialize service instances
image_processor = ImageProcessor()
hash_generator = HashGenerator()
drug_matcher = DrugMatcher()
drug_info_service = DrugInformation()

router = APIRouter(prefix="/api", tags=["API"])

#####################################################################################################################
# Utility Functions

def log_session_end(timestamp: str, status: str):
    """Log the end of a processing session"""
    logger.info(f"{'='*80}")
    logger.info(f"END Processing Session [{timestamp}] - Status: {status}")
    logger.info(f"{'='*80}\n")

#####################################################################################################################

@router.post("/upload")
async def upload_medicine_image(request: Request, photo: UploadFile = File(...)):
    """
    Process uploaded medicine image and extract information
    
    Args:
        photo: Uploaded image file (JPEG, PNG, etc.)
        
    Returns:
        JSON response with medicine information including:
        - genericName: Name(s) of identified medicine(s)
        - purpose: List of medicine purposes
        - do_not_use: List of usage warnings
        
    Raises:
        HTTPException: 406 if no photo received
        HTTPException: 415 if invalid image format
        HTTPException: 422 if no medicines detected
        HTTPException: 500 if internal processing error
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Bound before the try so the cleanup in the finally block can run whether
    # or not the file was ever written.
    temp_image_path = None

    # Start logging
    logger.info(f"{'='*80}")
    logger.info(f"START Processing Session [{timestamp}]")
    logger.info(f"{'='*80}")
    logger.info("Processing image...")

    try:
        # Validate file upload
        if not photo:
            logger.error("Photo not received")
            logger.info("Image processing aborted!")
            log_session_end(timestamp, "ERROR: No photo received")
            raise HTTPException(
                status_code=406,
                detail="Photo not received"
            )
        
        # Refuse an oversized body before reading it. photo.read() pulls the
        # whole upload into memory and neither FastAPI nor Starlette caps that,
        # so a single large request could exhaust it. Content-Length is a claim
        # rather than a guarantee, which is why validate_upload() checks the
        # real length again below; this only avoids buffering the obvious cases.
        declared_length = request.headers.get("content-length")

        if declared_length and declared_length.isdigit():
            if int(declared_length) > MAX_UPLOAD_BYTES:
                logger.warning(f"Rejected upload: declared {declared_length} bytes")
                log_session_end(timestamp, "REJECTED: too large")
                raise HTTPException(
                    status_code=413,
                    detail=f"The image is larger than the "
                           f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
                )

        # The client's filename is used for logging and validation only. It is
        # never used to build a path: pathlib does not confine a joined path,
        # and an absolute right-hand side replaces the directory entirely, so
        # a crafted name could write anywhere this process can write.
        contents = await photo.read()

        verdict = validate_upload(photo.filename, contents)

        if not verdict.ok:
            logger.warning(f"Rejected upload: {verdict.error}")
            log_session_end(timestamp, f"REJECTED: {verdict.error}")
            raise HTTPException(status_code=415, detail=verdict.error)

        # Decode image
        try:
            nparr = np.frombuffer(contents, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if image is None:
                raise ValueError("Failed to decode image")

            # Save under a generated name. Written to a temporary name first
            # because the digest that gives the file its lasting identity can
            # only be computed once the re-encoded image is on disk.
            temp_dir = Path("MRS/temp")
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_image_path = safe_join(temp_dir, temporary_name(verdict.extension))

            cv2.imwrite(str(temp_image_path), image)
            logger.info(f"Temporary image saved: {temp_image_path.name}")

        except HTTPException:
            raise

        except Exception as e:
            logger.error(f"Invalid image format: {str(e)}")
            log_session_end(timestamp, f"ERROR: Invalid format - {str(e)}")
            raise HTTPException(
                status_code=415,
                detail="Invalid image format"
            )

        # Generate image hash
        img_digest = hash_generator.generate_md5(str(temp_image_path))
        logger.info(f"Image hash generated: {img_digest}")

        # The stored name is the image's own digest, so the same picture
        # uploaded twice reuses one file instead of accumulating a copy per
        # request, and no part of it comes from the caller.
        stored_name = content_name(img_digest, verdict.extension)

        # Process image to extract text
        logger.info("Extracting text from image...")
        extracted_text = image_processor.process_image(image, stored_name, img_digest)
        
        # Handle extraction errors
        if extracted_text is None:
            logger.error("Internal server error during text extraction")
            log_session_end(timestamp, "ERROR_500")
            raise HTTPException(
                status_code=500,
                detail="Internal server error during processing"
            )
        
        if not extracted_text or extracted_text == '' or extracted_text == []:
            logger.warning("Cannot extract text from the given image")
            log_session_end(timestamp, "No text extracted")
            raise HTTPException(
                status_code=422,
                detail="No medicines detected in the image"
            )
        
        logger.info(f"Text extracted successfully: {extracted_text}")
        
        # Identify drugs from extracted text
        logger.info("Identifying drugs from extracted text...")
        drugs_list = drug_matcher.identify_drugs(extracted_text)
        
        if not drugs_list or len(drugs_list) == 0:
            logger.warning("No drugs identified from extracted text")
            log_session_end(timestamp, "No drugs identified")
            raise HTTPException(
                status_code=422,
                detail="No medicines detected in the image"
            )
        
        logger.info(f"Found {len(drugs_list)} drug(s): {drugs_list}")
        
        # Generate medicine information dictionary
        logger.info("Generating medicine information...")
        all_medicines_info = drug_info_service.get_multiple_drugs_info(drugs_list)
        
        # Build response data
        response_data = {
            'genericName': '',
            'purpose': [],
            'do_not_use': [],
        }
        
        for count, medicine in enumerate(all_medicines_info.values()):
            response_data['purpose'].append(medicine['purpose'].capitalize())
            response_data['do_not_use'].append(medicine['do_not_use'].capitalize())
            response_data['genericName'] += medicine['generic_name'].title()
            
            if count < len(all_medicines_info) - 1:
                response_data['genericName'] += ", "
        
        logger.info("HTTP Response prepared successfully")
        logger.info(f"Response data: {response_data}")
        log_session_end(timestamp, "SUCCESS")
        
        return JSONResponse(content=response_data, status_code=200)
    
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    
    except Exception as e:
        logger.exception(f"Unexpected error in upload_medicine_image: {str(e)}")
        log_session_end(timestamp, f"UNEXPECTED ERROR: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Unexpected server error"
        )

    finally:
        # The temporary copy has served its purpose once the digest is computed
        # and the image is in the media directory. Nothing removed it before, so
        # MRS/temp grew by one photograph per request — and for this application
        # those photographs are of people's medicine, sometimes with a
        # prescription label in frame.
        if temp_image_path is not None:
            try:
                temp_image_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                logger.warning(f"Could not remove temporary image: {cleanup_error}")