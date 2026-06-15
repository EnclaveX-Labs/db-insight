from __future__ import annotations

import re

import sqlglot
from sqlglot import exp


BLOCKED_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|call|execute)\b",
    re.IGNORECASE,
)


class UnsafeSqlError(ValueError):
    pass


def validate_select_only(sql: str, default_limit: int = 100) -> str:
    if not sql.strip():
        raise UnsafeSqlError("SQL is empty.")

    statements = sqlglot.parse(sql, read="postgres")
    if len(statements) != 1:
        raise UnsafeSqlError("Only one SQL statement is allowed.")

    statement = statements[0]
    if BLOCKED_KEYWORDS.search(sql):
        raise UnsafeSqlError("Only read-only SELECT queries are allowed.")

    if not isinstance(statement, (exp.Select, exp.Union, exp.With)):
        raise UnsafeSqlError("Query must be a SELECT or WITH SELECT statement.")

    for node in statement.walk():
        if isinstance(
            node,
            (
                exp.Insert,
                exp.Update,
                exp.Delete,
                exp.Drop,
                exp.Create,
                exp.Alter,
            ),
        ):
            raise UnsafeSqlError("Mutation and DDL statements are blocked.")

    if not statement.args.get("limit"):
        statement = statement.limit(default_limit)

    return statement.sql(dialect="postgres")


PII_PATTERNS = [
    re.compile(r"email", re.IGNORECASE),
    re.compile(r"phone", re.IGNORECASE),
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
]


def mask_pii_rows(rows: list[dict]) -> list[dict]:
    masked: list[dict] = []
    for row in rows:
        next_row = {}
        for key, value in row.items():
            if any(pattern.search(key) for pattern in PII_PATTERNS):
                next_row[key] = "[masked]"
            else:
                next_row[key] = value
        masked.append(next_row)
    return masked
