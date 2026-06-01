from app.ingestion.clients import body_indicates_x_timeline_error


def test_body_indicates_x_timeline_error() -> None:
    assert body_indicates_x_timeline_error("Something went wrong. Try reloading.")
    assert not body_indicates_x_timeline_error("CKCapitalxx @CKCapitalxx")
