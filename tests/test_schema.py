import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        database = Path(self.temp_dir.name) / "schema.db"
        self.url = f"sqlite:///{database}"
        self.config = Config("alembic.ini")
        self.config.set_main_option("sqlalchemy.url", self.url)

    def test_upgrade_builds_constrained_workflow_schema(self):
        command.upgrade(self.config, "head")
        inspector = inspect(create_engine(self.url))
        self.assertIn("rejection", {c["name"] for c in inspector.get_columns("command_receipts")})
        self.assertIn("actor_id", {c["name"] for c in inspector.get_columns("workflow_events")})
        self.assertEqual(set(inspector.get_table_names()), {"alembic_version", "workflows", "workflow_events", "command_outbox", "command_receipts"})
        self.assertEqual(
            {column["name"] for column in inspector.get_columns("workflows")},
            {"id", "name", "owner_id", "status", "attempts", "max_attempts", "version",
             "created_at", "updated_at"},
        )
        foreign_keys = inspector.get_foreign_keys("workflow_events")
        self.assertEqual(foreign_keys[0]["referred_table"], "workflows")
        self.assertEqual(foreign_keys[0]["options"].get("ondelete"), "CASCADE")
        unique_names = {item["name"] for item in inspector.get_unique_constraints("workflow_events")}
        self.assertIn("uq_workflow_events_position", unique_names)
        checks = {item["name"] for table in ("workflows", "workflow_events")
                  for item in inspector.get_check_constraints(table)}
        self.assertIn("ck_workflows_status", checks)
        self.assertIn("ck_workflows_version_nonnegative", checks)
        self.assertIn("ck_workflow_events_status", checks)
        self.assertIn("ck_workflows_owner_id", checks)
        self.assertIn("ck_workflow_events_actor_id", checks)

    def test_migration_is_reversible(self):
        command.upgrade(self.config, "head")
        command.downgrade(self.config, "base")
        self.assertEqual(inspect(create_engine(self.url)).get_table_names(), ["alembic_version"])

