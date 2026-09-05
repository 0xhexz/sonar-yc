from app.detect import classify, signal_identities
from app.models import CompanySignal, Founder, EARLY, CONFIRMED, SPEEDRUN


def test_directory_sources_are_always_confirmed():
    yc = CompanySignal(source="yc", name="Acme", slug="acme")
    sp = CompanySignal(source="speedrun", name="Bead", slug="bead")
    assert classify(yc, set(), set()) == CONFIRMED
    assert classify(sp, set(), set()) == SPEEDRUN


def test_social_matching_directory_is_confirmed():
    s = CompanySignal(source="x", name="Acme", slug="acme", founders=[Founder(handle="jane")])
    assert classify(s, {"acme"}, set()) == CONFIRMED
    assert classify(s, set(), {"acme"}) == SPEEDRUN


def test_social_not_in_directory_is_early():
    s = CompanySignal(source="x", name="Acme", slug="acme", founders=[Founder(handle="jane")])
    assert classify(s, set(), set()) == EARLY


def test_social_match_by_founder_handle():
    s = CompanySignal(source="linkedin", name="", founders=[Founder(handle="beknabdik")])
    # the YC directory knows this founder (announced) -> CONFIRMED
    assert classify(s, {"beknabdik"}, set()) == CONFIRMED
    # unknown founder -> EARLY
    assert classify(s, {"other"}, set()) == EARLY


def test_signal_identities():
    s = CompanySignal(source="x", name="Acme AI", slug="acme-ai", founders=[Founder(handle="j")])
    ids = signal_identities(s)
    assert "acme-ai" in ids
    assert "acme-ai" in ids  # slugify(name) equals slug here
    assert "j" in ids
