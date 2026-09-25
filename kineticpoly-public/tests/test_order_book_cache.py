from src.core.order_book_cache import OrderBookCache


def test_order_book_cache_keeps_dict_levels():
    cache = OrderBookCache(max_age_seconds=30.0)
    cache.update({
        "t1": {
            "bids": [{"price": 0.51, "size": 2.0}],
            "asks": [{"price": 0.53, "size": 3.0}],
        }
    })

    book = cache.get_order_book("t1")
    assert book is not None
    assert book["bids"][0]["price"] == 0.51
    assert book["bids"][0]["size"] == 2.0
    assert book["asks"][0]["price"] == 0.53
    assert cache.get_midpoint("t1") == 0.52


def test_order_book_cache_accepts_list_style_inputs():
    cache = OrderBookCache(max_age_seconds=30.0)
    cache.update_snapshot("t2", bids=[[0.50, 1.0]], asks=[[0.60, 2.0]])

    book = cache.get_order_book("t2")
    assert book is not None
    assert book["bids"][0]["price"] == 0.50
    assert book["bids"][0]["size"] == 1.0
    assert book["asks"][0]["price"] == 0.60
    assert book["asks"][0]["size"] == 2.0
