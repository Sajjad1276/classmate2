from classmate.admin_bot.access import ROLE_PERMISSIONS
from classmate.admin_bot.router import router as management_router
from classmate.db.base import Base
from classmate.db.models import AdminAccount, AdminAuditEvent, AdminDashboardPreference, ClassGroup


def test_live_class_schema_uses_direct_school_ownership() -> None:
    assert "school_id" in ClassGroup.__table__.columns
    assert "academic_year_id" not in ClassGroup.__table__.columns
    assert "academic_years" not in Base.metadata.tables


def test_admin_control_center_tables_are_in_shared_metadata() -> None:
    assert "admin_accounts" in Base.metadata.tables
    assert "admin_audit_events" in Base.metadata.tables
    assert "admin_dashboard_preferences" in Base.metadata.tables
    assert AdminAccount.__tablename__ == "admin_accounts"
    assert AdminAuditEvent.__tablename__ == "admin_audit_events"
    assert AdminDashboardPreference.__tablename__ == "admin_dashboard_preferences"


def test_management_bot_roles_are_scoped_and_do_not_default_to_owner() -> None:
    assert "*" in ROLE_PERMISSIONS["owner"]
    assert "*" not in ROLE_PERMISSIONS["operator"]
    assert "classes:write" in ROLE_PERMISSIONS["operator"]
    assert "approvals:write" not in ROLE_PERMISSIONS["viewer"]
    assert "approvals:write" in ROLE_PERMISSIONS["reviewer"]


def test_management_bot_router_imports_and_registers_handlers() -> None:
    assert management_router.name == "management-control-center"
    assert management_router.message.handlers
    assert management_router.callback_query.handlers
