from classmate.core.enums import RecordStatus, Role


def test_role_values():
    assert Role.STUDENT == "student"
    assert Role.CLASS_MANAGER == "class_manager"
    assert Role.TRUSTED_APPROVER == "trusted_approver"


def test_record_status_values():
    assert RecordStatus.PENDING == "PENDING"
    assert RecordStatus.APPROVED == "APPROVED"
    assert RecordStatus.REJECTED == "REJECTED"
