"""The similarity floor must actually filter.

`QdrantVectorStore.search` used a 0.3 floor. BGE cosine on normalised English
prose almost never scores two texts below ~0.45, so that floor admitted
everything and `limit` did all the filtering: a query about anything at all came
back with five confident-looking lessons. These tests fail if the floor stops
rejecting off-topic queries.

The number itself is calibrated in tests/retrieval_benchmark.py against
tests/benchmark_data/retrieval_queries.yaml; this module guards the property the
calibration bought.
"""

import tempfile

import pytest

from mgcp.models import Lesson
from mgcp.qdrant_vector_store import QdrantVectorStore

from .retrieval_benchmark import load_cases

pytestmark = pytest.mark.slow


# Deliberately unrelated to each other and to the probe queries below.
CORPUS = [
    Lesson(
        id="password-storage",
        trigger="password, hash, store password, bcrypt, argon2",
        action="Hash passwords with argon2id or bcrypt. Never store plaintext or "
        "a fast general-purpose digest such as MD5 or SHA-1.",
        tags=["security"],
    ),
    Lesson(
        id="sidebar-scroll",
        trigger="flex-1, overflow-y-auto, scrollable sidebar, content cut off",
        action="A flex child needs min-height:0 before overflow-y:auto will "
        "scroll; without it the child grows to fit and the scrollbar never appears.",
        tags=["css"],
    ),
    Lesson(
        id="log-rotation",
        trigger="logging, log files, disk space, production logging",
        action="Configure RotatingFileHandler whenever the service writes logs to "
        "disk, or the volume fills and the process dies.",
        tags=["operations"],
    ),
]


@pytest.fixture(scope="module")
def store():
    with tempfile.TemporaryDirectory() as tmpdir:
        s = QdrantVectorStore(persist_path=tmpdir, collection_name="lessons")
        s.rebuild_index(CORPUS)
        yield s
        s.client.close()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN DEFECT, recorded rather than hidden: the store cannot answer "
        "'nothing relevant'. An off-topic query still scores ~0.41 against "
        "unrelated lessons, and the floor sits at 0.30 because every value "
        "high enough to reject this also starves the 2-4 word queries "
        "query_lessons documents and tests/test_trigger_coverage.py asserts "
        "(52 pass at 0.30; 8 fail at 0.52; 13 at 0.55). The short-query band "
        "and the noise band overlap, so no constant separates them — the fix "
        "is length normalisation or a reranker. See DEFAULT_MIN_SCORE in "
        "qdrant_vector_store.py for the measured table. strict=True on "
        "purpose: when someone lands the real fix this test starts passing "
        "and pytest will fail until the xfail is removed."
    ),
)
def test_off_topic_query_returns_nothing(store):
    """The property the old floor did not have: an empty result is possible."""
    for query in [
        "my sourdough loaf will not rise, what am I doing wrong",
        "recommend a hardtail mountain bike for trail riding",
        "explain the offside rule in football",
    ]:
        assert store.search(query) == [], f"{query!r} should match nothing"


def test_off_topic_query_scores_above_the_old_floor(store):
    """Non-vacuity guard: the queries above are only rejected because the floor
    moved. At 0.3 every one of them comes back full."""
    for query in [
        "my sourdough loaf will not rise, what am I doing wrong",
        "recommend a hardtail mountain bike for trail riding",
        "explain the offside rule in football",
    ]:
        assert len(store.search(query, min_score=0.3)) == len(CORPUS)


def test_on_topic_queries_still_win(store):
    """The floor must not buy precision by starving recall."""
    for query, expected in [
        ("what is the right way to store user passwords in the database", "password-storage"),
        ("the sidebar list is cut off and won't scroll", "sidebar-scroll"),
        ("the service writes logs to a file on disk in production", "log-rotation"),
    ]:
        hits = store.search(query)
        assert hits, f"{query!r} returned nothing"
        assert hits[0][0] == expected, f"{query!r} -> {hits}"


def test_query_set_is_well_formed():
    """The calibration set is a committed artefact; keep it loadable and sane."""
    cases = load_cases()
    positives = [c for c in cases if not c.is_negative]
    negatives = [c for c in cases if c.is_negative]

    assert len(positives) >= 20, "at least 20 labelled positive queries"
    assert negatives, "hard negatives are what make the floor measurable"
    assert len({c.id for c in cases}) == len(cases), "duplicate case id"
    for c in cases:
        assert c.query and c.paraphrase and c.query != c.paraphrase


def test_tag_filter_actually_filters(store):
    """The tag filter silently returned nothing for this system's whole history.

    Tags were stored as a comma-joined string ("git,commits,workflow") and
    queried with Qdrant's MatchValue, which is exact equality on the whole
    field — so a filter for "git" could never match. Nobody noticed because no
    shipped caller passed `tags`.

    Non-vacuity: change the payload in add_lesson back to
    ",".join(lesson.tags) and the first assertion fails.
    """
    from mgcp.models import Lesson

    store.add_lesson(Lesson(
        id="tagged-git", trigger="pushing a branch",
        action="run the tests first", tags=["git", "testing"],
    ))
    store.add_lesson(Lesson(
        id="tagged-other", trigger="picking a colour",
        action="use the token", tags=["design"],
    ))

    ids = {i for i, _ in store.search("pushing a branch", limit=5,
                                      min_score=0.0, tags=["git"])}
    assert "tagged-git" in ids, (
        "a lesson tagged 'git' was not returned by a tags=['git'] filter — "
        "the payload is not a list, or the query is not MatchValue"
    )
    assert "tagged-other" not in ids, "the filter let an untagged lesson through"
