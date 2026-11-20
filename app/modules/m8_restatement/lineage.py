"""重述来路：把余额变化拆成迟报回补与更正，逐凭证挂回来源。"""
from app.modules.m3_snapshot.service import ACCOUNT

# 迟报（补报）：结账后才知悉的新票、收款、冲销；更正：引用原归档版本的修订。
LATE = "LATE_REPORT"
CORRECTION = "CORRECTION"


def classify_supplement_events(merged, provenance):
    """给补充来源中被采信的每个事件分类，并带来源行证据。"""
    rows = []
    for event in merged.events:
        origin = provenance[event.line]
        if origin["source_seq"] == 0:
            continue
        kind = CORRECTION if (event.kind == "VERSION" and event.ref) else LATE
        rows.append({"line": event.line, "event": event.public(), "class": kind, **origin})
    return sorted(rows, key=lambda row: (row["source_seq"], row["source_line"]))


def _index(snapshot):
    return {row["invoice_id"]: row for row in snapshot["invoices"]}


def attribute_deltas(original_closing, restated_closing, supplement_rows):
    """发票级归属：更正只改原票，迟报以新票为主；混合影响在事件清单里逐凭证可见。"""
    correction_invoices = {row["event"]["invoice_id"] for row in supplement_rows if row["class"] == CORRECTION}
    before, after = _index(original_closing), _index(restated_closing)
    late_total = {key: 0 for key in ACCOUNT}
    correction_total = {key: 0 for key in ACCOUNT}
    invoices = []
    for invoice_id in sorted(before.keys() | after.keys()):
        left, right = before.get(invoice_id), after.get(invoice_id)
        delta = {key: (right or {}).get(key, 0) - (left or {}).get(key, 0) for key in ACCOUNT}
        if not any(delta.values()):
            continue
        bucket = CORRECTION if invoice_id in correction_invoices else LATE
        target = correction_total if bucket == CORRECTION else late_total
        for key in ACCOUNT:
            target[key] += delta[key]
        invoices.append({"invoice_id": invoice_id,
                         "customer_id": (right or left)["customer_id"], "class": bucket,
                         "original": left, "restated": right, "delta": delta})
    return {"late_totals": late_total, "correction_totals": correction_total, "invoices": invoices}
