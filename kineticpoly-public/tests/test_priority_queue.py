from src.core.priority_queue import PriorityQueue
from src.core.signal import TradingSignal


def _signal(edge, hours_to_start=24.0, token="t"):
    return TradingSignal(
        token_id=token, market_id="m", side="BUY",
        price=0.5, stake=10.0, edge=edge, fair_price=0.55,
        strategy="TEST", timestamp=0.0,
        hours_to_start=hours_to_start,
    )


def test_higher_edge_pops_first():
    q = PriorityQueue()
    q.push(_signal(0.01, token="low"))
    q.push(_signal(0.05, token="high"))
    q.push(_signal(0.03, token="mid"))
    assert q.pop().token_id == "high"
    assert q.pop().token_id == "mid"
    assert q.pop().token_id == "low"


def test_equal_edge_falls_back_to_fifo():
    q = PriorityQueue()
    q.push(_signal(0.02, token="first"))
    q.push(_signal(0.02, token="second"))
    q.push(_signal(0.02, token="third"))
    assert q.pop().token_id == "first"
    assert q.pop().token_id == "second"
    assert q.pop().token_id == "third"


def test_proximity_breaks_edge_ties():
    q = PriorityQueue()
    q.push(_signal(0.02, hours_to_start=40.0, token="far"))
    q.push(_signal(0.02, hours_to_start=1.0, token="soon"))
    assert q.pop().token_id == "soon"


def test_max_size_evicts_lowest_priority():
    q = PriorityQueue(max_size=2)
    q.push(_signal(0.01, token="a"))
    q.push(_signal(0.02, token="b"))
    q.push(_signal(0.03, token="c"))
    assert q.size() == 2
    assert q.pop().token_id == "c"
    assert q.pop().token_id == "b"
    assert q.pop() is None


def test_peek_does_not_remove():
    q = PriorityQueue()
    q.push(_signal(0.04, token="x"))
    assert q.peek().token_id == "x"
    assert q.size() == 1
