"""SQLGlot safety gate for compiler output."""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from ..schemas.snapshot import Snapshot


class QuerySafetyError(ValueError):
    pass


class QueryAstValidator:
    def validate(
        self, sql: str, snapshot: Snapshot, *, dialect: str | None = None
    ) -> None:
        try:
            statements = sqlglot.parse(sql, read=self._sqlglot_dialect(dialect))
        except sqlglot.errors.ParseError as exc:
            raise QuerySafetyError("SQL AST parse failed") from exc
        if len(statements) != 1:
            raise QuerySafetyError("Only one SQL statement is allowed")
        statement = statements[0]
        if not isinstance(statement, (exp.Select, exp.With)):
            raise QuerySafetyError("Only SELECT statements are allowed")
        if any(
            statement.find(node)
            for node in (
                exp.Insert,
                exp.Update,
                exp.Delete,
                exp.Create,
                exp.Drop,
                exp.Command,
            )
        ):
            raise QuerySafetyError("Write or command expressions are not allowed")
        if any(self._selects_star(select) for select in statement.find_all(exp.Select)):
            raise QuerySafetyError("SELECT * is not allowed")
        forbidden_functions = {
            "bulk",
            "dblink",
            "load_extension",
            "load_file",
            "openrowset",
            "pg_read_file",
            "readfile",
            "xp_cmdshell",
        }
        if any(
            isinstance(node, exp.Func)
            and (
                node.sql_name().lower() in forbidden_functions
                or str(getattr(node, "name", "")).lower() in forbidden_functions
            )
            for node in statement.walk()
        ):
            raise QuerySafetyError(
                "File, remote access, or shell functions are not allowed"
            )
        allowed_view = snapshot.runtime_config.get("view", "").lower()
        allowed_names = {allowed_view, allowed_view.split(".")[-1]}
        cte_names = {
            str(cte.alias).lower()
            for cte in statement.find_all(exp.CTE)
            if str(cte.alias or "").strip()
        }
        for table in statement.find_all(exp.Table):
            if table.name.lower() in cte_names:
                continue
            actual = ".".join(
                part for part in (table.catalog, table.db, table.name) if part
            ).lower()
            if actual not in allowed_names and table.name.lower() not in allowed_names:
                raise QuerySafetyError("SQL references an unbound view")

    @staticmethod
    def _sqlglot_dialect(dialect: str | None) -> str | None:
        if dialect is None:
            return None
        normalized = dialect.lower()
        return {"postgresql": "postgres"}.get(normalized, normalized)

    @staticmethod
    def _selects_star(select: exp.Select) -> bool:
        for expression in select.expressions:
            target = expression.this if isinstance(expression, exp.Alias) else expression
            if isinstance(target, exp.Star):
                return True
            if isinstance(target, exp.Column) and isinstance(target.this, exp.Star):
                return True
        return False


__all__ = ["QueryAstValidator", "QuerySafetyError"]
