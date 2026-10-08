from app.pricing import subtotal


def test_subtotal():
    assert subtotal([1.10, 2.20]) == 3.3
