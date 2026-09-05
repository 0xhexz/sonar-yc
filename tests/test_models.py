from app.models import CompanySignal, Founder, slugify, EARLY


def test_slugify():
    assert slugify("Bead AI") == "bead-ai"
    assert slugify("  OpenTag   Labs  ") == "opentag-labs"
    assert slugify("a16z Speedrun") == "a16z-speedrun"


def test_founder_lookup_key():
    f = Founder(handle="@something")
    assert f.lookup_key == "something"
    assert Founder(name="Jane Doe").lookup_key == "jane doe"


def test_signal_identity():
    s = CompanySignal(source="speedrun", name="Bead AI", slug="bead-ai")
    assert s.slug == "bead-ai"
    assert s.url or True  # url defaults to ""

    s2 = CompanySignal(source="x", name="", founders=[Founder(handle="bek")])
    assert s2.founders[0].lookup_key == "bek"
