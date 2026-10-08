import pytest

from app.text import slugify


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("  Hello World ", "hello-world"),
        ("snake_case and--dashes", "snake-case-and-dashes"),
        ("C'est la vie!", "cest-la-vie"),
        ("", ""),
    ],
)
def test_slugify(title, expected):
    assert slugify(title) == expected
