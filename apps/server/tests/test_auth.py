from kashi_server.auth import generate_key, hash_key, looks_like_key


def test_generate_key_shape_and_uniqueness():
    a, b = generate_key(), generate_key()
    assert a != b
    assert looks_like_key(a) and looks_like_key(b)
    assert len(hash_key(a)) == 64


def test_looks_like_key_rejects_garbage():
    assert not looks_like_key("")
    assert not looks_like_key("ksh_short")
    assert not looks_like_key("ksh_" + "Z" * 32)  # non-hex
    assert not looks_like_key("bearer_" + "a" * 32)
