# -*- coding: utf-8 -*-
"""Local persistence for ShopMind feedback, optional accounts and scene governance."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_PASSWORD_ITERATIONS = 310_000
_TOKEN_TTL = timedelta(days=30)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_CN_ID = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_ADDRESS = re.compile(r"\d{1,4}\s*(?:号|室|栋|单元)|(?:省|市|区|县|路|街).{0,8}\d{1,4}\s*(?:号|室|栋|单元)")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _safe_candidate_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("模板候选文本必须是字符串")
    value = " ".join(value.strip().split())
    if not value or len(value) > 100:
        raise ValueError("候选模块/项目名称不能为空，且不能超过 100 字")
    if any(pattern.search(value) for pattern in (_EMAIL, _PHONE, _CN_ID, _ADDRESS)):
        raise ValueError("候选内容疑似包含个人联系方式或地址，请移除后再提交")
    return value


class ShopMindMvpStore:
    """SQLite-backed local MVP records. Passwords and bearer tokens are never stored raw."""

    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self._path = data_dir / "shopmind_mvp.sqlite3"
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_consents (
                    user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                    enabled INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS inventory (
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    item TEXT NOT NULL,
                    quantity TEXT NOT NULL DEFAULT '',
                    condition TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, item)
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    session_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    value TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, kind)
                );
                CREATE TABLE IF NOT EXISTS template_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    sections_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    evaluation_passed INTEGER,
                    evaluation_json TEXT NOT NULL DEFAULT '{}',
                    evaluated_at TEXT NOT NULL DEFAULT '',
                    reviewer TEXT NOT NULL DEFAULT '',
                    review_note TEXT NOT NULL DEFAULT '',
                    published_scene_id TEXT NOT NULL DEFAULT '',
                    published_version INTEGER
                );
                CREATE TABLE IF NOT EXISTS template_actions (
                    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scene_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    version INTEGER,
                    created_at TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(template_candidates)")}
            for name, definition in (
                ("evaluation_passed", "INTEGER"),
                ("evaluation_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("evaluated_at", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE template_candidates ADD COLUMN {name} {definition}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _password_digest(password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PASSWORD_ITERATIONS)

    def register(self, username: str, password: str) -> tuple[str, str]:
        username = username.strip()
        if not re.fullmatch(r"[\w.-]{3,48}", username, flags=re.UNICODE):
            raise ValueError("账号需为 3–48 位字母、数字、下划线、点或短横线")
        if len(password) < 10 or len(password) > 256:
            raise ValueError("密码长度需为 10–256 位")
        salt = secrets.token_bytes(16)
        user_id = f"user-{uuid.uuid4().hex}"
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO users(user_id,username,salt,password_hash,created_at) VALUES(?,?,?,?,?)",
                    (user_id, username, salt, self._password_digest(password, salt), _iso(_now())),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("该账号已存在") from exc
        return user_id, self._new_token(user_id)

    def login(self, username: str, password: str) -> tuple[str, str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id,salt,password_hash FROM users WHERE username=? COLLATE NOCASE",
                (username.strip(),),
            ).fetchone()
        if row is None or not hmac.compare_digest(
            bytes(row["password_hash"]), self._password_digest(password, bytes(row["salt"])),
        ):
            raise ValueError("账号或密码错误")
        return row["user_id"], self._new_token(row["user_id"])

    def _new_token(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO auth_tokens(token_hash,user_id,expires_at) VALUES(?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), user_id, _iso(_now() + _TOKEN_TTL)),
            )
        return token

    def authenticate(self, token: str) -> str | None:
        if not token or len(token) > 256:
            return None
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id,expires_at FROM auth_tokens WHERE token_hash=?", (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if datetime.fromisoformat(row["expires_at"]) <= _now():
                conn.execute("DELETE FROM auth_tokens WHERE token_hash=?", (token_hash,))
                return None
            return str(row["user_id"])

    def revoke_token(self, token: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM auth_tokens WHERE token_hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            )

    def set_memory_consent(self, user_id: str, enabled: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO memory_consents(user_id,enabled,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at",
                (user_id, int(enabled), _iso(_now())),
            )

    def has_memory_consent(self, user_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT enabled FROM memory_consents WHERE user_id=?", (user_id,)).fetchone()
        return bool(row and row["enabled"])

    def add_inventory(self, user_id: str, item: str, quantity: str = "", condition: str = "") -> dict:
        item = item.strip()
        quantity = quantity.strip()
        condition = condition.strip()
        if not item or len(item) > 100 or len(quantity) > 80 or len(condition) > 200:
            raise ValueError("物品名称或补充说明不符合长度要求")
        updated_at = _iso(_now())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO inventory(user_id,item,quantity,condition,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(user_id,item) DO UPDATE SET quantity=excluded.quantity,condition=excluded.condition,updated_at=excluded.updated_at",
                (user_id, item, quantity, condition, updated_at),
            )
        return {"item": item, "quantity": quantity, "condition": condition, "updated_at": updated_at}

    def list_inventory(self, user_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT item,quantity,condition,updated_at FROM inventory WHERE user_id=? ORDER BY item",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_inventory(self, user_id: str, item: str) -> bool:
        with self._connect() as conn:
            result = conn.execute("DELETE FROM inventory WHERE user_id=? AND item=?", (user_id, item))
        return result.rowcount > 0

    def clear_inventory(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM inventory WHERE user_id=?", (user_id,))

    def save_feedback(self, session_id: str, kind: str, value: str, note: str = "") -> dict:
        allowed = {
            "checklist_usefulness": {"helpful", "needs_work"},
            "purchase_outcome": {"bought", "not_found", "chose_not_to_buy", "not_yet"},
        }
        if kind not in allowed or value not in allowed[kind]:
            raise ValueError("反馈类型或选项无效")
        if len(session_id) > 100 or len(note) > 500:
            raise ValueError("反馈内容超出长度限制")
        updated_at = _iso(_now())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO feedback(session_id,kind,value,note,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(session_id,kind) DO UPDATE SET value=excluded.value,note=excluded.note,updated_at=excluded.updated_at",
                (session_id, kind, value, note.strip(), updated_at),
            )
        return {"session_id": session_id, "kind": kind, "value": value, "updated_at": updated_at}

    def feedback_summary(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute("SELECT kind,value,COUNT(*) AS count FROM feedback GROUP BY kind,value").fetchall()
        by_kind: dict[str, dict[str, int]] = {}
        for row in rows:
            by_kind.setdefault(row["kind"], {})[row["value"]] = int(row["count"])
        usefulness = by_kind.get("checklist_usefulness", {})
        total = sum(usefulness.values())
        return {
            "checklist_usefulness": {
                "responses": total,
                "helpful": usefulness.get("helpful", 0),
                "needs_work": usefulness.get("needs_work", 0),
                "helpful_rate": round(usefulness.get("helpful", 0) / total, 4) if total else None,
            },
            "purchase_outcomes": by_kind.get("purchase_outcome", {}),
        }

    def submit_template_candidate(self, sections: list[dict]) -> dict:
        if not 2 <= len(sections) <= 6:
            raise ValueError("候选清单必须包含 2–6 个模块")
        normalized: list[dict] = []
        for index, section in enumerate(sections, start=1):
            if not isinstance(section, dict):
                raise ValueError("候选模块格式无效")
            title = _safe_candidate_text(section.get("title"))
            items: dict[str, list[dict]] = {"required": [], "optional": []}
            for bucket in items:
                raw_items = section.get(bucket, [])
                if not isinstance(raw_items, list) or len(raw_items) > 32:
                    raise ValueError("候选项目列表格式无效")
                for raw in raw_items:
                    if not isinstance(raw, dict):
                        raise ValueError("候选项目格式无效")
                    item = {"name": _safe_candidate_text(raw.get("name"))}
                    for field in ("quantity", "condition"):
                        if raw.get(field):
                            item[field] = _safe_candidate_text(raw[field])
                    if isinstance(raw.get("purchasable"), bool):
                        item["purchasable"] = raw["purchasable"]
                    items[bucket].append(item)
            if not items["required"] and not items["optional"]:
                raise ValueError("每个候选模块至少要有一个必备或按需项目")
            normalized.append({"id": f"module-{index}", "title": title, **items})
        candidate_id = f"candidate-{uuid.uuid4().hex[:16]}"
        created_at = _iso(_now())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO template_candidates(candidate_id,sections_json,created_at) VALUES(?,?,?)",
                (candidate_id, json.dumps(normalized, ensure_ascii=False), created_at),
            )
        return {"candidate_id": candidate_id, "status": "pending", "created_at": created_at, "sections": normalized}

    def list_template_candidates(self, status: str = "pending") -> list[dict]:
        if status not in {"pending", "approved", "rejected", "all"}:
            raise ValueError("候选状态无效")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM template_candidates "
                + ("" if status == "all" else "WHERE status=? ")
                + "ORDER BY created_at",
                () if status == "all" else (status,),
            ).fetchall()
        return [self._candidate_dict(row) for row in rows]

    def get_template_candidate(self, candidate_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM template_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
        return self._candidate_dict(row) if row else None

    def evaluate_template_candidate(self, candidate_id: str) -> dict | None:
        candidate = self.get_template_candidate(candidate_id)
        if candidate is None:
            return None
        if candidate["status"] != "pending":
            raise ValueError("只能评测待审核候选")
        sections = candidate["sections"]
        titles = [section["title"].casefold() for section in sections]
        items = [
            item["name"].casefold()
            for section in sections
            for bucket in ("required", "optional")
            for item in section[bucket]
        ]
        generic_names = ("用品", "用具", "物品", "材料", "装备", "物资", "衣物", "其他", "通用", "东西")
        generic_items = sorted({name for name in items if any(word in name for word in generic_names)})
        duplicate_titles = sorted({title for title in titles if titles.count(title) > 1})
        duplicate_items = sorted({name for name in items if items.count(name) > 1})
        checks = {
            "module_count_2_to_6": 2 <= len(sections) <= 6,
            "unique_module_titles": not duplicate_titles,
            "every_module_has_items": all(section["required"] or section["optional"] for section in sections),
            "specific_item_names": not generic_items,
            "no_duplicate_items": not duplicate_items,
        }
        details = {
            "evaluation_version": "checklist-structure-v1",
            "checks": checks,
            "module_count": len(sections),
            "item_count": len(items),
            "duplicate_titles": duplicate_titles,
            "duplicate_items": duplicate_items,
            "generic_items": generic_items,
        }
        passed = all(checks.values())
        evaluated_at = _iso(_now())
        with self._connect() as conn:
            conn.execute(
                "UPDATE template_candidates SET evaluation_passed=?,evaluation_json=?,evaluated_at=? WHERE candidate_id=? AND status='pending'",
                (int(passed), json.dumps(details, ensure_ascii=False), evaluated_at, candidate_id),
            )
        return {**candidate, "evaluation_passed": passed, "evaluation": details, "evaluated_at": evaluated_at}

    @staticmethod
    def _candidate_dict(row: sqlite3.Row) -> dict:
        return {
            "candidate_id": row["candidate_id"], "status": row["status"],
            "created_at": row["created_at"], "sections": json.loads(row["sections_json"]),
            "evaluation_passed": bool(row["evaluation_passed"]) if row["evaluation_passed"] is not None else None,
            "evaluation": json.loads(row["evaluation_json"] or "{}"),
            "evaluated_at": row["evaluated_at"],
            "reviewer": row["reviewer"], "review_note": row["review_note"],
            "published_scene_id": row["published_scene_id"], "published_version": row["published_version"],
        }

    def review_template_candidate(
        self, candidate_id: str, *, status: str, reviewer: str, note: str = "",
        scene_id: str = "", version: int | None = None,
    ) -> dict | None:
        if status not in {"approved", "rejected"}:
            raise ValueError("审核结果必须为 approved 或 rejected")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM template_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
            if row is None:
                return None
            if row["status"] != "pending":
                raise ValueError("该候选已审核，不能重复发布")
            if status == "approved" and not row["evaluation_passed"]:
                raise ValueError("候选必须先通过结构评测，才能人工审核发布")
            conn.execute(
                "UPDATE template_candidates SET status=?,reviewer=?,review_note=?,published_scene_id=?,published_version=? WHERE candidate_id=?",
                (status, reviewer[:80], note[:500], scene_id, version, candidate_id),
            )
            updated = conn.execute("SELECT * FROM template_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
        return self._candidate_dict(updated)

    def record_template_action(
        self, scene_id: str, action: str, reviewer: str, note: str = "", version: int | None = None,
    ) -> dict:
        created_at = _iso(_now())
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO template_actions(scene_id,action,reviewer,note,version,created_at) VALUES(?,?,?,?,?,?)",
                (scene_id, action, reviewer[:80], note[:500], version, created_at),
            )
            action_id = cursor.lastrowid
        return {
            "action_id": action_id, "scene_id": scene_id, "action": action,
            "reviewer": reviewer[:80], "note": note[:500], "version": version, "created_at": created_at,
        }
