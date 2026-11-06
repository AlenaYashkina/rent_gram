"""LLM output normalization tests: no live API or Telegram session required."""
from llm import _clean_currency, _ensure_analysis_schema


def norm(**data):
    data.setdefault("llm", {"model": "test"})
    return _ensure_analysis_schema(data)


def test_currency_symbols_and_amd():
    for raw, expected in [
        ("$", "USD"), ("₾", "GEL"), ("лари", "GEL"),
        ("₽", "RUB"), ("֏", "AMD"), ("դրամ", None),
        ("€", "EUR"), ("£", "GBP"), ("₸", "KZT"),
    ]:
        assert _clean_currency(raw) == expected
    assert _clean_currency("???") is None
    assert _clean_currency("USDT") is None


def test_rejects_unverified_llm_fx_and_preserves_original():
    a = norm(is_rental_offer=True, long_term=True,
             price={"value_raw": 70000, "currency_raw": "RUB",
                    "usd": 70000, "fx_rate": 1, "fx_source": "invented", "period": "month"})
    assert a["price"]["currency_raw"] == "RUB"
    assert a["price"]["value_raw"] == 70000
    assert a["price"]["usd"] is None
    assert a["price"]["fx_source"] is None


def test_usd_original_has_valid_parity():
    a = norm(is_rental_offer=True,
             price={"value_raw": 600, "currency_raw": "$", "usd": 42000000})
    assert a["price"]["usd"] == 600
    assert a["price"]["fx_rate"] == 1.0


def test_separate_daily_rent_monthly_rent_sale_and_deposit():
    daily = norm(is_rental_offer=True, long_term=True,
                 price={"value_raw": 70, "currency_raw": "USD", "period": "day"})
    assert daily["is_short_term"] is True
    assert daily["long_term"] is False
    monthly = norm(is_rental_offer=True, is_short_term=False,
                   price={"value_raw": 600, "currency_raw": "USD", "period": "month",
                          "deposit_value_raw": 600, "deposit_currency_raw": "USD"})
    assert monthly["long_term"] is True
    assert monthly["price"]["deposit_value_raw"] == 600
    sale = norm(is_rental_offer=False,
                price={"value_raw": 42000000, "currency_raw": "RUB", "period": "total"})
    assert sale["is_rental_offer"] is False
    assert sale["price"]["period"] == "total"


def test_explicit_false_is_not_coerced_from_truthy_fallback():
    a = norm(is_rental_offer=True, is_short_term=False, short_term=True)
    assert a["is_short_term"] is False


def test_missing_currency_and_price_stay_unknown():
    a = norm(is_rental_offer=True, price={"value_raw": 200, "currency_raw": None, "usd": 200})
    assert a["price"]["usd"] is None
    assert a["price"]["currency_raw"] is None
    assert norm(is_rental_offer=True, price={})["price"]["value_raw"] is None
