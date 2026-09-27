"""project deletion: the app role's DELETE privilege on projects

Migration 0003 granted ``aiper_app`` exactly the table privileges the routes
of its day used, so the coarse GRANT layer under the row policies refuses
anything newer — loudly, which is the designed failure mode for a route
added without thinking about its authorisation. The DELETE *policy* (owner
only) has existed since 0003; this grants the matching privilege now that
``DELETE /api/v1/projects/{id}`` exists.

Folders and documents cascade via their foreign keys, which Postgres runs
internally as the table owner — no further grants are needed for the
cascade itself.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "aiper_app"


def upgrade() -> None:
    op.execute(f"GRANT DELETE ON projects TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE DELETE ON projects FROM {APP_ROLE}")
