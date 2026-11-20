import sqlite3
from datetime import datetime, timezone
from time import perf_counter_ns

from app.clock import Cut, Period, parse_known, period_query, utc_text
from app.config import RULE_VERSION
from app.errors import Problem
from app.evidence import canonical, digest
from app.modules.m1_intake.service import load
from app.modules.m3_snapshot.service import snapshot
from app.modules.m6_bridge.service import period_book
from app.modules.m7_archive import repository as archive_repository
from app.modules.m8_restatement import repository
from app.modules.m8_restatement.lineage import attribute_deltas, classify_supplement_events
from app.modules.m8_restatement.merge import SourceBundle, merge

ADOPTED_POLICY = "ARCHIVE_PLUS_SUPPLEMENTS"
ALTERNATIVE_POLICY = "LATEST_INTAKE_ONLY"


def read_restatement(store, restate_id):
    with store.connect() as connection:
        stored = repository.get(connection, restate_id)
        stored["sources"] = repository.sources(connection, restate_id)
    return stored


def _verify_parent(connection, parent_run_id):
    parent = archive_repository.get(connection, parent_run_id)
    if digest(canonical(parent["result"])) != parent["manifest"]["result_digest"]:
        raise Problem("ARCHIVE_MISMATCH", "原归档结果摘要不符，不能作为重述来源", 409)
    return parent


def _parent_period(parent):
    period = parent["result"]["bridge"]["period"]
    opening, closing = period["opening"], period["closing"]
    return period_query(opening["effective"], opening["known"], closing["effective"], closing["known"])


def build_restatement(store, parent_run_id, supplement_ids, restate_known_text):
    try:
        restate_known = parse_known(restate_known_text)
    except ValueError as exc:
        raise Problem("INVALID_CUT", str(exc)) from exc
    if not supplement_ids:
        raise Problem("NO_SUPPLEMENT", "重述至少需要一份补报收件", 422)
    if len(supplement_ids) != len(set(supplement_ids)):
        raise Problem("DUPLICATE_SOURCE", "补报收件列表存在重复来源", 422)

    with store.connect() as connection:
        parent = _verify_parent(connection, parent_run_id)
        parent_source_id = parent["manifest"]["source_id"]
        if parent_source_id in supplement_ids:
            raise Problem("DUPLICATE_SOURCE", "原归档来源不能同时作为补报收件", 422)
        ordered = [parent_source_id, *supplement_ids]
        if any(connection.execute("SELECT 1 FROM sources WHERE source_id=?", (sid,)).fetchone() is None
               for sid in ordered):
            raise Problem("SOURCE_NOT_FOUND", "存在尚未收件的来源文件", 404)
        intakes = {sid: load(store, sid) for sid in ordered}

    period = _parent_period(parent)
    if restate_known < period.closing.known:
        raise Problem("INVALID_CUT", "重述知悉截止不得早于原结账知悉截止", 422)
    restate_period = Period(period.opening, Cut(period.closing.effective, restate_known))

    bundles = [SourceBundle(seq, sid, "ARCHIVE" if seq == 0 else "SUPPLEMENT", intakes[sid])
               for seq, sid in enumerate(ordered)]
    merged = merge(bundles)
    latest_known = max((event.known for event in merged.intake.events), default=None)
    if latest_known is not None and latest_known > restate_known:
        raise Problem("EVIDENCE_AFTER_CUT",
                      "重述知悉截止早于补充凭证的最晚知悉时间，无法覆盖全部凭证", 422)

    book = period_book(merged.intake, restate_period)
    if book["bridge"]["status"] != "MATCHED":
        raise Problem("UNBALANCED", "重述期间桥接存在差额", 409)

    original_closing = parent["result"]["closing"]
    restated_closing = book["closing"]
    supplement_rows = classify_supplement_events(merged.intake, merged.provenance)
    attribution = attribute_deltas(original_closing, restated_closing, supplement_rows)
    original_balance = original_closing["totals"]["balance_cents"]
    delta_balance = (attribution["late_totals"]["balance_cents"]
                     + attribution["correction_totals"]["balance_cents"])
    attribution["balance_reconciliation"] = {
        "original_closing_cents": original_balance,
        "late_balance_cents": attribution["late_totals"]["balance_cents"],
        "correction_balance_cents": attribution["correction_totals"]["balance_cents"],
        "restated_closing_cents": restated_closing["totals"]["balance_cents"],
        "difference_cents": restated_closing["totals"]["balance_cents"] - original_balance - delta_balance,
    }
    if attribution["balance_reconciliation"]["difference_cents"] != 0:
        raise Problem("UNBALANCED", "重述余额勾稽存在差额", 409)

    # 未采纳的另一种口径：只拿最新一份收件整体重算结账日截面。
    latest_source_id = supplement_ids[-1]
    naive = snapshot(intakes[latest_source_id], restate_period.closing)
    alternative = {
        "policy": ALTERNATIVE_POLICY, "adopted": False, "source_id": latest_source_id,
        "closing_totals": naive["totals"],
        "difference_vs_adopted_cents": (naive["totals"]["balance_cents"]
                                        - restated_closing["totals"]["balance_cents"]),
        "note": "仅用最新收件会丢失原归档已采信凭证，不作为重述口径，仅披露差异",
    }

    source_rows = [{"seq": seq, "source_id": sid, "role": bundle.role}
                   for seq, (sid, bundle) in enumerate(zip(ordered, bundles))]
    inputs = {"parent_run_id": parent_run_id, "sources": ordered,
              "rule_version": RULE_VERSION, "restate_known": utc_text(restate_known)}
    restate_id = digest(canonical(inputs))
    result = {
        "policy": ADOPTED_POLICY,
        "restatement": {"restate_id": restate_id, "parent_run_id": parent_run_id,
                        "rule_version": RULE_VERSION, "restate_period": restate_period.public()},
        "parent": {"run_id": parent_run_id, "source_id": parent_source_id,
                   "result_digest": parent["manifest"]["result_digest"],
                   "generated_at": parent["manifest"]["generated_at"],
                   "period": period.public(), "closing": original_closing},
        "sources": merged.source_reports,
        "book": book,
        "provenance": {str(line): origin for line, origin in sorted(merged.provenance.items())},
        "supplement_evidence": {
            "accepted": supplement_rows,
            "quarantine": [row for row in merged.intake.quarantine if row.get("source_seq", 0) > 0],
            "duplicates": [row for row in merged.intake.duplicates if row.get("source_seq", 0) > 0],
        },
        "attribution": attribution,
        "alternatives": [alternative],
    }
    return _persist(store, restate_id, parent_run_id, source_rows, result)


def _persist(store, restate_id, parent_run_id, source_rows, result):
    start = perf_counter_ns()
    payload = canonical(result)
    manifest = {"restate_id": restate_id, "parent_run_id": parent_run_id, "rule_version": RULE_VERSION,
                "elapsed_ms": (perf_counter_ns() - start) // 1_000_000,
                "generated_at": utc_text(datetime.now(timezone.utc)), "result_digest": digest(payload)}
    try:
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            created = repository.insert(connection, manifest, payload, source_rows)
            stored = repository.get(connection, restate_id)
            if (stored["manifest"]["result_digest"] != manifest["result_digest"]
                    or digest(canonical(stored["result"])) != manifest["result_digest"]):
                raise Problem("RESTATEMENT_MISMATCH", "重述摘要与已存批次不符", 409)
            saved_sources = repository.sources(connection, restate_id)
    except sqlite3.Error as exc:
        raise Problem("RESTATEMENT_WRITE_FAILED", "重述写入失败，请重跑", 503) from exc
    if not created and saved_sources != source_rows:
        raise Problem("RESTATEMENT_MISMATCH", "同号重述的来源清单不一致", 409)
    return {"created": created, "manifest": stored["manifest"],
            "restate_period": result["restatement"]["restate_period"],
            "sources": source_rows,
            "balances": {"original_closing_cents": result["attribution"]["balance_reconciliation"]["original_closing_cents"],
                         "restated_closing_cents": result["attribution"]["balance_reconciliation"]["restated_closing_cents"]}}


def rerun(store, restate_id):
    stored = read_restatement(store, restate_id)
    if stored["manifest"]["rule_version"] != RULE_VERSION:
        raise Problem("RULE_VERSION_CHANGED", "重述使用的规则版本已变更", 409)
    saved = stored["sources"]
    result = stored["result"]
    candidate_inputs = {
        "parent_run_id": stored["manifest"]["parent_run_id"],
        "sources": [row["source_id"] for row in saved],
        "rule_version": RULE_VERSION,
        "restate_known": result["restatement"]["restate_period"]["closing"]["known"],
    }
    if digest(canonical(candidate_inputs)) != restate_id:
        raise Problem("RESTATEMENT_MISMATCH", "重述参数与批次编号不符", 409)
    supplement_ids = [row["source_id"] for row in saved if row["role"] == "SUPPLEMENT"]
    return build_restatement(store, stored["manifest"]["parent_run_id"], supplement_ids,
                             candidate_inputs["restate_known"])
