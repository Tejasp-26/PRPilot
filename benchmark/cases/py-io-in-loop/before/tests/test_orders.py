from app.orders import get_order


def test_get_order(requests_mock):
    requests_mock.get("https://api.example.com/orders/1", json={"id": 1})
    assert get_order(1)["id"] == 1
