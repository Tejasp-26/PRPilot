from app.orders import get_order, order_totals


def test_get_order(requests_mock):
    requests_mock.get("https://api.example.com/orders/1", json={"id": 1})
    assert get_order(1)["id"] == 1


def test_order_totals(requests_mock):
    requests_mock.get("https://api.example.com/customers/7/orders", json=[{"total": 2.5}])
    assert order_totals([7]) == {7: 2.5}
