from src.core.async_fetcher import AsyncMarketDataFetcher


def test_level_price_handles_dict_and_list_forms():
    assert AsyncMarketDataFetcher._level_price({"price": 0.51}) == 0.51
    assert AsyncMarketDataFetcher._level_price([0.51, 100]) == 0.51
    assert AsyncMarketDataFetcher._level_price("garbage") is None
    assert AsyncMarketDataFetcher._level_price(None) is None


def test_sort_book_puts_best_at_index_zero():
    # Polymarket returns best-at-end on both sides.
    raw = {
        "bids": [{"price": 0.48}, {"price": 0.49}, {"price": 0.51}],
        "asks": [{"price": 0.55}, {"price": 0.54}, {"price": 0.52}],
    }
    out = AsyncMarketDataFetcher._sort_book(raw)
    assert out["bids"][0]["price"] == 0.51
    assert out["bids"][-1]["price"] == 0.48
    assert out["asks"][0]["price"] == 0.52
    assert out["asks"][-1]["price"] == 0.55


def test_sort_book_accepts_list_form():
    raw = {
        "bids": [[0.48, 10], [0.51, 20], [0.49, 5]],
        "asks": [[0.55, 10], [0.52, 20], [0.54, 5]],
    }
    out = AsyncMarketDataFetcher._sort_book(raw)
    assert out["bids"][0][0] == 0.51
    assert out["asks"][0][0] == 0.52


def test_sort_book_passes_through_non_dict_input():
    assert AsyncMarketDataFetcher._sort_book(None) is None
    assert AsyncMarketDataFetcher._sort_book("not a book") == "not a book"


def test_sort_book_tolerates_missing_sides():
    out = AsyncMarketDataFetcher._sort_book({"bids": [{"price": 0.5}]})
    assert out["bids"][0]["price"] == 0.5
    assert out["asks"] == []
