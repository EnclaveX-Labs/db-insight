import pytest

from db_insight.safety import UnsafeSqlError, mask_pii_rows, validate_select_only


def test_adds_limit_to_select() -> None:
    assert validate_select_only("select * from users", 25).endswith("LIMIT 25")


def test_blocks_mutation() -> None:
    with pytest.raises(UnsafeSqlError):
        validate_select_only("delete from users")


def test_blocks_empty_sql() -> None:
    with pytest.raises(UnsafeSqlError):
        validate_select_only("")


def test_masks_pii_columns() -> None:
    rows = mask_pii_rows([{"email": "a@example.com", "name": "A"}])
    assert rows == [{"email": "[masked]", "name": "A"}]
