import requests

API = "https://api.example.com"


def get_order(order_id: int) -> dict:
    resp = requests.get(f"{API}/orders/{order_id}", timeout=5)
    resp.raise_for_status()
    return resp.json()
