"""Fractional ranks: the ordering property (a key between any two),
append growth (short keys for a long sheet), midpoint depth (about
one character per six inserts into the same gap), the keys the
scheme refuses, and the id mint every keyset by id rests on (the
kernel ships no test runner, so its two order primitives are pinned
here, beside the sheet that depends on them).

Run: DJANGO_ENV=test uv run python manage.py test lists.tests.test_ranks
"""

from __future__ import annotations

import random

from django.test import SimpleTestCase

from openbower_kernel.fields import is_valid_ulid, new_ulid
from openbower_kernel.ranks import DIGITS, RankError, first_key, key_between, keys_between, respace_keys, validate


class RankTests(SimpleTestCase):
    def test_the_first_key_and_appends_count_up(self):
        first = key_between(None, None)
        self.assertEqual(first, "a0")
        keys = keys_between(None, None, 5)
        self.assertEqual(keys, ["a0", "a1", "a2", "a3", "a4"])
        self.assertEqual(key_between("a4", None), "a5")
        # Past the two-character space the head grows one digit.
        self.assertEqual(key_between("az", None), "b00")
        self.assertEqual(key_between("b00", None), "b01")

    def test_fifty_thousand_appends_stay_four_characters(self):
        keys = keys_between(None, None, 50_000)
        self.assertEqual(sorted(keys), keys)
        self.assertEqual(len(set(keys)), 50_000)
        self.assertLessEqual(max(len(k) for k in keys), 4)

    def test_the_first_key_is_the_one_an_empty_order_hands_out(self):
        self.assertEqual(first_key(), "a0")
        self.assertEqual(first_key(), keys_between(None, None, 1)[0])

    def test_respace_keys_are_disjoint_ordered_and_take_the_shorter_side(self):
        # Held keys just above the Y/Z head boundary: the keys below them
        # need a longer head, so the fresh set goes ABOVE the highest.
        held = ["Z1", "Z2", "Z3", "Z4", "Z5"]
        fresh = respace_keys(held)
        self.assertEqual(fresh, sorted(fresh))
        self.assertEqual(len(fresh), len(held))
        self.assertTrue(all(key > max(held) for key in fresh))
        self.assertTrue(set(fresh).isdisjoint(held))
        # Held at the top of a head: the next key up needs a longer head,
        # so the fresh set goes BELOW the lowest.
        fresh = respace_keys(["az"])
        self.assertTrue(fresh[0] < "az" and len(fresh[0]) == 2)
        for key in fresh:
            validate(key)

    def test_a_key_exists_between_any_two_and_orders_between_them(self):
        a, b = "a0", "a1"
        for _ in range(40):
            mid = key_between(a, b)
            self.assertTrue(a < mid < b, (a, mid, b))
            validate(mid)
            b = mid  # keep inserting at the same spot: the tail deepens
        # About a character per six inserts (a base-62 digit halves six
        # times): 40 inserts fit in 9. FAILS at a character per insert.
        self.assertLessEqual(len(b), 12)

    def test_inserting_at_the_top_counts_down(self):
        self.assertEqual(key_between(None, "a0"), "Zz")
        self.assertEqual(key_between(None, "Zz"), "Zy")
        keys = keys_between(None, "a0", 3)
        self.assertEqual(keys, sorted(keys))
        self.assertTrue(all(k < "a0" for k in keys))

    def test_many_keys_between_two_stay_ordered(self):
        keys = keys_between("a0", "a1", 25)
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(set(keys)), 25)
        self.assertTrue(all("a0" < k < "a1" for k in keys))

    def test_random_moves_never_break_the_order(self):
        rng = random.Random(7)
        order = keys_between(None, None, 20)
        for _ in range(500):
            i = rng.randrange(len(order) + 1)
            before = order[i - 1] if i > 0 else None
            after = order[i] if i < len(order) else None
            key = key_between(before, after)
            order.insert(i, key)
            self.assertEqual(order, sorted(order))
            self.assertEqual(len(set(order)), len(order))

    def test_the_scheme_refuses_keys_it_did_not_make(self):
        # Refused as the scheme's own error, whatever the package raises:
        # the cursor parser turns exactly RankError into a 400.
        for bad in ("", "0", "a", "a10", "A" + DIGITS[0] * 26, "!x", "a\x00", "a1!", "a" * 100):
            with self.subTest(bad=bad), self.assertRaises(RankError):
                validate(bad)
        with self.assertRaises(RankError):
            key_between("a1", "a0")
        with self.assertRaises(RankError):
            key_between("a0", "a0")


class MonotonicUlidTests(SimpleTestCase):
    def test_ids_minted_in_one_millisecond_count_up(self):
        # A walk pages by id and a consent range is captured as an id,
        # so id order must be insertion order INSIDE a bulk insert too,
        # where every id shares a millisecond. FAILS if the mint draws
        # a fresh random part per id again.
        ids = [new_ulid() for _ in range(5_000)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), len(ids))
        self.assertTrue(all(is_valid_ulid(i) for i in ids))
