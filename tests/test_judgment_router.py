import random
import unittest

from newsverify.risk_benchmark.judgment_router import choose, partial_fallback


def expected_brier(p, q):
    return q*(1-p)**2 + (1-q)*p**2


class JudgmentRouterTests(unittest.TestCase):
    def test_partial_fallback_improves_expected_loss_over_interval(self):
        rng = random.Random(138)
        partial_count = 0
        for _ in range(2000):
            a, c = rng.random(), rng.random()
            low, high = sorted((rng.random(), rng.random()))
            result = partial_fallback(a, c, low, high)
            self.assertGreaterEqual(result.circuit_weight, 0)
            self.assertLessEqual(result.circuit_weight, 1)
            self.assertAlmostEqual(result.probability,
                                   a+result.circuit_weight*(c-a))
            for q in (low, (low+high)/2, high):
                self.assertLessEqual(expected_brier(result.probability, q),
                                     expected_brier(a, q)+1e-15)
            partial_count += 0 < result.circuit_weight < 1
        self.assertGreater(partial_count, 100)

    def test_partial_fallback_uses_only_supported_fraction(self):
        result = partial_fallback(.03, .04, .032, .06)
        self.assertAlmostEqual(result.probability, .032)
        self.assertAlmostEqual(result.circuit_weight, .2)
        self.assertEqual(choose(.03, .04, .032, .06).arm, 'Astra')
        self.assertEqual(partial_fallback(.03, .04, .02, .06).circuit_weight, 0)
        self.assertEqual(partial_fallback(.03, .04, .05, .06).circuit_weight, 1)
        self.assertAlmostEqual(partial_fallback(.06, .04, .01, .055).probability, .055)
        self.assertEqual(partial_fallback(.06, .04, .01, .07).circuit_weight, 0)
        self.assertEqual(partial_fallback(.06, .04, .01, .02).circuit_weight, 1)

    def test_point_judgment_matches_expected_brier_minimizer(self):
        rng = random.Random(136)
        for _ in range(1000):
            a, c, q = rng.random(), rng.random(), rng.random()
            route = choose(a, c, q, q)
            selected = a if route.arm == 'Astra' else c
            self.assertLessEqual(expected_brier(selected, q),
                                 expected_brier(c if route.arm == 'Astra' else a, q)+1e-15)

    def test_interval_only_routes_when_better_at_both_ends(self):
        rng = random.Random(137)
        for _ in range(1000):
            a, c = rng.random(), rng.random()
            low, high = sorted((rng.random(), rng.random()))
            route = choose(a, c, low, high)
            if route.arm == 'Circuit':
                for q in (low, high):
                    self.assertLess(expected_brier(c, q), expected_brier(a, q))

    def test_missing_judgment_ties_and_boundary_default_to_astra(self):
        self.assertEqual(choose(.03, .04).arm, 'Astra')
        self.assertEqual(choose(.03, .03, 0, 1).arm, 'Astra')
        self.assertEqual(choose(.03, .04, .035, .035).arm, 'Astra')
        self.assertEqual(choose(.03, .04, .04, .05).arm, 'Circuit')

    def test_invalid_probabilities_are_rejected(self):
        for bad in (float('nan'), float('inf'), True, -0.1, 1.1, '0.5'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                choose(.1, bad, .2, .3)
        with self.assertRaises(ValueError):
            choose(.1, .2, .3, .2)
        with self.assertRaises(ValueError):
            choose(.1, .2, .3, None)
        with self.assertRaises(ValueError):
            choose(.1, .1, float('nan'), .5)
        with self.assertRaises(ValueError):
            partial_fallback(.1, .2, .4, float('nan'))


if __name__ == '__main__':
    unittest.main()
