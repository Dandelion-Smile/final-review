"""Independent worker: parsed materials -> candidates -> deduplication -> atomic merge."""

import argparse
import copy
import logging
from threading import Event, Thread

from .config import Settings
from .embeddings import build_embeddings
from .knowledge import (
    Extraction,
    dedup_internal,
    digest,
    extract,
    normalize,
    plan_matches,
    split_blocks,
    validate_embedding,
    verify_facts,
)
from .knowledge_matching import AuditedMatcher
from .knowledge_store import (
    KnowledgeRepository,
    LeaseLost,
    SnapshotChanged,
    SourceUnavailable,
    build_config,
    embedding_config,
)
from .llm import build_review_model
from .observe import flush, job_trace, observation
from .pipeline_runtime import embedding_batches, run_queue
from .postgres import PostgresStore

logger = logging.getLogger(__name__)


def extract_candidates(repo, job, model, embeddings):
    """Checkpoint each block; no writes to formal points/facts/evidence in this phase."""
    repo.source(job)
    if not repo.progress(job, "splitting"):
        raise LeaseLost()
    config = job["config_json"]
    locators = repo.locators(job)
    if not locators:
        raise SourceUnavailable("资料没有可用证据块")
    blocks = repo.blocks(job, split_blocks(locators, config["block_chars"], config["block_pages"]))
    by_id = {x["locator_id"]: x for x in locators}
    for block in blocks:
        if block["extract_status"] == "succeeded":
            continue
        try:
            if not repo.progress(job, "extracting"):
                raise LeaseLost()
            checkpoint = repo.block_candidates(job, block)
            if checkpoint:
                result = Extraction(
                    points=[
                        dict(
                            name=c["name"],
                            description=c["description"],
                            importance_score=c["importance_score"],
                            facts=[
                                dict(
                                    fact_key=f["fact_key"],
                                    fact_text=f["fact_text"],
                                    evidence=[
                                        dict(locator_id=e["locator_id"], quote=e["quote"])
                                        for e in f["evidence"]
                                    ],
                                )
                                for f in c["facts_json"]["facts"]
                            ],
                        )
                        for c in checkpoint
                    ]
                )
            else:
                with observation("knowledge.extract", {"block_index": block["block_index"]}):
                    result = extract(model, block)
                # Persist model output before slower semantic verification and embedding.
                raw = []
                for point in result.points:
                    raw.append(
                        dict(
                            name=point.name.strip(),
                            normalized_name=normalize(point.name),
                            description=point.description.strip(),
                            importance_score=point.importance_score,
                            status="pending",
                            embedding=None,
                            facts_json={
                                "schema_version": 1,
                                "facts": [
                                    dict(
                                        fact_key=f.fact_key,
                                        fact_text=f.fact_text,
                                        normalized_text=normalize(f.fact_text),
                                        status="pending",
                                        evidence=[
                                            dict(
                                                material_version_id=job["material_version_id"],
                                                locator_id=e.locator_id,
                                                quote=e.quote,
                                                quote_verified=False,
                                                support_status="uncertain",
                                                verification_method="rule",
                                            )
                                            for e in f.evidence
                                        ],
                                    )
                                    for f in point.facts
                                ],
                            },
                        )
                    )
                repo.save_block(job, block, raw, complete=False)
                logger.info(
                    "知识库任务 %s 块 %s: 已保存 %s 个提取候选",
                    job["job_id"],
                    block["block_index"],
                    len(raw),
                )
                checkpoint = repo.block_candidates(job, block)
            if not repo.progress(job, "verifying"):
                raise LeaseLost()
            prepared = []
            for index, point in enumerate(result.points):
                old = checkpoint[index]
                verification_hash = digest(point.model_dump(mode="json"))
                saved = old["facts_json"]
                reusable = (
                    saved.get("verification_complete")
                    and saved.get("verification_input_hash") == verification_hash
                )
                if reusable or (
                    old.get("embedding") is not None and not saved.get("verification_input_hash")
                ):
                    facts = copy.deepcopy(saved)
                else:
                    with observation("knowledge.verify", {"candidate": index}):
                        facts = verify_facts(model, point, block, by_id, job["material_version_id"])
                facts.update(verification_complete=True, verification_input_hash=verification_hash)
                vector_hash = digest(
                    [point.name.strip(), point.description.strip(), embedding_config(config)]
                )
                vector = old.get("embedding")
                if saved.get("embedding_input_hash") not in (None, vector_hash):
                    vector = None
                facts["embedding_input_hash"] = vector_hash
                prepared.append(
                    dict(
                        candidate_index=index,
                        name=point.name.strip(),
                        normalized_name=normalize(point.name),
                        description=point.description.strip(),
                        importance_score=point.importance_score,
                        facts_json=facts,
                        embedding=vector,
                        status="pending"
                        if any(f["status"] == "supported" for f in facts["facts"])
                        else "rejected",
                    )
                )
            repo.save_block(job, block, prepared, complete=False)
            if not repo.progress(job, "embedding"):
                raise LeaseLost()
            missing = [c for c in prepared if c["embedding"] is None]
            for start, vectors in embedding_batches(
                embeddings,
                [f"{c['name']}\n{c['description']}" for c in missing],
                settings=getattr(repo.store, "knowledge_settings", None),
            ):
                batch = missing[start : start + len(vectors)]
                for candidate, vector in zip(batch, vectors, strict=True):
                    validate_embedding(vector, config["embedding_dimensions"])
                    candidate["embedding"] = vector
                repo.save_block(job, block, batch, complete=False)
            repo.save_block(job, block, prepared)
            logger.info(
                "知识库任务 %s 块 %s: 校验及向量化完成", job["job_id"], block["block_index"]
            )
        except LeaseLost:
            raise
        except Exception as exc:
            repo.fail_block(job, block, exc)
            raise
    return repo.candidates(job)


def process_knowledge_job(store, model, embeddings, job, settings=None):
    with job_trace("knowledge-worker", job) as metrics:
        with observation("knowledge.build", {"material_version_id": job["material_version_id"]}):
            result = _process_knowledge_job(store, model, embeddings, job, settings)
            metrics["status"] = "succeeded" if result else "failed"
            return result


def _process_knowledge_job(store, model, embeddings, job, settings=None):
    repo = KnowledgeRepository(store)
    if settings is not None:
        store.knowledge_settings = settings
    token = store.bind_user(str(job["user_id"]))
    stop = Event()

    def heartbeat():
        lease_store = PostgresStore(store.database_url)
        lease_store.bind_user(str(job["user_id"]))
        try:
            while not stop.wait(30):
                if not KnowledgeRepository(lease_store).progress(job):
                    return
        except Exception:
            logger.exception("知识库任务续约失败: %s", job["job_id"])
        finally:
            lease_store.close()

    thread = Thread(target=heartbeat, daemon=True, name="knowledge-job-heartbeat")
    thread.start()
    try:
        with store.account_file_access(str(job["user_id"])) as allowed:
            if not allowed:
                raise SourceUnavailable("账号已申请注销")
            if settings is not None and build_config(settings) != job["config_json"]:
                raise SourceUnavailable("构建配置已变化，请创建新版本任务")
            candidates = extract_candidates(repo, job, model, embeddings)
            if not repo.progress(job, "dedup_internal"):
                raise LeaseLost()
            config = job["config_json"]
            matcher = AuditedMatcher(model, repo, job)
            with observation("knowledge.dedup_internal"):
                candidates = dedup_internal(
                    matcher, candidates, config["top_k"], config["min_similarity"]
                )
            repo.save_internal(job, candidates)
            # Matching calls stay outside transactions. Concurrent publication invalidates
            # the snapshot and reruns matching against the newly committed knowledge.
            for _ in range(8):
                if not repo.progress(job, "matching"):
                    raise LeaseLost()
                fingerprint, points = repo.snapshot(job["course_id"])
                matcher.phase = "matching"
                for point in points:
                    validate_embedding(point["embedding"], config["embedding_dimensions"])
                    if point["data"].get("embedding_config") != embedding_config(config):
                        raise SourceUnavailable("已有知识库使用不同向量模型，需要重新构建")
                with observation("knowledge.matching"):
                    plan = plan_matches(
                        matcher, candidates, points, config["top_k"], config["min_similarity"]
                    )
                try:
                    with observation("knowledge.publish"):
                        repo.publish(job, fingerprint, plan)
                    return True
                except SnapshotChanged:
                    continue
            raise SnapshotChanged("课程并发更新频繁，稍后自动重试")
    except LeaseLost:
        logger.warning("知识库任务已由其他 worker 接管: %s", job["job_id"])
        return False
    except Exception as exc:
        logger.exception("知识库构建失败: %s", job["job_id"])
        repo.fail(job, exc, retry=not isinstance(exc, SourceUnavailable))
        return False
    finally:
        stop.set()
        thread.join(timeout=5)
        store.reset_user(token)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Course knowledge worker")
    parser.add_argument("--once", action="store_true", help="只领取并处理一个任务")
    args = parser.parse_args()
    settings = Settings()
    config = next(
        (c for c in settings.available_chat_models() if c.id == settings.knowledge_model_id), None
    )
    if config is None:
        raise ValueError("KNOWLEDGE_MODEL_ID 未匹配到已配置模型")
    if not settings.embedding_api_key.get_secret_value():
        raise ValueError("请配置 EMBEDDING_API_KEY")

    def execute(job):
        task_store = PostgresStore(settings.database_url.get_secret_value())
        embeddings = build_embeddings(settings)
        model = build_review_model(config, settings, knowledge_generation=True)
        try:
            process_knowledge_job(task_store, model, embeddings, job, settings)
        finally:
            task_store.close()

    store = PostgresStore(settings.database_url.get_secret_value())
    store.setup()
    try:
        run_queue(
            KnowledgeRepository(store).claim,
            execute,
            settings.knowledge_task_concurrency,
            once=args.once,
        )
    finally:
        store.close()
        flush()


if __name__ == "__main__":
    main()
