from fastapi import Response

from app.errors import Problem
from app.evidence import canonical, digest
from app.modules.m8_restatement.service import read_restatement


def export_evidence(store, restate_id):
    stored = read_restatement(store, restate_id)
    if digest(canonical(stored["result"])) != stored["manifest"]["result_digest"]:
        raise Problem("RESTATEMENT_MISMATCH", "重述证据摘要不符", 409)
    return Response(canonical(stored), media_type="application/json",
                    headers={"Content-Disposition":
                             f'attachment; filename="restatement-{restate_id[:12]}.json"'})
