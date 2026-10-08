import requests

API = "https://api.example.com"


def get_order(order_id: int) -> dict:
    resp = requests.get(f"{API}/orders/{order_id}", timeout=5)
    resp.raise_for_status()
    return resp.json()


def order_totals(customer_ids: list[int]) -> dict[int, float]:
    totals = {}
    for customer_id in customer_ids:
        resp = requests.get(f"{API}/customers/{customer_id}/orders", timeout=5)
        resp.raise_for_status()
        totals[customer_id] = sum(o["total"] for o in resp.json())
    return totals
