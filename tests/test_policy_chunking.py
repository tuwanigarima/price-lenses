from tools.policy_chunking import PolicyDocument, chunk_document


def test_faq_children_reference_a_real_parent_chunk():
    document = PolicyDocument(
        document_version_id="11111111-1111-1111-1111-111111111111",
        retailer="Flipkart",
        policy_type="FAQ",
        title="Returns",
        content=(
            "# Mobile returns\n"
            "Can I return a defective phone?\nA defective phone is replacement eligible.\n"
            "Can I cancel an order?\nCancellation depends on shipment status."
        ),
        product_category="electronics",
    )
    chunks = chunk_document(document)
    parents = [chunk for chunk in chunks if chunk.chunk_type == "PARENT"]
    children = [chunk for chunk in chunks if chunk.chunk_type == "FAQ"]
    assert len(parents) == 1
    assert len(children) == 2
    assert {child.parent_chunk_id for child in children} == {parents[0].chunk_id}
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
