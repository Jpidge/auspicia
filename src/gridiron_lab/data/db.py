"""The query handle feature code is allowed to use.

``db.as_of(table, ts)`` returns, per entity key, the latest row whose
``known_as_of`` is strictly earlier than ``ts``.
"""

from gridiron_lab.data.store import Database, open_db

__all__ = ["Database", "open_db"]
