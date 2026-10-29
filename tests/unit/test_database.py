import pytest

from app.core.database import MIGRATIONS, PG_MIGRATIONS, Row, to_pg_sql


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT * FROM t WHERE a = ? AND b = ?", "SELECT * FROM t WHERE a = %s AND b = %s"),
        ("WHERE date BETWEEN :start AND :end", "WHERE date BETWEEN %(start)s AND %(end)s"),
        ("WHERE x = 'is it ?' AND t = '10:30' AND y = ?", "WHERE x = 'is it ?' AND t = '10:30' AND y = %s"),
        ("WHERE name LIKE '50%' AND id = ?", "WHERE name LIKE '50%%' AND id = %s"),
        ("WHERE a = 'it''s :not' AND b = :yes", "WHERE a = 'it''s :not' AND b = %(yes)s"),
    ],
)
def test_to_pg_sql(sql: str, expected: str) -> None:
    assert to_pg_sql(sql) == expected


def test_row_supports_names_and_positions() -> None:
    row = Row(a=1, b="x")
    assert row["b"] == "x" and row[0] == 1 and dict(row) == {"a": 1, "b": "x"}


def test_migrations_stay_in_lockstep() -> None:
    assert len(MIGRATIONS) == len(PG_MIGRATIONS)
