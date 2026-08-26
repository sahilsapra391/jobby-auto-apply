"""A token is kept only when the board is that company, in that market.

`probe_tenants` answers "does this token have a live board". That is not enough
to put it in the registry: a token is a short common word often enough that
somebody else holds it, and a wrong token is worse than a missing one, because
it is a confident wrong answer that every later sweep repeats, filing another
company's jobs under a name the user recognises.

The two fixture cases below are real. Both were confirmed as Indian coverage by
a first version of this gate and are now the regression.
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import confirm_tenants  # noqa: E402


class MarketMatchTests(unittest.TestCase):
    """Whole words, not substrings.

    The first version asked `"india" in text`. Both fixtures below are American
    companies operating in **Indiana**, and both were reported as India finds --
    a false positive that reads exactly like a real one, and would have seeded
    two US boards as Indian coverage. The fix is word-boundary matching against
    the country name, the cities, and nothing else; it is not geo-coding, which
    the board text does not support.
    """

    def test_dungarvin_in_indiana_is_not_india(self):
        body = (b"Careers at Dungarvin. Open roles in Indianapolis, Indiana; "
                b"Minnesota; and Oregon.")
        identity, in_market, _hits, _strong = confirm_tenants.confirm(
            body, "Dungarvin", "IN")
        self.assertTrue(identity)
        self.assertFalse(in_market, "Indiana is not India")

    def test_thompson_thrift_in_indiana_is_not_india(self):
        body = b"Thompson Thrift - Terre Haute, Indiana and Denver, Colorado."
        _identity, in_market, _hits, _strong = confirm_tenants.confirm(
            body, "Thompson Thrift", "IN")
        self.assertFalse(in_market)

    def test_a_board_with_an_indian_city_is_in_market(self):
        body = b"WPP Media - Bengaluru, KA; Gurugram, HR; London."
        _identity, in_market, _h, _s = confirm_tenants.confirm(body, "WPP Media", "IN")
        self.assertTrue(in_market)

    def test_the_country_name_alone_is_enough(self):
        body = b"Remote - India"
        _identity, in_market, _h, _s = confirm_tenants.confirm(body, "Acme Widgets", "IN")
        self.assertTrue(in_market)


class IdentityTests(unittest.TestCase):
    def test_every_distinctive_word_must_appear_not_just_one(self):
        """"Sonic Automotive" must not be confirmed by a board saying "sonic"."""
        body = b"SonicWall careers. Firewalls and network security."
        identity, _m, hits, strong = confirm_tenants.confirm(
            body, "Sonic Automotive", "IN")
        self.assertFalse(identity)
        self.assertNotEqual(len(hits), len(strong))

    def test_generic_words_cannot_carry_an_identity(self):
        """A name made only of weak words proves nothing, so it is refused
        rather than confirmed by a board that says "solutions"."""
        body = b"Global Technology Solutions Limited - we do solutions."
        identity, _m, _h, strong = confirm_tenants.confirm(
            body, "Global Solutions Limited", "IN")
        self.assertEqual(strong, [])
        self.assertFalse(identity)

    def test_a_name_that_reduces_to_nothing_is_refused(self):
        """AT&T becomes ['at', 't'], both too short to be distinctive."""
        identity, _m, _h, strong = confirm_tenants.confirm(
            b"Careers at some board", "AT&T", "IN")
        self.assertEqual(strong, [])
        self.assertFalse(identity)


if __name__ == "__main__":
    unittest.main()
