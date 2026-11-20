import json

from app.errors import Problem

FIELDS = ("restate_id, parent_run_id, rule_version, generated_at, elapsed_ms, result_digest")


def get(connection, restate_id):
    row = connection.execute(f"SELECT {FIELDS}, payload FROM restatements WHERE restate_id=?",
                             (restate_id,)).fetchone()
    if row is None:
        raise Problem("RESTATEMENT_NOT_FOUND", "重述批次不存在", 404)
    return {"manifest": {key: row[key] for key in row.keys() if key != "payload"},
            "result": json.loads(row["payload"])}


def sources(connection, restate_id):
    rows = connection.execute(
        "SELECT source_id, seq, role FROM restatement_sources WHERE restate_id=? ORDER BY seq",
        (restate_id,)).fetchall()
    return [dict(row) for row in rows]


def insert(connection, manifest, payload, source_rows):
    values = tuple(manifest[field.strip()] for field in FIELDS.split(","))
    cursor = connection.execute(
        f"INSERT INTO restatements ({FIELDS}, payload) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(restate_id) DO NOTHING", (*values, payload))
    created = cursor.rowcount == 1
    if created:
        connection.executemany(
            "INSERT INTO restatement_sources (restate_id, source_id, seq, role) VALUES (?, ?, ?, ?)",
            [(manifest["restate_id"], row["source_id"], row["seq"], row["role"]) for row in source_rows])
    else:
        # 已存在批次：持有行锁直到事务结束，阻止并发重跑并发插入来源清单
        connection.execute("SELECT restate_id FROM restatements WHERE restate_id=?",
                           (manifest["restate_id"],)).fetchone()
    return created


def history(connection, parent_run_id=None):
    rows = connection.execute(
        f"SELECT {FIELDS} FROM restatements WHERE (? IS NULL OR parent_run_id=?) "
        "ORDER BY generated_at DESC, restate_id LIMIT 100", (parent_run_id, parent_run_id))
    return [dict(row) for row in rows]
