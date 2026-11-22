import pytest

from salesbot import moderation as m


@pytest.mark.parametrize("text", [
    "berapa harga?", "ada stok lagi?", "boleh COD tak?", "how much is this?", "这个多少钱?",
    "Assalamualaikum kak", "spesial sangat", "nice class", "size M ada?", "wah cantik giler",
    "can I get two?", "I passed the class", "babies love this",
])
def test_allows_normal_shopping_comments(text):
    assert m.check(text) is None


@pytest.mark.parametrize("text,reason", [
    ("bodoh punya live", "profanity"),
    ("B0D0H", "profanity"),
    ("f*u*c*k this", "profanity"),
    ("pukimak", "profanity"),
    ("knn la", "profanity"),
    ("visit www.cheap.xyz now", "link"),
    ("https://bit.ly/abc", "link"),
    ("call me 012-345 6789", "personal_data"),
    ("email me a@b.com", "personal_data"),
    ("follow me for free followers", "spam"),
    ("whatsapp saya", "spam"),
    ("aaaaaaaaaaaaaaaa", "spam"),
    ("", "empty"),
])
def test_blocks(text, reason):
    assert m.check(text) == reason


def test_safe_username():
    assert m.safe_username("kelvin.lim") == "kelvin.lim"
    assert m.safe_username("babi_123") == "friend"
    assert m.safe_username("www.spam.com") == "friend"
    assert m.safe_username("") == "friend"
    assert len(m.safe_username("abcdefghij" * 10)) == 24


def test_clean_comment():
    assert m.clean_comment("  hi \n\n there  ") == "hi there"
    assert len(m.clean_comment("a" * 500)) == 200
