"""持久化真人输入邮箱。仅供服务端调用；HTTP 鉴权与规则执行由引擎接线。"""
import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager


class HumanInputError(ValueError):
    pass


class HumanService:
    def __init__(self, database):
        self.database = str(database)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    game_id TEXT PRIMARY KEY, seat INTEGER NOT NULL, token_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS pending (
                    game_id TEXT PRIMARY KEY, pending_id TEXT NOT NULL, cursor INTEGER NOT NULL,
                    payload TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS submissions (
                    game_id TEXT NOT NULL, operation_id TEXT NOT NULL, pending_id TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(game_id, operation_id),
                    UNIQUE(game_id, pending_id));
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def bind(self, game_id, seat):
        """可信开局入口调用一次；明文令牌只交给该局本机真人会话。"""
        if type(seat) is not int or not 1 <= seat <= 10:
            raise HumanInputError("invalid seat")
        token = secrets.token_urlsafe(32)
        with self._connect() as db:
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (game_id, seat, self._hash(token)))
        return token

    def _authorize(self, db, game_id, token):
        row = db.execute("SELECT * FROM sessions WHERE game_id=?", (game_id,)).fetchone()
        if row is None or not secrets.compare_digest(row["token_hash"], self._hash(token)):
            raise PermissionError("invalid human session")
        return row["seat"]

    def authenticate(self, game_id, token):
        with self._connect() as db:
            return self._authorize(db, game_id, token)

    def publish(self, game_id, pending_id, cursor, *, contract):
        """引擎在同局锁内发布本人允许动作；不得传入原始状态或私密事件。"""
        payload = json.dumps(contract, ensure_ascii=False, sort_keys=True)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM sessions WHERE game_id=?", (game_id,)).fetchone():
                raise HumanInputError("session not bound")
            old = db.execute("SELECT * FROM pending WHERE game_id=?", (game_id,)).fetchone()
            if old:
                if old["pending_id"] == pending_id:
                    if old["cursor"] != cursor or old["payload"] != payload:
                        raise HumanInputError("pending conflict")
                    return
                if not old["closed"] or cursor <= old["cursor"]:
                    raise HumanInputError("pending operation not completed or stale cursor")
            db.execute("INSERT OR REPLACE INTO pending VALUES (?, ?, ?, ?, 0)",
                       (game_id, pending_id, cursor, payload))

    def pending(self, game_id, token):
        with self._connect() as db:
            seat = self._authorize(db, game_id, token)
            row = db.execute("SELECT * FROM pending WHERE game_id=?", (game_id,)).fetchone()
            if row is None or row["closed"]:
                return {"seat_id": seat, "status": "no_pending"}
            queued = db.execute("SELECT 1 FROM submissions WHERE game_id=? AND pending_id=?",
                                (game_id, row["pending_id"])).fetchone()
            return dict(seat_id=seat, status="submitted" if queued else "waiting_human",
                        pending_id=row["pending_id"], cursor=row["cursor"],
                        contract=json.loads(row["payload"]))

    def submit(self, game_id, token, *, operation_id, pending_id, expected_cursor, action):
        """只持久化提交，不执行技能。引擎须统一校验，并以 operation_id 幂等执行。"""
        if not operation_id or not isinstance(action, dict):
            raise HumanInputError("operation_id and action object required")
        if set(action) - {"action", "target", "text"} or not isinstance(action.get("action"), str):
            raise HumanInputError("submit action/target/text only; no reason or seat override")
        payload = json.dumps(action, ensure_ascii=False, sort_keys=True)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._authorize(db, game_id, token)
            old = db.execute("SELECT * FROM submissions WHERE game_id=? AND operation_id=?",
                             (game_id, operation_id)).fetchone()
            if old:
                if old["pending_id"] != pending_id or old["payload"] != payload:
                    raise HumanInputError("operation conflict")
                return {"status": "submitted", "operation_id": operation_id}
            row = db.execute("SELECT * FROM pending WHERE game_id=?", (game_id,)).fetchone()
            if row is None or row["closed"] or row["pending_id"] != pending_id or row["cursor"] != expected_cursor:
                raise HumanInputError("stale pending or cursor")
            try:
                db.execute("INSERT INTO submissions VALUES (?, ?, ?, ?)",
                           (game_id, operation_id, pending_id, payload))
            except sqlite3.IntegrityError:
                raise HumanInputError("pending already submitted") from None
        return {"status": "submitted", "operation_id": operation_id}

    def queued(self, game_id):
        """可信引擎入口读取；不挂到浏览器路由。"""
        with self._connect() as db:
            row = db.execute("SELECT s.* FROM submissions s JOIN pending p ON s.game_id=p.game_id "
                             "AND s.pending_id=p.pending_id WHERE s.game_id=? AND p.closed=0", (game_id,)).fetchone()
            return dict(operation_id=row["operation_id"], pending_id=row["pending_id"],
                        action=json.loads(row["payload"])) if row else None

    def complete(self, game_id, pending_id):
        """引擎持久化执行结果后确认；崩溃恢复可重取队列，执行端须幂等。"""
        with self._connect() as db:
            db.execute("UPDATE pending SET closed=1 WHERE game_id=? AND pending_id=?", (game_id, pending_id))
