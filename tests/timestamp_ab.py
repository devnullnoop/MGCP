"""Does putting the written date into a lesson's embedded text help retrieval?

The LoCoMo measurement found that adopting LoCoMo's timestamped record format
raised search accuracy there by about 3 points. This checks whether that carries
over to MGCP's own notes. It does not. The result is recorded in
docs/locomo-retrieval-eval.md under "Timestamps do not transfer".

Each condition runs in its own process against its own fresh copy of the store,
because both build an index in the same place and embedded Qdrant allows one
client per directory.

    for c in stock dated; do
      SB=$(mktemp -d); cp ~/.mgcp/lessons.db "$SB/lessons.db"
      MGCP_DATA_DIR=$SB python tests/timestamp_ab.py $c /tmp/ts-$c.json
    done
"""
import asyncio
import json
import sys
from pathlib import Path

from mgcp.persistence import LessonStore
from mgcp.qdrant_vector_store import QdrantVectorStore, get_default_qdrant_path

CONDITION, OUT = sys.argv[1], sys.argv[2]

_stock = QdrantVectorStore._lesson_to_text

def dated(self, lesson):
    """Stock text plus the date the lesson was written, in words."""
    base = _stock(self, lesson)
    when = lesson.created_at.strftime("%-d %B %Y")
    return f"{base}\nRecorded on {when}."

async def main():
    if CONDITION == "dated":
        QdrantVectorStore._lesson_to_text = dated

    store = LessonStore()
    lessons = await store.get_all_lessons()
    vs = QdrantVectorStore(persist_path=get_default_qdrant_path())
    vs.rebuild_index(lessons)
    sample = QdrantVectorStore._lesson_to_text(vs, lessons[0])
    print(f"  {CONDITION}: indexed {len(lessons)} lessons")
    print(f"  last line of the embedded text: {sample.splitlines()[-1][:70]!r}")

    # Embedded Qdrant allows one client per directory, and the scorer opens its
    # own, so this one has to let go first.
    vs.client.close()

    sys.path.insert(0, "tests")
    from retrieval_benchmark import collect_raw, load_cases, score
    cases = load_cases()
    out = {}
    for variant in ("query", "paraphrase"):
        raw = collect_raw(get_default_qdrant_path(), cases, variant)
        out[variant] = score(cases, raw, 0.30)
    Path(OUT).write_text(json.dumps({"condition": CONDITION, "scores": out}, indent=1))

asyncio.run(main())
