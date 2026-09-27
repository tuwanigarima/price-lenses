from types import SimpleNamespace

from tools.policy_retrieval import HybridPolicyRetriever, build_policy_index
from tools.policy_types import policy_keyword_query


class FakeOpenAIEmbedder:
    provider = "openai"
    model = "text-embedding-3-small"
    dimensions = 1536

    def embed(self, texts):
        return [[float(index == 0) for index in range(self.dimensions)] for _ in texts]


class IndexDatabase:
    def __init__(self):
        self.upserted = []

    def active_policy_chunks(self, **_kwargs):
        return [
            {
                "chunk_id": "11111111-1111-1111-1111-111111111111",
                "document_version_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "content": (
                    "Return rules for a defective phone require the original packaging, "
                    "purchase invoice, accessories, and documented eligibility checks."
                ),
                "content_sha256": "a" * 64,
            },
            {
                "chunk_id": "22222222-2222-2222-2222-222222222222",
                "document_version_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "content": (
                    "Cancellation rules permit an electronics order to be cancelled "
                    "before dispatch, subject to the documented retailer conditions."
                ),
                "content_sha256": "b" * 64,
            },
        ]

    def policy_embedding_hashes(self, _model, _ids):
        return {"11111111-1111-1111-1111-111111111111": "a" * 64}

    def upsert_policy_embeddings(self, rows, **_kwargs):
        self.upserted.extend(rows)
        return len(rows)

    def policy_embedding_count(self, _model):
        return 2


def test_pgvector_index_only_embeds_missing_or_changed_chunks():
    database = IndexDatabase()
    settings = SimpleNamespace()

    result = build_policy_index(
        database, settings, embedder=FakeOpenAIEmbedder(), batch_size=1
    )

    assert result["backend"] == "pgvector"
    assert result["chunk_count"] == 2
    assert result["embedded_count"] == 1
    assert result["unchanged_count"] == 1
    assert [str(row["chunk_id"]) for row in database.upserted] == [
        "22222222-2222-2222-2222-222222222222"
    ]
    assert len(database.upserted[0]["embedding"]) == 1536


def test_pgvector_index_rejects_official_domain_error_pages():
    database = IndexDatabase()
    database.active_policy_chunks = lambda **_kwargs: [{
        "chunk_id": "33333333-3333-3333-3333-333333333333",
        "document_version_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "content": (
            "We are sorry. We cannot find the page you were trying to reach. "
            "Please visit customer service for more information."
        ),
        "content_sha256": "c" * 64,
    }]

    try:
        build_policy_index(database, SimpleNamespace(), embedder=FakeOpenAIEmbedder())
    except ValueError as exc:
        assert "No approved active policy chunks" in str(exc)
    else:
        raise AssertionError("An error page was accepted for pgvector indexing")


def test_pgvector_index_rejects_all_chunks_from_an_error_document():
    database = IndexDatabase()
    database.active_policy_chunks = lambda **_kwargs: [{
        "chunk_id": "44444444-4444-4444-4444-444444444444",
        "document_version_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "content": (
            "Customer service provides account support, order information, and other "
            "general assistance through the help centre navigation options."
        ),
        "document_content": (
            "We are sorry. We cannot find the page you were trying to reach. "
            "Customer service provides account support and general assistance."
        ),
        "content_sha256": "d" * 64,
    }]

    try:
        build_policy_index(database, SimpleNamespace(), embedder=FakeOpenAIEmbedder())
    except ValueError as exc:
        assert "No approved active policy chunks" in str(exc)
    else:
        raise AssertionError("A chunk from an error document was accepted")


class RetrievalDatabase:
    def __init__(self):
        self.keyword_question = None

    def keyword_policy_search(self, *_args, **_kwargs):
        self.keyword_question = _args[0]
        return [
            {"chunk_id": "11111111-1111-1111-1111-111111111111"},
            {"chunk_id": "22222222-2222-2222-2222-222222222222"},
        ]

    def semantic_policy_search(self, *_args, **_kwargs):
        return [
            {"chunk_id": "22222222-2222-2222-2222-222222222222"},
            {"chunk_id": "11111111-1111-1111-1111-111111111111"},
        ]

    def policy_chunks_by_ids(self, ids):
        return {
            chunk_id: {
                "chunk_id": chunk_id,
                "document_version_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "retailer": "Flipkart",
                "policy_type": "RETURN",
                "content": (
                    "Defective electronics follow the documented return rules when the "
                    "customer retains the invoice, packaging, and supplied accessories."
                ),
                "source_url": "https://www.flipkart.com/pages/returnpolicy",
                "product_category": "electronics",
                "is_active": True,
            }
            for chunk_id in ids
        }


def test_hybrid_retriever_fuses_keyword_and_pgvector_results():
    database = RetrievalDatabase()
    retriever = HybridPolicyRetriever(
        database, SimpleNamespace(), embedder=FakeOpenAIEmbedder()
    )

    hits = retriever.search(
        "Can I return a defective phone?",
        retailers=["Flipkart"],
        policy_types=["RETURN"],
    )

    assert len(hits) == 2
    assert all(hit.retrieval_sources == ("keyword", "semantic") for hit in hits)
    assert database.keyword_question == "return OR refund"


def test_policy_keyword_query_uses_or_across_requested_topics():
    query = policy_keyword_query(("RETURN", "CANCELLATION", "WARRANTY"))

    assert "return OR refund" in query
    assert "cancellation OR cancel" in query
    assert "warranty OR guarantee" in query
