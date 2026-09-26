"""PostgreSQL implementation of the existing Store seam.

It deliberately retains the Store API, allowing the RAG and LangGraph workflow
to move off SurrealDB without changing their business rules.  A ContextVar
binds every request to the authenticated user before any read or write.
"""

import json
from contextlib import contextmanager
from contextvars import ContextVar, Token
from hashlib import md5

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .storage import StorageError, stable_key

_user_id: ContextVar[str | None] = ContextVar("final_review_user_id", default=None)


class PostgresStore:
    TABLES = {
        "course": "courses",
        "document": "documents",
        "chunk": "document_chunks",
        "conversation": "conversations",
        "message": "messages",
        "review_session": "review_sessions",
        "knowledge_point": "knowledge_points",
        "checkpoint": "checkpoints",
        "pending_write": "pending_writes",
        "attempt": "attempts",
        "learning_event": "learning_events",
        "fast_quiz_session": "fast_quiz_sessions",
        "exam": "exams",
        "learning_asset": "learning_assets",
        "asset_revision": "asset_revisions",
        "quiz_revision_payload": "quiz_revision_payloads",
        "question_revision": "question_revisions",
        "material_version": "material_versions",
        "source_reference": "source_references",
        "source_snapshot": "source_reference_snapshots",
        "confirmation": "confirmation_requests",
        "audit_event": "audit_events",
    }

    def __init__(self, database_url: str):
        self.connection = psycopg.connect(database_url, row_factory=dict_row)
        self._transaction_depth = 0

    @contextmanager
    def transaction(self):
        """Group one destructive domain operation into a single transaction."""
        self._transaction_depth += 1
        try:
            with self.connection.transaction():
                yield
        finally:
            self._transaction_depth -= 1

    def _commit(self):
        if not self._transaction_depth:
            self.connection.commit()

    def _rollback(self):
        if not self._transaction_depth:
            self.connection.rollback()

    def close(self):
        self.connection.close()

    def setup(self):
        """Fail fast when the self-managed PostgreSQL migration is absent."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                if cursor.fetchone() is None:
                    raise StorageError(
                        "缺少 pgvector；请先执行 db/migrations/001_database_auth.sql"
                    )
                cursor.execute("SELECT to_regclass('public.document_chunks') AS table_name")
                if cursor.fetchone()["table_name"] is None:
                    raise StorageError("缺少第二阶段数据表；请先执行数据库迁移")
                cursor.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = %s",
                    ("003_m0_database_hardening.sql",),
                )
                if cursor.fetchone() is None:
                    raise StorageError(
                        "数据库迁移未完成；请先运行 python -m final_review.migrations"
                    )
        except psycopg.Error as exc:
            raise StorageError("PostgreSQL 初始化检查失败") from exc

    def bind_user(self, user_id: str) -> Token:
        return _user_id.set(user_id)

    def reset_user(self, token: Token):
        _user_id.reset(token)

    def _user(self) -> str:
        user = _user_id.get()
        if not user:
            raise StorageError("数据库操作缺少已认证用户上下文")
        return user

    def session_key(self, course_id: str, session_id: str) -> str:
        """Prevent equal client session names from sharing LangGraph checkpoints."""
        return stable_key(self._user(), course_id, session_id)

    def _table(self, table: str) -> str:
        try:
            return self.TABLES[table]
        except KeyError as exc:
            raise ValueError("未知数据表") from exc

    def _columns(self, table: str, data: dict) -> dict:
        names = [
            "course_id",
            "conversation_id",
            "document_id",
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "created_at",
        ]
        if table == "source_reference":
            names.remove("document_id")
        if table == "course":
            names.extend(
                ["status", "archived_at", "deleted_at", "purge_after", "deletion_confirmed_at"]
            )
        if table == "attempt":
            names.extend(["legacy_session_id", "quiz_revision_id"])
        if table == "learning_asset":
            names.extend(["asset_id", "current_revision_id"])
        if table == "asset_revision":
            names.extend(
                ["revision_id", "asset_id", "revision_no", "state", "confirmed_at", "content_hash"]
            )
        if table == "material_version":
            names.append("material_version_id")
        if table == "source_reference":
            names.extend(["asset_revision_id", "material_version_id", "locator_id"])
        if table == "fast_quiz_session":
            names.append("session_id")
        if table == "quiz_revision_payload":
            names.extend(["quiz_revision_id", "state"])
        return {key: data[key] for key in names if key in data}

    def put(self, table: str, key: str, data: dict):
        if table == "asset_revision":
            data = {**data, "content_hash": self._revision_hash(data)}
        if table == "source_reference" and "asset_revision_id" not in data:
            data = {**data, "asset_revision_id": data.get("revision_id"), "locator_id": "document"}
        physical = self._table(table)
        user_id = self._user()
        columns = self._columns(table, data)
        names = ["record_key", "user_id", *columns, "data"]
        values = [key, user_id, *columns.values(), Jsonb(data)]
        update = [
            f"{name} = EXCLUDED.{name}" for name in names if name not in {"record_key", "user_id"}
        ]
        placeholders = ", ".join(["%s"] * len(names))
        sql = (
            f"INSERT INTO {physical} ({', '.join(names)}) VALUES ({placeholders}) "
            f"ON CONFLICT (record_key) DO UPDATE SET {', '.join(update)}"
        )
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql, values)
                if table == "material_version":
                    cursor.execute(
                        "INSERT INTO material_locators("
                        "user_id,course_id,material_version_id,locator_id,locator_kind,ordinal"
                        ") VALUES (%s,%s,%s,'document','document',0) ON CONFLICT DO NOTHING",
                        (user_id, data["course_id"], data["material_version_id"]),
                    )
            self._commit()
        except psycopg.Error as exc:
            self._rollback()
            raise StorageError("PostgreSQL 写入失败") from exc

    @staticmethod
    def _revision_hash(data: dict) -> str:
        source_ids = data.get("source_document_ids", [])
        return md5(
            (
                data.get("title", "")
                + "\n"
                + data.get("markdown", "")
                + "\n"
                + json.dumps(source_ids, ensure_ascii=False, separators=(",", ":"))
            ).encode()
        ).hexdigest()

    def get(self, table: str, key: str) -> dict | None:
        physical = self._table(table)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT data FROM {physical} WHERE record_key = %s AND user_id = %s",
                    (key, self._user()),
                )
                row = cursor.fetchone()
            return row["data"] if row else None
        except psycopg.Error as exc:
            raise StorageError("PostgreSQL 查询失败") from exc

    def get_for_update(self, table: str, key: str) -> dict | None:
        """Read an owned record while holding a row lock inside ``transaction``."""
        if not self._transaction_depth:
            raise StorageError("行锁读取必须在事务中执行")
        physical = self._table(table)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT data FROM {physical} WHERE record_key = %s "
                    "AND user_id = %s FOR UPDATE",
                    (key, self._user()),
                )
                row = cursor.fetchone()
            return row["data"] if row else None
        except psycopg.Error as exc:
            raise StorageError("PostgreSQL 行锁查询失败") from exc

    def scan(self, table: str, filters: dict) -> list[dict]:
        physical = self._table(table)
        allowed = {
            "user_id",
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "course_id",
            "conversation_id",
            "document_id",
        }
        if not filters.keys() <= allowed:
            raise ValueError("不支持的查询字段")
        clauses, values = ["user_id = %s"], [self._user()]
        for key, value in filters.items():
            if key == "user_id":
                if value != self._user():
                    return []
                continue
            clauses.append(f"{key} = %s")
            values.append(value)
        order = " ORDER BY created_at ASC" if table == "message" else ""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT data FROM {physical} WHERE {' AND '.join(clauses)}{order}", values
                )
                return [row["data"] for row in cursor.fetchall()]
        except psycopg.Error as exc:
            raise StorageError("PostgreSQL 查询失败") from exc

    def delete(self, table: str, key: str):
        physical = self._table(table)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"DELETE FROM {physical} WHERE record_key = %s AND user_id = %s",
                    (key, self._user()),
                )
            self._commit()
        except psycopg.Error as exc:
            self._rollback()
            raise StorageError("PostgreSQL 删除失败") from exc

    def delete_document(self, key: str):
        user_id = self._user()
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM document_chunks WHERE document_id = %s AND user_id = %s",
                    (key, user_id),
                )
                cursor.execute(
                    "DELETE FROM documents WHERE record_key = %s AND user_id = %s", (key, user_id)
                )
            self._commit()
        except psycopg.Error as exc:
            self._rollback()
            raise StorageError("PostgreSQL 删除资料失败") from exc

    def deindex_document(self, key: str):
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM document_chunks WHERE document_id = %s AND user_id = %s",
                    (key, self._user()),
                )
            self._commit()
        except psycopg.Error as exc:
            self._rollback()
            raise StorageError("PostgreSQL 移除资料检索索引失败") from exc

    def ingest(self, document: dict, chunks: list[dict]):
        # One transaction ensures failed parsing never exposes a half-indexed document.
        user_id = self._user()
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO documents(record_key,user_id,course_id,document_id,data) "
                    "VALUES (%s,%s,%s,%s,%s) ON CONFLICT(record_key) DO UPDATE SET "
                    "course_id=EXCLUDED.course_id,data=EXCLUDED.data",
                    (
                        document["document_id"],
                        user_id,
                        document["course_id"],
                        document["document_id"],
                        Jsonb(document),
                    ),
                )
                cursor.execute(
                    "DELETE FROM document_chunks WHERE document_id = %s AND user_id = %s",
                    (document["document_id"], user_id),
                )
                for chunk in chunks:
                    cursor.execute(
                        "INSERT INTO document_chunks("
                        "record_key,user_id,course_id,document_id,data,content,embedding"
                        ") VALUES (%s,%s,%s,%s,%s,%s,%s::vector)",
                        (
                            chunk["chunk_id"],
                            user_id,
                            chunk["course_id"],
                            chunk["document_id"],
                            Jsonb(chunk),
                            chunk["content"],
                            self._vector(chunk["embedding"]),
                        ),
                    )
            self._commit()
        except psycopg.Error as exc:
            self._rollback()
            raise StorageError("PostgreSQL 资料入库失败") from exc

    @staticmethod
    def _vector(vector: list[float]) -> str:
        return "[" + ",".join(str(value) for value in vector) + "]"

    def search(self, vector: list[float], course: str, chapter: str, limit: int) -> list[dict]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """SELECT data, 1 - (embedding <=> %s::vector) AS similarity
                    FROM document_chunks
                    WHERE user_id = %s AND course_id = %s
                      AND (%s = '' OR data->>'chapter' = %s)
                    ORDER BY embedding <=> %s::vector LIMIT %s""",
                    (
                        self._vector(vector),
                        self._user(),
                        course,
                        chapter,
                        chapter,
                        self._vector(vector),
                        limit,
                    ),
                )
                # Chunks retain their embedding in JSON for the stored source record as
                # well as in pgvector.  It is not part of the retrieval contract:
                # passing it on causes Evidence's strict schema validation to fail.
                return [
                    {
                        **{key: value for key, value in row["data"].items() if key != "embedding"},
                        "similarity": float(row["similarity"]),
                    }
                    for row in cursor.fetchall()
                ]
        except psycopg.Error as exc:
            raise StorageError("PostgreSQL 向量检索失败") from exc
