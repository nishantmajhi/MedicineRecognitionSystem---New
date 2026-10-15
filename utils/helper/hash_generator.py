import hashlib
import logging

logger = logging.getLogger("hash_generator.py")

class HashGenerator:
    @staticmethod
    def generate_md5(file_path: str) -> str:
        try:
            with open(file_path, 'rb') as f:
                file_data = f.read()
            hash_object = hashlib.md5()
            hash_object.update(file_data)
            return hash_object.hexdigest()
        except Exception as e:
            logger.exception(f"Exception in generate_md5: {e}")
            return ""
