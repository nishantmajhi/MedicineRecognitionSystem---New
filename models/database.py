"""
The database layer: plain SQL over Python's built-in ``sqlite3``.

The application stores one thing -- a cache of medicine information keyed by
generic name -- and needs exactly three statements against it: create the table,
look a row up by name, insert a row. That did not justify an ORM, so there
isn't one. The SQL is written out where you can read it.

Concurrency
-----------
Each call opens its own connection rather than sharing one. FastAPI runs
synchronous endpoints in a thread pool, and a ``sqlite3.Connection`` is not
safe to use from a thread other than the one that created it. Opening per call
sidesteps that entirely; SQLite handles the cross-process locking itself, and
for a cache read this costs microseconds.

Safety
------
Every value is passed as a bound parameter (``?``), never formatted into the SQL
string. A drug name such as ``'); DROP TABLE medicines;--`` is therefore stored
and matched as literal text.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("database.py")

DEFAULT_DB_PATH = Path("MRS/database/db.sqlite3")

CREATE_MEDICINES_TABLE = """
CREATE TABLE IF NOT EXISTS medicines (
    id           INTEGER PRIMARY KEY,
    generic_name TEXT UNIQUE NOT NULL,
    purpose      TEXT,
    do_not_use   TEXT
)
"""

SELECT_MEDICINE = """
SELECT generic_name, purpose, do_not_use
FROM medicines
WHERE generic_name = ?
"""

# INSERT OR IGNORE does the "only if absent" check inside SQLite, as one atomic
# statement. Reading first and then inserting leaves a window where two threads
# both see the row missing and the second violates the UNIQUE constraint.
INSERT_MEDICINE = """
INSERT OR IGNORE INTO medicines (generic_name, purpose, do_not_use)
VALUES (?, ?, ?)
"""


CREATE_SYNONYMS_TABLE = """
CREATE TABLE IF NOT EXISTS name_synonyms (
    source_name   TEXT PRIMARY KEY,
    resolved_name TEXT NOT NULL,
    rxcui         TEXT,
    source        TEXT NOT NULL
)
"""

SELECT_SYNONYM = """
SELECT source_name, resolved_name, rxcui, source
FROM name_synonyms
WHERE source_name = ?
"""

INSERT_SYNONYM = """
INSERT OR REPLACE INTO name_synonyms (source_name, resolved_name, rxcui, source)
VALUES (?, ?, ?, ?)
"""


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """
    Open a connection to the medicine database, creating its directory if needed.

    Rows come back as ``sqlite3.Row``, which supports access by column name.
    """
    db_path = Path(db_path)
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(db_path, timeout=10.0)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Create the tables if they do not already exist."""
    with connect(db_path) as connection:
        connection.execute(CREATE_MEDICINES_TABLE)
        connection.execute(CREATE_SYNONYMS_TABLE)
    logger.info("Database ready")


def get_medicine(
    generic_name: str, db_path: Path = DEFAULT_DB_PATH
) -> Optional[Dict[str, str]]:
    """
    Read one cached medicine by exact generic name.

    Returns:
        A dict with ``generic_name``, ``purpose`` and ``do_not_use``, or None if
        the name is not cached. Matching is exact; fuzzy matching happens
        upstream in ``DrugMatcher``.
    """
    with connect(db_path) as connection:
        row = connection.execute(SELECT_MEDICINE, (generic_name,)).fetchone()
        return dict(row) if row else None


def save_medicine(drug_info: Dict[str, str], db_path: Path = DEFAULT_DB_PATH) -> None:
    """
    Cache one medicine. Re-saving an already cached name is a no-op.

    Raises:
        KeyError: If ``generic_name`` is missing.
        sqlite3.Error: On any database failure.
    """
    with connect(db_path) as connection:
        connection.execute(
            INSERT_MEDICINE,
            (
                drug_info["generic_name"],
                drug_info.get("purpose"),
                drug_info.get("do_not_use"),
            ),
        )


def get_synonym(
    source_name: str, db_path: Path = DEFAULT_DB_PATH
) -> Optional[Dict[str, str]]:
    """
    Read a cached name resolution.

    Returns:
        A dict with ``source_name``, ``resolved_name``, ``rxcui`` and
        ``source``, or None if the name has not been resolved before.
    """
    with connect(db_path) as connection:
        row = connection.execute(SELECT_SYNONYM, (source_name,)).fetchone()
        return dict(row) if row else None


def save_synonym(
    source_name: str,
    resolved_name: str,
    rxcui: Optional[str],
    source: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """
    Cache one name resolution.

    Uses INSERT OR REPLACE rather than OR IGNORE: unlike the medicines cache, a
    later resolution is a better one. A name first resolved from the offline
    override table should be updated if RxNorm is reachable next time.
    """
    with connect(db_path) as connection:
        connection.execute(
            INSERT_SYNONYM, (source_name, resolved_name, rxcui, source)
        )


# Create the tables when the module is first imported, as before.
init_db()
