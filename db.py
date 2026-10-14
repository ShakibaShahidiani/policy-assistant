import aiosqlite

DB_PATH = "policy_assistant.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                citations TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                policy_id TEXT,
                version TEXT,
                effective_date TEXT,
                status TEXT,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_messages_conv
                ON messages(conversation_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_documents_lookup
                ON documents(conversation_id, policy_id, status);
        """)
        await db.commit()


async def get_or_create_conversation(conversation_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO conversations (id) VALUES (?)",
            (conversation_id,),
        )
        await db.commit()


async def get_active_doc(conversation_id: str, policy_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT filename, version, effective_date FROM documents "
            "WHERE conversation_id=? AND policy_id=? AND status='active'",
            (conversation_id, policy_id),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def mark_doc_superseded(conversation_id: str, policy_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE documents SET status='superseded' "
            "WHERE conversation_id=? AND policy_id=? AND status='active'",
            (conversation_id, policy_id),
        )
        await db.commit()


async def save_document(filename: str, conversation_id: str, meta: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO documents (filename, conversation_id, policy_id, version, effective_date, status) "
            "VALUES (?,?,?,?,?,?)",
            (filename, conversation_id,
             meta.get("policy_id"), meta.get("version"),
             meta.get("effective_date"), meta.get("status")),
        )
        await db.commit()


async def get_history(conversation_id: str, limit: int = 5) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT role, content FROM messages "
            "WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
            (conversation_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in reversed(rows)]


async def save_message(conversation_id: str, role: str,
                       content: str, citations: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content, citations) "
            "VALUES (?,?,?,?)",
            (conversation_id, role, content, citations),
        )
        await db.commit()
