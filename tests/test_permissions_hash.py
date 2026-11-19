from classmate.services.permissions import hash_code, make_code


def test_make_code_length():
    assert len(make_code(8)) == 8


def test_hash_stable():
    assert hash_code("ABC123") == hash_code("abc123")
    assert hash_code("ABC123") != hash_code("ABC124")
