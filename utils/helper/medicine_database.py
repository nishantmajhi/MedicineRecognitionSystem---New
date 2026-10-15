import logging
from pathlib import Path
from typing import Dict, Optional

from models import database

logger = logging.getLogger("medicine_database.py")


class MedicineDatabase:
    """
    Read-through cache of medicine information, stored in SQLite.

    A thin wrapper over ``models.database``: its job is to turn database
    failures into ``None``/``False`` return values, so a cache problem degrades
    the response instead of failing the request.
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Args:
            db_path: Override the database location. Mainly for tests, which
                point it at a temporary file.
        """
        self.db_path = Path(db_path) if db_path else database.DEFAULT_DB_PATH

    def get_medicine(self, generic_name: str) -> Optional[Dict[str, str]]:
        """The cached entry for a name, or None if absent or unreadable."""
        try:
            return database.get_medicine(generic_name, self.db_path)
        except Exception as e:
            logger.exception(f"Database error: {e}")
            return None

    def save_medicine(self, drug_info: Dict[str, str]) -> bool:
        """
        Cache one medicine.

        Returns True if the row is present afterwards, which includes the case
        where it was already there. Returns False only on a real failure.
        """
        try:
            database.save_medicine(drug_info, self.db_path)
            return True
        except Exception as e:
            logger.exception(f"Error saving medicine: {e}")
            return False
