def subtotal(prices: list[float]) -> float:
    return round(sum(prices), 2)


def apply_discount(amount: float, percent: float) -> float:
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return round(amount * (1 - percent / 100), 2)
