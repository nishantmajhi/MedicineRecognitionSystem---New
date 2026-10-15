import cv2
import json
import numpy as np
import logging
from pathlib import Path
from utils.helper.uploads import safe_join
from typing import Any, List, Optional


def _to_plain_types(value: Any) -> Any:
    """
    Convert numpy scalars and arrays into things json can serialise.

    EasyOCR returns bounding boxes as numpy integers and confidences as numpy
    floats. json.dump refuses both, so they are converted here rather than the
    cache falling back to a Python repr that only eval() could read.
    """
    if isinstance(value, np.generic):
        return value.item()

    if isinstance(value, np.ndarray):
        return [_to_plain_types(item) for item in value.tolist()]

    if isinstance(value, (list, tuple)):
        return [_to_plain_types(item) for item in value]

    if isinstance(value, dict):
        return {str(key): _to_plain_types(item) for key, item in value.items()}

    return value

import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

import easyocr

logger = logging.getLogger("image_processor.py")

class ImageProcessor:
    """Handles image processing and OCR operations"""
    
    def __init__(self, media_dir: str = "MRS/media", cache_dir: str = "MRS/cache"):
        """
        Initialize ImageProcessor
        
        Args:
            media_dir: Directory to store processed images
            cache_dir: Directory to store OCR cache files
        """
        self.media_dir = Path(media_dir)
        self.cache_dir = Path(cache_dir)
        self.min_confidence = 25.00
        self.min_text_length = 3
        self._ocr_reader = None
        
        # Create directories if they don't exist
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    @property
    def ocr_reader(self):
        """Lazy load OCR reader"""
        if self._ocr_reader is None:
            logger.info("Initializing EasyOCR reader...")
            self._ocr_reader = easyocr.Reader(['en'], gpu=False, model_storage_directory='MRS/model/', download_enabled=False)
        return self._ocr_reader
    
    def process_image(self, image: np.ndarray, file_name: str, md5_hash: str) -> Optional[List[str]]:
        """
        Process image and perform OCR with caching
        
        Args:
            image: OpenCV image array
            file_name: Original filename
            md5_hash: MD5 hash of the image
            
        Returns:
            List of cleaned text strings or None on failure
        """
        try:
            # safe_join refuses anything that is not a single plain filename.
            # The caller already passes a generated name, so a failure here
            # means a caller is passing something it should not — most likely a
            # client-supplied filename, which is how this directory could
            # previously be written to from outside.
            media_path = safe_join(self.media_dir, file_name)
            cache_path = safe_join(self.cache_dir, f"{md5_hash}.ocr")
            
            # Save image if not exists
            self._save_image(image, media_path)
            
            # Get OCR result (from cache or perform new)
            ocr_result = self._get_ocr_result(image, cache_path, md5_hash)
            
            # Clean and return the OCR result
            cleaned_result = self._clean_ocr_result(ocr_result)
            logger.info(f"OCR processing completed. Found {len(cleaned_result)} valid texts")
            
            return cleaned_result
            
        except Exception as e:
            logger.exception(f"Exception in process_image: {str(e)}")
            logger.error("Image processing aborted!")
            return None
    
    def _save_image(self, image: np.ndarray, media_path: Path) -> None:
        """Save image to media folder if not exists"""
        if not media_path.exists():
            cv2.imwrite(str(media_path), image)
            logger.info(f"Image saved to media folder: {media_path}")
        else:
            logger.info(f"Image already exists in media folder: {media_path}")
    
    def _get_ocr_result(self, image: np.ndarray, cache_path: Path, hash_digest: str) -> List:
        """
        Get an OCR result from the cache, or run OCR if there isn't a usable one.

        A cache file that cannot be read is treated as absent rather than as a
        failure. It previously aborted the whole request: the file was passed to
        eval(), and anything that did not parse raised out of process_image(),
        which returned None and became a 500. A stale or truncated cache entry
        should cost a re-run, not the request.
        """
        if not cache_path.exists():
            logger.info("Cache not found. Performing OCR...")
            return self._perform_ocr(image, hash_digest)

        cached = self._read_cache(cache_path)

        if cached is None:
            logger.warning(f"Ignoring unreadable OCR cache: {cache_path.name}")
            return self._perform_ocr(image, hash_digest)

        logger.info(f"Using cached OCR result: {cache_path.name}")
        return cached

    @staticmethod
    def _read_cache(cache_path: Path) -> Optional[List]:
        """
        Read a cached OCR result, or None if the file cannot be used.

        The cache is JSON. It used to hold repr() of the EasyOCR result and was
        read back with eval(), which executes whatever the file contains: any
        way of writing to the cache directory was a way of running code in this
        process, and until the upload path was fixed a crafted filename was
        exactly that. tools/convert_ocr_cache.py converts files written in the
        old format.
        """
        try:
            with open(cache_path, 'r', encoding='utf-8') as cache:
                loaded = json.load(cache)
        except (OSError, ValueError) as error:
            logger.warning(f"Could not read OCR cache {cache_path.name}: {error}")
            return None

        if not isinstance(loaded, list):
            logger.warning(f"OCR cache {cache_path.name} is not a list of detections")
            return None

        return loaded

    @staticmethod
    def _write_cache(cache_path: Path, result: List) -> None:
        """Store an OCR result as JSON, converting numpy types to plain ones."""
        try:
            with open(cache_path, 'w', encoding='utf-8') as cache:
                json.dump(_to_plain_types(result), cache)
        except (OSError, TypeError, ValueError) as error:
            # A cache is an optimisation; failing to write one must not fail the
            # request that produced the result.
            logger.warning(f"Could not write OCR cache {cache_path.name}: {error}")
    
    def _perform_ocr(self, image: np.ndarray, hash_digest: str) -> List:
        """
        Perform OCR on image using EasyOCR and cache the result
        
        Args:
            image: OpenCV image array
            hash_digest: MD5 hash for cache filename
            
        Returns:
            OCR result list
        """
        try:
            logger.info("Performing OCR on image...")
            result = self.ocr_reader.readtext(image)

            # Cache the OCR result
            self._write_cache(safe_join(self.cache_dir, f"{hash_digest}.ocr"), result)

            logger.info(f"OCR completed and cached: {hash_digest}.ocr")
            logger.info(f"OCR found {len(result)} text regions")
            
            return result
            
        except Exception as e:
            logger.exception(f"Exception in _perform_ocr: {str(e)}")
            raise
    
    def _clean_ocr_result(self, ocr_result: List) -> List[str]:
        """
        Filter OCR results based on confidence and text length
        
        Args:
            ocr_result: EasyOCR result list [[bbox, text, confidence], ...]
            
        Returns:
            List of cleaned text strings
        """
        good_texts = []
        
        try:
            for item in ocr_result:
                confidence = item[2] * 100
                text = item[1]
                
                if len(text) > self.min_text_length and confidence > self.min_confidence:
                    good_texts.append(text.lower())
            
            logger.info(f"Cleaned OCR result: {good_texts}")
            return good_texts
            
        except Exception as e:
            logger.exception(f"Exception in _clean_ocr_result: {str(e)}")
            return []
    
    def set_confidence_threshold(self, threshold: float) -> None:
        """Set minimum confidence threshold for OCR results"""
        self.min_confidence = threshold
        logger.info(f"Confidence threshold set to: {threshold}")
    
    def set_min_text_length(self, length: int) -> None:
        """Set minimum text length for OCR results"""
        self.min_text_length = length
        logger.info(f"Minimum text length set to: {length}")