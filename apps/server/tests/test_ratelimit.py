from kashi_server.ratelimit import TokenBuckets


def test_bucket_consumes_then_blocks():
    b = TokenBuckets()
    for _ in range(3):
        allowed, _ = b.allow("k", capacity=3, refill_per_s=1.0, now=100.0)
        assert allowed
    allowed, retry_after = b.allow("k", capacity=3, refill_per_s=1.0, now=100.0)
    assert not allowed
    assert 0.9 <= retry_after <= 1.1


def test_bucket_refills_over_time():
    b = TokenBuckets()
    assert b.allow("k", capacity=1, refill_per_s=0.5, now=0.0)[0]
    assert not b.allow("k", capacity=1, refill_per_s=0.5, now=0.1)[0]
    assert b.allow("k", capacity=1, refill_per_s=0.5, now=2.5)[0]  # 2.4s * 0.5 > 1


def test_bucket_caps_at_capacity():
    b = TokenBuckets()
    b.allow("k", capacity=2, refill_per_s=10.0, now=0.0)
    # Long idle must not bank more than `capacity` tokens.
    assert b.allow("k", capacity=2, refill_per_s=10.0, now=1000.0)[0]
    assert b.allow("k", capacity=2, refill_per_s=10.0, now=1000.0)[0]
    assert not b.allow("k", capacity=2, refill_per_s=10.0, now=1000.0)[0]


def test_buckets_are_isolated_per_key():
    b = TokenBuckets()
    assert b.allow("a", capacity=1, refill_per_s=0.0, now=0.0)[0]
    assert b.allow("b", capacity=1, refill_per_s=0.0, now=0.0)[0]
    assert not b.allow("a", capacity=1, refill_per_s=0.0, now=0.0)[0]
