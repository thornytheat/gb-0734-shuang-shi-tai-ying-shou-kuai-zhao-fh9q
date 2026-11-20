from fastapi import APIRouter, Depends

from app.clock import RestatementRequest
from app.modules.m8_restatement import repository, service
from app.store import Store, get_store

router = APIRouter(prefix="/api/restatement", tags=["期间重述"])


@router.get("")
def history(parent_run_id: str | None = None, store: Store = Depends(get_store)):
    with store.connect() as connection:
        return {"restatements": repository.history(connection, parent_run_id)}


@router.post("")
def restate(request: RestatementRequest, store: Store = Depends(get_store)):
    return service.build_restatement(store, request.parent_run_id,
                                     request.supplement_source_ids, request.restate_known)


@router.post("/{restate_id}/rerun")
def rerun(restate_id: str, store: Store = Depends(get_store)):
    return service.rerun(store, restate_id)


@router.get("/{restate_id}")
def detail(restate_id: str, store: Store = Depends(get_store)):
    return service.read_restatement(store, restate_id)


@router.get("/{restate_id}/export")
def export(restate_id: str, store: Store = Depends(get_store)):
    from app.modules.m8_restatement.export import export_evidence
    return export_evidence(store, restate_id)
