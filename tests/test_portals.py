import doorman_profile as P
import lp_scan
import oi_scan
from conftest import FIXTURES

SOLO = P.load(FIXTURES / "solo-studio.yaml")
COUPLE = P.load(FIXTURES / "couple-2br.yaml")

HTML = """
<a href="/unit/nyc/1-sample-st-4b"><img alt="1 Sample St #4B, Queens, NY 11102"></a>
<div>Astoria · Studio</div><span>$2,450</span><span>Rent-stabilized</span>
<a href="/unit/nyc/2-test-ave-2"><img alt="2 Test Ave #2, Brooklyn, NY 11215"></a>
<div>Park Slope · 2 beds</div><span>$5,100</span>
<a href="/unit/nyc/no-alt">nothing useful</a>
"""


def row(**kw):
    base = {"zip": "11102", "price": 2500, "beds": 0}
    return {**base, **kw}


def test_fits_solo():
    assert oi_scan.fits(row(), SOLO)
    assert oi_scan.fits(row(beds=None), SOLO)
    assert not oi_scan.fits(row(zip="11215"), SOLO)
    assert not oi_scan.fits(row(price=2901), SOLO)
    assert not oi_scan.fits(row(price=None), SOLO)
    assert not oi_scan.fits(row(beds=1), SOLO)


def test_fits_couple():
    assert oi_scan.fits(row(zip="11215", price=5600, beds=2), COUPLE)
    assert not oi_scan.fits(row(zip="10024", beds=2), COUPLE)
    assert not oi_scan.fits(row(zip="11215", price=5601, beds=2), COUPLE)
    assert not oi_scan.fits(row(zip="11215", beds=0), COUPLE)


def test_parse_sample():
    a, b = oi_scan.parse(HTML)
    assert (a["address"], a["unit"], a["borough"], a["zip"]) == ("1 Sample St", "4B", "Queens", "11102")
    assert (a["price"], a["beds"], a["neighborhood"], a["stabilized"]) == (2450, 0, "Astoria", True)
    assert a["url"] == "https://www.openigloo.com/unit/nyc/1-sample-st-4b"
    assert (b["price"], b["beds"], b["stabilized"]) == (5100, 2, False)


def test_listing_label_only_when_stabilized():
    a, b = oi_scan.parse(HTML)
    assert oi_scan.listing(a)["src_label"] == "rent-stabilized"
    assert oi_scan.listing(a)["source"] == "openigloo"
    assert "src_label" not in oi_scan.listing(b)


def lp(title, category, price, per="month", desc=""):
    return {"title": title, "category": category, "price": price, "per": per, "desc": desc,
            "location": "Astoria", "url": "https://www.listingsproject.com/listings/" + title.replace(" ", "-")}


def test_cut_buckets():
    items = [
        lp("Sunny studio", "Apartments for Rent", 2500),
        lp("Takeover studio", "Apartments for Lease Takeover", 2800),
        lp("Pricey studio", "Apartments for Rent", 3500),
        lp("Big 2 bedroom", "Apartments for Rent", 2500),
        lp("Studio sublet", "Sublets", 2000),
        lp("Unpriced studio", "Apartments for Rent", None, per=""),
    ]
    got = [l["title"] for l in lp_scan.cut_buckets(items, SOLO)]
    assert got == ["Sunny studio", "Takeover studio", "Unpriced studio"]
    two = [lp("Nice 2 bedroom", "Apartments for Rent", 5000), lp("Sunny studio", "Apartments for Rent", 2500)]
    assert [l["title"] for l in lp_scan.cut_buckets(two, COUPLE)] == ["Nice 2 bedroom"]


def test_lp_listing_row():
    l = lp("Sunny studio", "Apartments for Rent", 2500)
    (kept,) = lp_scan.cut_buckets([l], SOLO)
    assert lp_scan.listing(kept) == {"source": "listingsproject", "address": "Sunny studio",
                                     "neighborhood": "Astoria", "price": 2500, "url": l["url"]}
