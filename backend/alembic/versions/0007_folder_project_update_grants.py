"""rename/move/delete: the app role's remaining tree-mutation privileges

The same lesson as 0006, one unit sooner each time: migration 0003 granted
``aiper_app`` exactly the table privileges the routes of its day used, and the
row *policies* it wrote were already broad enough for mutations no route
exercised yet — ``projects UPDATE`` and ``folders UPDATE``/``DELETE`` have all
been sitting there, correctly scoped to owner/editor, since 0003. Only the
coarse GRANT layer under them was narrower than the policies, because nothing
called them. This grants the privileges now that project rename, folder
rename, folder move and folder delete exist as routes.

Document UPDATE/DELETE were already granted in full by 0003 (0006 added only
the missing DELETE on *projects*), so document move needs no grant here — it
is a new route over an already-granted table.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "aiper_app"


def upgrade() -> None:
    op.execute(f"GRANT UPDATE ON projects TO {APP_ROLE}")
    op.execute(f"GRANT UPDATE, DELETE ON folders TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE UPDATE, DELETE ON folders FROM {APP_ROLE}")
    op.execute(f"REVOKE UPDATE ON projects FROM {APP_ROLE}")
