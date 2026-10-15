"""
Tests for the SQLite cache (models.database, MedicineDatabase) and the lookup
orchestration (DrugInformation).

Run against real SQLite files in ``tmp_path``. There is no ORM to mock, and
mocking the driver would leave the actual SQL untested, which is the part worth
testing.

DrugInformation implements a read-through cache: consult SQLite first, fall back
to the openFDA API, then persist the result. The tests below pin that ordering
down, since getting it wrong would either hammer the API or serve stale rows.
"""

import sqlite3
import threading

import pytest

from models import database
from utils.helper.drug_information import DrugInformation
from utils.helper.medicine_database import MedicineDatabase


@pytest.fixture
def db_path(tmp_path):
    """A throwaway database with the schema already created."""
    path = tmp_path / "test.sqlite3"
    database.init_db(path)
    return path


@pytest.fixture
def medicine_database(db_path):
    return MedicineDatabase(db_path)


IBUPROFEN = {
    "generic_name": "ibuprofen",
    "purpose": "pain reliever",
    "do_not_use": "if allergic to nsaids",
}


class TestSchema:
    def test_creates_the_medicines_table(self, db_path):
        with database.connect(db_path) as connection:
            tables = [
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
        assert "medicines" in tables

    def test_init_is_idempotent(self, db_path):
        database.init_db(db_path)
        database.init_db(db_path)  # must not raise

    def test_creates_the_parent_directory(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "db.sqlite3"
        database.init_db(nested)
        assert nested.exists()

    def test_generic_name_is_unique(self, db_path):
        with database.connect(db_path) as connection:
            connection.execute(database.INSERT_MEDICINE, ("ibuprofen", "a", "b"))

        with pytest.raises(sqlite3.IntegrityError):
            with database.connect(db_path) as connection:
                connection.execute(
                    "INSERT INTO medicines (generic_name) VALUES (?)", ("ibuprofen",)
                )

    def test_generic_name_is_required(self, db_path):
        with pytest.raises(sqlite3.IntegrityError):
            with database.connect(db_path) as connection:
                connection.execute(
                    "INSERT INTO medicines (generic_name) VALUES (?)", (None,)
                )


class TestMedicineDatabase:
    def test_missing_medicine_returns_none(self, medicine_database):
        assert medicine_database.get_medicine("ibuprofen") is None

    def test_save_then_retrieve(self, medicine_database):
        assert medicine_database.save_medicine(IBUPROFEN) is True
        assert medicine_database.get_medicine("ibuprofen") == IBUPROFEN

    def test_saving_twice_is_idempotent(self, medicine_database, db_path):
        medicine_database.save_medicine(IBUPROFEN)
        assert medicine_database.save_medicine(IBUPROFEN) is True

        with database.connect(db_path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) AS n FROM medicines WHERE generic_name = ?",
                ("ibuprofen",),
            ).fetchone()["n"]
        assert count == 1

    def test_resaving_does_not_overwrite(self, medicine_database):
        """INSERT OR IGNORE keeps the first value, matching previous behaviour."""
        medicine_database.save_medicine(IBUPROFEN)
        medicine_database.save_medicine(
            {"generic_name": "ibuprofen", "purpose": "changed", "do_not_use": "changed"}
        )
        assert medicine_database.get_medicine("ibuprofen")["purpose"] == "pain reliever"

    def test_lookup_is_exact_not_fuzzy(self, medicine_database):
        """Fuzzy matching happens upstream; the cache is keyed on exact names."""
        medicine_database.save_medicine(IBUPROFEN)
        assert medicine_database.get_medicine("ibuprofn") is None
        assert medicine_database.get_medicine("Ibuprofen") is None

    def test_several_medicines_coexist(self, medicine_database):
        medicine_database.save_medicine(IBUPROFEN)
        medicine_database.save_medicine(
            {"generic_name": "aspirin", "purpose": "pain reliever", "do_not_use": "ulcers"}
        )

        assert medicine_database.get_medicine("ibuprofen")["purpose"] == "pain reliever"
        assert medicine_database.get_medicine("aspirin")["do_not_use"] == "ulcers"

    def test_optional_fields_may_be_missing(self, medicine_database):
        assert medicine_database.save_medicine({"generic_name": "aspirin"}) is True
        assert medicine_database.get_medicine("aspirin") == {
            "generic_name": "aspirin",
            "purpose": None,
            "do_not_use": None,
        }

    def test_invalid_payload_returns_false(self, medicine_database):
        """A missing generic_name is a caller bug, reported rather than raised."""
        assert medicine_database.save_medicine({"unexpected_field": "value"}) is False

    def test_unreadable_database_returns_none(self, tmp_path):
        """A cache failure must degrade the response, not fail the request."""
        broken = MedicineDatabase(tmp_path / "never-created.sqlite3")
        assert broken.get_medicine("x") is None


class TestSqlInjection:
    """Values are bound as parameters, so hostile input stays inert."""

    @pytest.mark.parametrize(
        "hostile",
        [
            "'); DROP TABLE medicines;--",
            "' OR '1'='1",
            'ibuprofen"; DELETE FROM medicines WHERE "1"="1',
        ],
    )
    def test_hostile_lookup_does_not_execute(self, medicine_database, db_path, hostile):
        medicine_database.save_medicine(IBUPROFEN)

        assert medicine_database.get_medicine(hostile) is None

        with database.connect(db_path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) AS n FROM medicines"
            ).fetchone()["n"]
        assert count == 1, "the table was modified"

    def test_hostile_value_round_trips_verbatim(self, medicine_database):
        hostile = "'); DROP TABLE medicines;--"
        medicine_database.save_medicine(
            {"generic_name": hostile, "purpose": "p", "do_not_use": "d"}
        )
        assert medicine_database.get_medicine(hostile)["generic_name"] == hostile


class TestConcurrency:
    """
    FastAPI runs synchronous endpoints in a thread pool, so two requests for the
    same drug can reach the cache at once.
    """

    def test_concurrent_saves_of_the_same_drug_all_succeed(self, db_path):
        """
        The old check-then-insert had a race: both threads saw the row missing
        and the loser hit the UNIQUE constraint. INSERT OR IGNORE performs the
        check inside SQLite as one atomic statement.
        """
        results = []
        barrier = threading.Barrier(8)

        def save():
            db = MedicineDatabase(db_path)
            barrier.wait()  # maximise the overlap
            results.append(db.save_medicine(IBUPROFEN))

        threads = [threading.Thread(target=save) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert all(results), "a concurrent save failed"

        with database.connect(db_path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) AS n FROM medicines"
            ).fetchone()["n"]
        assert count == 1

    def test_reads_and_writes_can_interleave(self, db_path):
        errors = []

        def worker(index):
            try:
                db = MedicineDatabase(db_path)
                db.save_medicine({"generic_name": f"drug{index}", "purpose": "p"})
                db.get_medicine(f"drug{index}")
            except Exception as error:  # pragma: no cover - failure path
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []


class FakeFDAClient:
    """Stands in for the network client and records what was asked for."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.queries = []

    def query(self, generic_name):
        self.queries.append(generic_name)
        return self.responses.get(generic_name)


@pytest.fixture
def drug_information(medicine_database):
    service = DrugInformation()
    service.database = medicine_database
    service.fda_client = FakeFDAClient({"ibuprofen": IBUPROFEN})
    return service


class TestDrugInformationCaching:
    def test_fetches_from_api_when_not_cached(self, drug_information):
        assert drug_information.get_drug_info("ibuprofen") == IBUPROFEN
        assert drug_information.fda_client.queries == ["ibuprofen"]

    def test_api_result_is_persisted(self, drug_information, medicine_database):
        drug_information.get_drug_info("ibuprofen")
        assert medicine_database.get_medicine("ibuprofen") == IBUPROFEN

    def test_second_lookup_skips_the_api(self, drug_information):
        drug_information.get_drug_info("ibuprofen")
        drug_information.get_drug_info("ibuprofen")

        assert drug_information.fda_client.queries == ["ibuprofen"], (
            "cached medicine should not trigger a second API call"
        )

    def test_cached_row_is_preferred_over_api(self, drug_information, medicine_database):
        medicine_database.save_medicine(
            {"generic_name": "ibuprofen", "purpose": "from cache", "do_not_use": "cached"}
        )

        result = drug_information.get_drug_info("ibuprofen")

        assert result["purpose"] == "from cache"
        assert drug_information.fda_client.queries == []

    def test_unknown_drug_returns_none(self, drug_information):
        assert drug_information.get_drug_info("unobtainium") is None

    def test_failed_api_lookup_is_not_cached(self, drug_information, medicine_database):
        drug_information.get_drug_info("unobtainium")
        assert medicine_database.get_medicine("unobtainium") is None


class TestMultipleDrugs:
    def test_returns_index_keyed_mapping(self, drug_information):
        drug_information.fda_client.responses["aspirin"] = {
            "generic_name": "aspirin",
            "purpose": "pain reliever",
            "do_not_use": "ulcers",
        }

        result = drug_information.get_multiple_drugs_info(["ibuprofen", "aspirin"])

        assert list(result.keys()) == [0, 1]
        assert result[0]["generic_name"] == "ibuprofen"
        assert result[1]["generic_name"] == "aspirin"

    def test_unresolvable_names_are_omitted(self, drug_information):
        """
        Note the index gap: keys come from enumerate() over the input, so a
        missing drug leaves a hole rather than renumbering.
        """
        result = drug_information.get_multiple_drugs_info(["unobtainium", "ibuprofen"])

        assert list(result.keys()) == [1]
        assert result[1]["generic_name"] == "ibuprofen"

    def test_empty_input(self, drug_information):
        assert drug_information.get_multiple_drugs_info([]) == {}

    def test_all_unknown_returns_empty(self, drug_information):
        assert drug_information.get_multiple_drugs_info(["nope", "also-nope"]) == {}
