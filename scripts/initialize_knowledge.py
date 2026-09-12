"""Import the packaged, precomputed knowledge release without a network call."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from wellio.errors import BackendError
from wellio.knowledge import DIMENSIONS, MODEL, RECIPE, EmbeddingConfig, KnowledgeStore


def main():
    store = KnowledgeStore(os.environ["DATABASE_URL"])
    store.initialize()
    try:
        active = store.active()
    except BackendError as error:
        if error.code != "KNOWLEDGE_NOT_READY":
            raise
    else:
        print(f"知识库已就绪，保留现有版本：{active['id']}", flush=True)
        return

    release = "demo-6c1a7913caf33172c82b3482"
    cache = ROOT / "backend/.data/knowledge/nutrition-v1/releases" / release
    bundle = json.loads((cache / "bundle.json").read_text(encoding="utf-8"))
    cached = json.loads((cache / "vectors.json").read_text(encoding="utf-8"))
    if (bundle["releaseId"] != release or cached["releaseId"] != release
            or bundle["model"] != MODEL or bundle["dimensions"] != DIMENSIONS
            or bundle["recipe"] != RECIPE):
        raise ValueError("随包知识缓存的版本、模型或维度不匹配")
    vectors = [cached["vectors"][chunk["id"]] for chunk in bundle["chunks"]]
    store.import_release(release, EmbeddingConfig(), bundle["documents"], bundle["chunks"], vectors)
    store.activate(release)
    print(f"离线知识缓存已导入：{len(bundle['documents'])} 篇资料，{len(vectors)} 个片段", flush=True)


if __name__ == "__main__":
    main()
