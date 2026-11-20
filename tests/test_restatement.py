from concurrent.futures import ThreadPoolExecutor

from app.evidence import canonical, digest
from app.modules.m8_restatement.merge import LINE_BASE, SourceBundle, merge
from app.modules.m1_intake.reader import read


def supplement_csv(*rows):
    header = ("event_id,invoice_id,customer_id,kind,effective,valid_to,known,due,"
              "amount_cny,ref,note\r\n")
    return (header + "".join(row + "\r\n" for row in rows)).encode()


def make_bundle(seq, content, role="SUPPLEMENT"):
    return SourceBundle(seq, digest(content), role, read(content))


def test_merged_intake_keeps_parent_events_and_synthetic_lines(data, raw):
    parent = SourceBundle(0, digest(raw), "ARCHIVE", data)
    extra = supplement_csv(
        "Q-V1,AR-9001,9001,VERSION,2026-01-10,,2026-02-10T09:00+08:00,2026-02-10,100.00,,迟报发票")
    result = merge([parent, make_bundle(1, extra)])
    parent_lines = {event.line for event in data.events}
    assert {event.line for event in result.intake.events if event.line in parent_lines} == parent_lines
    new_event = next(event for event in result.intake.events if event.event_id == "Q-V1")
    assert new_event.line == LINE_BASE + 2
    assert result.provenance[new_event.line] == {"source_id": digest(extra), "source_seq": 1,
                                                  "source_role": "SUPPLEMENT", "source_line": 2}


def test_cross_source_duplicate_is_suppressed_with_provenance(data, raw):
    # 与样例中 E-P1 完全同内容（含同 UTC 知悉时刻）的跨来源副本
    dup = ("E-P1,AR-1005,00501,PAYMENT,2026-01-31,,2026-02-06T04:00Z,,50.65,E-V1,"
           "结清，正好在知悉截止时点\r\n")
    extra = supplement_csv(dup.rstrip("\r\n"))
    result = merge([SourceBundle(0, digest(raw), "ARCHIVE", data), make_bundle(1, extra)])
    assert [event.event_id for event in result.intake.events].count("E-P1") == 1
    note = next(row for row in result.intake.duplicates if row["event_id"] == "E-P1")
    assert note["code"] == "CROSS_SOURCE_DUPLICATE" and note["source_seq"] == 1 and note["source_line"] == 2


def test_cross_source_conflict_keeps_earliest_and_isolates_later(data, raw):
    extra = supplement_csv(
        "H-V1,AR-1008,00018,VERSION,2025-12-01,,2026-02-15T09:00+08:00,2025-12-31,120.00,,错误重报")
    result = merge([SourceBundle(0, digest(raw), "ARCHIVE", data), make_bundle(1, extra)])
    kept = next(event for event in result.intake.events if event.event_id == "H-V1")
    assert kept.cents == 10040  # 原归档 100.40 元原样保留
    conflict = next(row for row in result.intake.quarantine if row["event_id"] == "H-V1")
    assert conflict["code"] == "CROSS_SOURCE_CONFLICT" and conflict["related"][0]["event_id"] == "H-V1"


def test_same_source_internal_conflict_is_not_retried_as_cross_source(data, raw):
    # 样例内 REV-PAST 同号出现两次（M1 同号冲突），合并后两条都须原样保留
    extra = supplement_csv(
        "Q-V1,AR-9001,9001,VERSION,2026-01-10,,2026-02-10T09:00+08:00,2026-02-10,100.00,,迟报发票")
    result = merge([SourceBundle(0, digest(raw), "ARCHIVE", data), make_bundle(1, extra)])
    rev_past = [event for event in result.intake.events if event.event_id == "REV-PAST"]
    assert len(rev_past) == 2
    assert all(event.line < LINE_BASE for event in rev_past)


def test_supplement_quarantine_keeps_own_reason_and_source(data, raw):
    extra = supplement_csv(
        "BAD-S,AR-9002,9002,VERSION,2026-01-18,,2026-02-16T09:00+08:00,2026-02-12,N/A,,无效金额")
    result = merge([SourceBundle(0, digest(raw), "ARCHIVE", data), make_bundle(1, extra)])
    row = next(item for item in result.intake.quarantine if item["event_id"] == "BAD-S")
    assert row["code"] == "AMOUNT_MISSING" and row["source_seq"] == 1 and row["source_line"] == 2


def _archive(client, query):
    return client.post('/api/archive', json=query).json()['manifest']['run_id']


def _supplement(client):
    return client.get('/api/intake/sample-supplement').json()


def test_restatement_balances_reconcile_and_keep_original_archive(client, query):
    run = _archive(client, query)
    supplement = _supplement(client)
    reply = client.post('/api/restatement', json={
        "parent_run_id": run, "supplement_source_ids": [supplement["source_id"]],
        "restate_known": "2026-03-01T12:00+08:00"})
    assert reply.status_code == 200 and reply.json()["created"] is True
    body = reply.json()
    assert body["balances"]["original_closing_cents"] == 340065
    assert body["balances"]["restated_closing_cents"] == 370015
    detail = client.get(f"/api/restatement/{body['manifest']['restate_id']}").json()
    result = detail["result"]
    recon = result["attribution"]["balance_reconciliation"]
    assert recon["difference_cents"] == 0
    assert (recon["late_balance_cents"], recon["correction_balance_cents"]) == (25000, 4950)
    # 原归档批次的面额、证据和摘要原样保留
    original = client.get(f"/api/archive/{run}/export").json()
    assert original["manifest"]["run_id"] == run
    assert original["result"]["bridge"]["totals"]["closing_cents"] == 340065
    assert digest(canonical(original["result"])) == original["manifest"]["result_digest"]
    # 重述挂回原批次
    assert result["parent"]["run_id"] == run and detail["manifest"]["parent_run_id"] == run


def test_restatement_classifies_correction_and_late_vouchers(client, query):
    run = _archive(client, query)
    supplement = _supplement(client)
    restate_id = client.post('/api/restatement', json={
        "parent_run_id": run, "supplement_source_ids": [supplement["source_id"]],
        "restate_known": "2026-03-01T12:00+08:00"}).json()["manifest"]["restate_id"]
    result = client.get(f"/api/restatement/{restate_id}").json()["result"]
    classes = {row["event"]["event_id"]: row["class"] for row in result["supplement_evidence"]["accepted"]}
    assert classes == {"L-V1": "LATE_REPORT", "L-P1": "LATE_REPORT", "A-FIX": "CORRECTION"}
    codes = {row["event_id"]: row["code"] for row in
             result["supplement_evidence"]["duplicates"] + result["supplement_evidence"]["quarantine"]}
    assert codes == {"E-P1": "CROSS_SOURCE_DUPLICATE", "H-V1": "CROSS_SOURCE_CONFLICT",
                     "BAD-SUP": "AMOUNT_MISSING"}
    # 每张被采信凭证都能追回收件来源和物理行
    assert all(row["source_id"] == supplement["source_id"] and row["source_line"] >= 1
               for row in result["supplement_evidence"]["accepted"])


def test_same_supplements_do_not_generate_a_second_batch(client, query):
    run = _archive(client, query)
    supplement = _supplement(client)["source_id"]
    payload = {"parent_run_id": run, "supplement_source_ids": [supplement],
               "restate_known": "2026-03-01T12:00+08:00"}
    first = client.post('/api/restatement', json=payload).json()
    second = client.post('/api/restatement', json=payload).json()
    assert first["created"] is True and second["created"] is False
    assert first["manifest"]["restate_id"] == second["manifest"]["restate_id"]
    rerun = client.post(f"/api/restatement/{first['manifest']['restate_id']}/rerun").json()
    assert rerun["created"] is False and rerun["manifest"] == first["manifest"]
    assert len(client.get('/api/restatement').json()["restatements"]) == 1


def test_concurrent_restatement_calls_leave_one_batch(client, query):
    run = _archive(client, query)
    supplement = _supplement(client)["source_id"]
    payload = {"parent_run_id": run, "supplement_source_ids": [supplement],
               "restate_known": "2026-03-01T12:00+08:00"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: client.post('/api/restatement', json=payload), range(4)))
    assert [reply.status_code for reply in replies] == [200] * 4
    assert sum(reply.json()["created"] for reply in replies) == 1
    assert len({reply.json()["manifest"]["restate_id"] for reply in replies}) == 1


def test_distinct_supplement_sets_and_cuts_have_distinct_batches(client, query):
    run = _archive(client, query)
    supplement = _supplement(client)["source_id"]
    first = client.post('/api/restatement', json={
        "parent_run_id": run, "supplement_source_ids": [supplement],
        "restate_known": "2026-03-01T12:00+08:00"}).json()
    second = client.post('/api/restatement', json={
        "parent_run_id": run, "supplement_source_ids": [supplement],
        "restate_known": "2026-03-02T12:00+08:00"}).json()
    assert first["manifest"]["restate_id"] != second["manifest"]["restate_id"]
    assert len(client.get('/api/restatement').json()["restatements"]) == 2


def test_latest_intake_only_alternative_is_exported_and_marked_not_adopted(client, query):
    run = _archive(client, query)
    supplement = _supplement(client)["source_id"]
    restate_id = client.post('/api/restatement', json={
        "parent_run_id": run, "supplement_source_ids": [supplement],
        "restate_known": "2026-03-01T12:00+08:00"}).json()["manifest"]["restate_id"]
    export = client.get(f"/api/restatement/{restate_id}/export")
    assert export.status_code == 200
    assert export.headers["content-disposition"] == (
        f'attachment; filename="restatement-{restate_id[:12]}.json"')
    body = export.json()
    alternative = body["result"]["alternatives"][0]
    assert alternative["adopted"] is False and alternative["policy"] == "LATEST_INTAKE_ONLY"
    assert alternative["difference_vs_adopted_cents"] != 0
    assert body["result"]["policy"] == "ARCHIVE_PLUS_SUPPLEMENTS"
    assert digest(canonical(body["result"])) == body["manifest"]["result_digest"]
    # 导出带着完整来路：原批次、逐来源清单和逐凭证来源
    assert body["result"]["parent"]["run_id"] == run
    assert [row["role"] for row in body["sources"]] == ["ARCHIVE", "SUPPLEMENT"]
    assert body["result"]["provenance"]


def test_restatement_validation_error_codes(client, query):
    run = _archive(client, query)
    supplement = _supplement(client)["source_id"]
    base = {"parent_run_id": run, "supplement_source_ids": [supplement],
            "restate_known": "2026-03-01T12:00+08:00"}
    cases = [
        ({**base, "supplement_source_ids": []}, "NO_SUPPLEMENT"),
        ({**base, "supplement_source_ids": [supplement, supplement]}, "DUPLICATE_SOURCE"),
        ({**base, "supplement_source_ids": [query["source_id"]]}, "DUPLICATE_SOURCE"),
        ({**base, "supplement_source_ids": ["missing"]}, "SOURCE_NOT_FOUND"),
        ({**base, "restate_known": "2026-01-01T00:00+08:00"}, "INVALID_CUT"),
        ({**base, "restate_known": "not-a-timestamp"}, "INVALID_CUT"),
        ({"parent_run_id": "missing", "supplement_source_ids": [supplement],
          "restate_known": "2026-03-01T12:00+08:00"}, "RUN_NOT_FOUND"),
    ]
    for payload, code in cases:
        reply = client.post('/api/restatement', json=payload)
        assert (reply.status_code, reply.json()["code"]) != (200, None)
        assert reply.json()["code"] == code, payload


def test_cut_must_cover_all_supplement_vouchers(client, query):
    run = _archive(client, query)
    late = client.post('/api/intake', files={'file': ('late.csv', supplement_csv(
        "M-V1,AR-9030,9030,VERSION,2026-01-10,,2026-03-20T09:00+08:00,2026-02-10,80.00,,"))}).json()
    reply = client.post('/api/restatement', json={
        "parent_run_id": run, "supplement_source_ids": [late["source_id"]],
        "restate_known": "2026-03-01T12:00+08:00"})
    assert (reply.status_code, reply.json()["code"]) == (422, "EVIDENCE_AFTER_CUT")


def test_tampered_restatement_blocks_export_without_overwrite(client, query):
    run = _archive(client, query)
    supplement = _supplement(client)["source_id"]
    restate_id = client.post('/api/restatement', json={
        "parent_run_id": run, "supplement_source_ids": [supplement],
        "restate_known": "2026-03-01T12:00+08:00"}).json()["manifest"]["restate_id"]
    with client.app.state.store.connect() as connection:
        connection.execute("UPDATE restatements SET result_digest='bad' WHERE restate_id=?", (restate_id,))
    assert client.get(f"/api/restatement/{restate_id}/export").json()["code"] == "RESTATEMENT_MISMATCH"
    assert client.get('/api/restatement').json()["restatements"][0]["result_digest"] == "bad"


def test_tampered_parent_archive_blocks_restatement(client, query):
    run = _archive(client, query)
    supplement = _supplement(client)["source_id"]
    with client.app.state.store.connect() as connection:
        connection.execute("UPDATE archives SET result_digest='bad' WHERE run_id=?", (run,))
    reply = client.post('/api/restatement', json={
        "parent_run_id": run, "supplement_source_ids": [supplement],
        "restate_known": "2026-03-01T12:00+08:00"})
    assert (reply.status_code, reply.json()["code"]) == (409, "ARCHIVE_MISMATCH")
