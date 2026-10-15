import logging
from typing import Dict, List, Optional
from .medicine_database import MedicineDatabase
from .fda_client import FDAClient

logger = logging.getLogger("drug_information_service.py")

class DrugInformation:
    def __init__(self):
        self.database = MedicineDatabase()
        self.fda_client = FDAClient()

    def get_drug_info(self, generic_name: str) -> Optional[Dict[str, str]]:
        info = self.database.get_medicine(generic_name)
        if info:
            return info
        info = self.fda_client.query(generic_name)
        if info:
            self.database.save_medicine(info)
        return info

    def get_multiple_drugs_info(self, drug_names: List[str]) -> Dict[int, Dict[str, str]]:
        data = {}
        for i, name in enumerate(drug_names):
            info = self.get_drug_info(name)
            if info:
                data[i] = info
        return data
