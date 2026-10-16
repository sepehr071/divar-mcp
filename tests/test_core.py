import pytest

from divar_mcp import core
from divar_mcp.core import dist, fold, make_keep, price_flags, row_prices, sane, short_toman, to_int


def test_fold_unifies_persian_variants_digits_and_zwnj():
    assert fold("كيا  سراتو") == fold("کیا سراتو")
    assert fold("۱۴۰۰") == "1400"
    assert fold("سعادت\u200cآباد") == fold("سعادت اباد")


def test_to_int_reads_persian_digit_prices():
    assert to_int("۳,۵۰۰,۰۰۰,۰۰۰ تومان") == 3_500_000_000
    assert to_int("‏۱۰۰,۰۰۰,۰۰۰ تومان") == 100_000_000
    assert to_int("توافقی") is None


@pytest.mark.parametrize(
    ("text", "value"),
    [("۱٫۵ میلیارد", 1_500_000_000), ("۳۰ میلیون", 30_000_000), ("۵۰۰ هزار", 500_000), ("رایگان", 0), ("توافقی", None)],
)
def test_short_toman_reads_map_card_prices(text, value):
    assert short_toman(text) == value


def test_price_flags_catch_placeholders_and_absurd_values():
    assert price_flags(1111111) == ["placeholder"]
    assert price_flags(12345678) == ["placeholder"]
    assert price_flags(1000) == ["tiny"]
    assert price_flags(2_225_000_000_000_000) == ["absurd"]
    assert price_flags(950_000_000) == [] and price_flags(None) == [] and price_flags(0) == []


def test_row_prices_sale_rent_full_deposit_and_negotiable():
    assert row_prices({"middle_description_text": "۹۹۸,۰۰۰,۰۰۰ تومان", "top_description_text": "۱۳۸,۰۰۰ کیلومتر"}) == {
        "price": 998_000_000,
        "info": "۱۳۸,۰۰۰ کیلومتر",
    }
    rent = row_prices(
        {"middle_description_text": "اجاره: ۲۵,۰۰۰,۰۰۰ تومان", "top_description_text": "ودیعه: ۱۰۰,۰۰۰,۰۰۰ تومان"}
    )
    assert (rent["deposit"], rent["rent"], rent["info"]) == (100_000_000, 25_000_000, "")
    full = row_prices({"middle_description_text": "رهن کامل", "top_description_text": "ودیعه: ۹,۰۰۰,۰۰۰,۰۰۰ تومان"})
    assert (full["deposit"], full["rent"], full["full_deposit"]) == (9_000_000_000, 0, True)
    assert row_prices({"middle_description_text": "توافقی"})["negotiable"] is True
    zero = row_prices({"middle_description_text": "۰ تومان"})
    assert "price" not in zero and zero["negotiable"] is True
    tiny_rent = row_prices(
        {"middle_description_text": "اجاره: ۱۰,۰۰۰ تومان", "top_description_text": "ودیعه: ۸۰۰,۰۰۰,۰۰۰ تومان"}
    )
    assert tiny_rent["suspect_price"] == ["tiny_rent"]


def test_dist_percentiles_and_sane_filter():
    d = dist([100, 200, 300, 400, 500])
    assert (d["n"], d["min"], d["median"], d["p25"], d["p75"], d["max"], d["mean"]) == (5, 100, 300, 200, 400, 500, 300)
    assert dist([]) is None
    assert sane([5, 1000, 1100, 1200, 1300]) == [1000, 1100, 1200, 1300]


def test_make_keep_age_keywords_and_exclusion():
    keep = make_keep(2, ["پژو"], ["تصادفی"])
    assert keep({"title": "پژو ۲۰۶", "age_h": 1.0})
    assert not keep({"title": "پژو ۲۰۶", "age_h": 3.0})
    assert not keep({"title": "پژو تصادفی", "age_h": 1.0})
    assert not keep({"title": "پراید", "age_h": 1.0})
    assert make_keep(None, ["كيا"])({"title": "کیا سراتو"})  # Arabic letters in the query still match


def test_parse_time_trims_nanoseconds():
    t = core.parse_time("2026-10-16T01:57:20.555176473Z")
    assert (t.hour, t.minute, t.second) == (1, 57, 20) and t.tzinfo is not None
    assert core.parse_time("garbage") is None
