"""Authenticated inspection and retry endpoints for course knowledge builds."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import CurrentUser
from .knowledge_catalog_dispatch import DispatchRepository
from .knowledge_catalog_store import CatalogConflict
from .knowledge_store import KnowledgeRepository, build_config
from .knowledge_tree import public_catalog_job, read_catalog_job, read_tree
from .postgres import PostgresStore
from .schemas import Identifier


def knowledge_router(settings, authorize, require_course, conversation_store, require_file_access):
    router = APIRouter(prefix="/api/courses/{course_id}", tags=["course-knowledge"])

    def repo_for(course_id, user):
        require_course(course_id, user)
        store = conversation_store()
        if not isinstance(store, PostgresStore):
            raise HTTPException(503, "课程知识库需要 PostgreSQL")
        return KnowledgeRepository(store)

    def job_for(repo, course_id, job_id):
        row = repo.connection.execute(
            "SELECT * FROM knowledge_build_jobs WHERE user_id=%s AND course_id=%s AND job_id=%s",
            (repo.user, course_id, job_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "知识库构建任务不存在")
        return row

    @router.post(
        "/documents/{document_id}/knowledge-build", dependencies=[Depends(require_file_access)]
    )
    def start_build(
        course_id: Identifier, document_id: Identifier, user: CurrentUser = Depends(authorize)
    ):
        repo = repo_for(course_id, user)
        return repo.enqueue_ready(course_id, document_id, settings)

    @router.get("/knowledge-build-jobs")
    def list_jobs(
        course_id: Identifier,
        user: CurrentUser = Depends(authorize),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        repo = repo_for(course_id, user)
        rows = repo.connection.execute(
            "SELECT * FROM knowledge_build_jobs WHERE user_id=%s AND course_id=%s "
            "ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (repo.user, course_id, limit, offset),
        ).fetchall()
        return {"items": rows}

    @router.get("/knowledge-build-jobs/{job_id}")
    def read_job(course_id: Identifier, job_id: UUID, user: CurrentUser = Depends(authorize)):
        return job_for(repo_for(course_id, user), course_id, job_id)

    @router.get("/knowledge-build-status")
    def build_status(course_id: Identifier, user: CurrentUser = Depends(authorize)):
        repo = repo_for(course_id, user)
        rows = repo.connection.execute(
            """SELECT DISTINCT ON (j.document_id) j.job_id,j.document_id,j.status,j.stage,
               j.error_message,j.updated_at,
               (SELECT count(*) FROM material_extraction_blocks b
                  WHERE b.build_job_id=j.job_id) AS total_blocks,
               (SELECT count(*) FROM material_extraction_blocks b
                  WHERE b.build_job_id=j.job_id
                    AND b.extract_status='succeeded') AS completed_blocks,
               (SELECT count(*) FROM knowledge_candidates c WHERE c.build_job_id=j.job_id
                  AND c.status='review_needed') AS review_count,
               (SELECT count(*) FROM knowledge_candidates c WHERE c.build_job_id=j.job_id
                  AND c.status='rejected') AS rejected_count
               FROM knowledge_build_jobs j WHERE j.user_id=%s AND j.course_id=%s
               ORDER BY j.document_id,j.created_at DESC""",
            (repo.user, course_id),
        ).fetchall()
        return {"items": rows}

    @router.get("/knowledge-build-jobs/{job_id}/decisions")
    def match_decisions(
        course_id: Identifier,
        job_id: UUID,
        user: CurrentUser = Depends(authorize),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        repo = repo_for(course_id, user)
        job_for(repo, course_id, job_id)
        rows = repo.connection.execute(
            """SELECT * FROM knowledge_match_decisions WHERE user_id=%s AND course_id=%s
               AND build_job_id=%s ORDER BY updated_at DESC,id LIMIT %s OFFSET %s""",
            (repo.user, course_id, job_id, limit, offset),
        ).fetchall()
        return {"items": rows}

    @router.post(
        "/knowledge-build-jobs/{job_id}/retry", dependencies=[Depends(require_file_access)]
    )
    def retry_job(course_id: Identifier, job_id: UUID, user: CurrentUser = Depends(authorize)):
        repo = repo_for(course_id, user)
        job = job_for(repo, course_id, job_id)
        repo.source(job)
        if job["config_json"] != build_config(settings):
            raise HTTPException(409, "配置已变化，请通过资料重新创建构建任务")
        row = repo.retry(course_id, job_id)
        if not row:
            raise HTTPException(409, "仅失败任务可以重试")
        return row

    @router.get("/knowledge-build-jobs/{job_id}/candidates")
    def list_candidates(
        course_id: Identifier,
        job_id: UUID,
        user: CurrentUser = Depends(authorize),
        limit: int = Query(100, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        repo = repo_for(course_id, user)
        job_for(repo, course_id, job_id)
        rows = repo.connection.execute(
            """SELECT id,block_id,candidate_index,name,normalized_name,description,
               importance_score,facts_json,status,matched_candidate_id,matched_point_id,
               match_relation,match_method,match_score,match_reason,created_at,updated_at
               FROM knowledge_candidates WHERE user_id=%s AND course_id=%s AND build_job_id=%s
               ORDER BY created_at,id LIMIT %s OFFSET %s""",
            (repo.user, course_id, job_id, limit, offset),
        ).fetchall()
        return {"items": rows}

    @router.get("/knowledge-tree")
    def course_tree(course_id: Identifier, user: CurrentUser = Depends(authorize)):
        return read_tree(repo_for(course_id, user), course_id)

    @router.get("/knowledge-catalog/jobs/{job_id}")
    def catalog_job(course_id: Identifier, job_id: UUID, user: CurrentUser = Depends(authorize)):
        job = read_catalog_job(repo_for(course_id, user), course_id, job_id)
        if job is None:
            raise HTTPException(404, "目录任务不存在")
        return job

    @router.post(
        "/knowledge-catalog/jobs/{job_id}/retry", dependencies=[Depends(require_file_access)]
    )
    def retry_catalog_job(
        course_id: Identifier, job_id: UUID, user: CurrentUser = Depends(authorize)
    ):
        repo = DispatchRepository(repo_for(course_id, user).store)
        try:
            job = repo.retry_failed(course_id, job_id)
        except CatalogConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        if job is None:
            raise HTTPException(404, "目录任务不存在")
        return public_catalog_job(job)

    @router.get("/knowledge-points")
    def list_points(
        course_id: Identifier,
        user: CurrentUser = Depends(authorize),
        limit: int = Query(100, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        repo = repo_for(course_id, user)
        rows = repo.connection.execute(
            """SELECT id,name,normalized_name,description,parent_id,importance_score,status,
               created_at,updated_at FROM knowledge_points WHERE user_id=%s AND course_id=%s
               AND status='active' ORDER BY name,id LIMIT %s OFFSET %s""",
            (repo.user, course_id, limit, offset),
        ).fetchall()
        return {"items": rows}

    @router.get("/knowledge-points/{point_id}")
    def read_point(course_id: Identifier, point_id: UUID, user: CurrentUser = Depends(authorize)):
        repo = repo_for(course_id, user)
        point = repo.connection.execute(
            """SELECT id,name,normalized_name,description,parent_id,importance_score,status
               FROM knowledge_points WHERE user_id=%s AND course_id=%s AND id=%s""",
            (repo.user, course_id, point_id),
        ).fetchone()
        if not point:
            raise HTTPException(404, "知识点不存在")
        facts = repo.connection.execute(
            """SELECT f.id,f.fact_text,f.status,coalesce((SELECT jsonb_agg(jsonb_build_object(
               'material_version_id',e.material_version_id,'locator_id',e.locator_id,
               'quote',e.quote,'quote_verified',e.quote_verified,'support_status',e.support_status))
               FROM fact_evidence e WHERE e.user_id=f.user_id AND e.course_id=f.course_id
               AND e.fact_id=f.id),'[]'::jsonb) AS evidence FROM knowledge_facts f
               WHERE f.user_id=%s AND f.course_id=%s AND f.point_id=%s
               ORDER BY f.created_at,f.id""",
            (repo.user, course_id, point_id),
        ).fetchall()
        return {**point, "facts": facts}

    return router
