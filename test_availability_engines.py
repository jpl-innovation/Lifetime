"""The optional Rust accelerator (rust/lifetime_fast) against the Python reference in lifetime_availability.

The same cases run against the Python functions (checked against a slow, obvious computation) and, when lifetime_fast
is built, against the Rust copy, which must give identical results: the same numbers, the same types. Without the Rust
module the Rust half is skipped (only the Python half runs); LIFETIME_REQUIRE_RUST=1 (CI) makes it fail instead.
"""
import datetime as dt
import os
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import lifetime_availability as availability
import lifetime_uptime
from test_availability import ALWAYS, new_york

try:
    import lifetime_fast
except ImportError:
    lifetime_fast = None

UTC = dt.timezone.utc
HERE = os.path.dirname(os.path.abspath(__file__))
FIX = ('The Rust copy (rust/lifetime_fast/src/lib.rs) no longer matches the Python in lifetime_availability.py: change '
       'the Rust function of the same name the same way, see rust/lifetime_fast/README.md "If you change the Python" '
       '(or "How to ship without Rust").')
STATES = ('online', 'offline', 'offline', 'online', 'unknown')
BASES = (dt.datetime(1900, 1, 1, tzinfo=UTC), dt.datetime(2026, 10, 25, tzinfo=UTC),     # 1 Nov 2026: a DST change
         dt.datetime(2026, 3, 1, tzinfo=UTC), dt.datetime(9000, 6, 1, tzinfo=UTC))       # 8 Mar 2026: the other one


def random_intervals(chance, base, count, merged=True):
    """Touching, zero-length, unsorted and overlapping intervals with microseconds; merged and sorted if asked."""
    items = []
    for _ in range(count):
        start = base + dt.timedelta(minutes=chance.randint(0, 30 * 1440), microseconds=chance.randint(0, 999999))
        items.append((start, start + dt.timedelta(minutes=chance.choice((0, 1, 59, 600, 3000)),
                                                  microseconds=chance.randint(0, 999999))))
        if chance.random() < .2:            # one that touches the previous one
            items.append((items[-1][1], items[-1][1] + dt.timedelta(seconds=chance.randint(0, 7200))))
    chance.shuffle(items)
    return availability._merge(items) if merged else items


def sums_cases():
    """(scheduled, cuts, online, offline, edges) as device_availability passes them: merged, sorted, edges ascending."""
    chance = random.Random(7)
    yield [], [], [], [], []
    yield [], [], [], [], [BASES[1]]
    for case in range(80):
        base = BASES[case % len(BASES)]
        online = random_intervals(chance, base, chance.randint(0, 60))
        offline = availability._subtract(random_intervals(chance, base, chance.randint(0, 60)), online)
        edges = sorted(base + dt.timedelta(days=chance.randint(-2, 32), hours=chance.choice((0, 0, 5)))
                       for _ in range(chance.randint(1, 40)))      # repeated edges too, as clamped day starts are
        yield (random_intervals(chance, base, chance.randint(0, 20)), random_intervals(chance, base, chance.randint(0, 6)),
               online, offline, edges)


def device_cases():
    """Arguments of device_availability: random presence and maintenance, schedules, zones, spans, daily rows."""
    chance = random.Random(13)
    business = dict(ALWAYS, kind='business', start='22:00', end='06:00', weekdays=[0, 2, 3, 6], holidays=['2026-11-03'])
    for case in range(60):
        base = BASES[1 + case % 2]
        zone = chance.choice([UTC, new_york(), None])
        presence = [(chance.choice(STATES), start, None if chance.random() < .03 else end)
                    for start, end in random_intervals(chance, base, chance.randint(0, 80), merged=False)]
        maintenance = random_intervals(chance, base, chance.randint(0, 4), merged=False)
        schedule = chance.choice([ALWAYS, business, dict(business, start='08:00', end='18:00'), dict(ALWAYS, kind='auto')])
        first = base.date() + dt.timedelta(days=chance.randint(-2, 10))
        last = first + dt.timedelta(days=chance.randint(0, 20))
        now = base + dt.timedelta(days=chance.randint(0, 35), microseconds=chance.randint(0, 999999))
        kind = chance.choice([None, 'day', 'week', 'month'])
        yield ((presence, schedule, chance.random() < .3, maintenance, first, last, zone, now),
               dict(daily=chance.random() < .5, since=base + dt.timedelta(hours=chance.randint(0, 200)) if case % 7 == 0 else None,
                    spans=lifetime_uptime.buckets(first, last, kind) if kind else None))
    huge = dt.datetime(9000, 6, 1, 12, 0, 0, 1, tzinfo=UTC)
    for presence in ([], [('online', huge, huge + dt.timedelta(days=3)), ('offline', huge + dt.timedelta(days=3), None)]):
        yield ((presence, ALWAYS, False, [(huge, huge + dt.timedelta(hours=5))], huge.date(), huge.date() + dt.timedelta(days=9),
                UTC, huge + dt.timedelta(days=7, microseconds=5)), dict(daily=True))


def slow_sums(scheduled, cuts, online, offline, edges):
    """_sums the obvious way: every pair of intervals, every bucket."""
    def intersect(first, second):
        return sorted((max(a, c), min(b, d)) for a, b in first for c, d in second if max(a, c) < min(b, d))

    def subtract(first, second):
        result = []
        for start, end in first:
            for low, high in second:
                if low < end and high > start:
                    if low > start:
                        result.append((start, low))
                    start = max(start, high)
            if start < end:
                result.append((start, end))
        return result

    def spread(intervals):
        return [sum(max(0.0, (min(end, high) - max(start, low)).total_seconds()) for start, end in intervals)
                for low, high in zip(edges, edges[1:])]
    counted = subtract(scheduled, cuts)
    lengths = []
    for start, end in offline:
        seconds = 0.0
        for low, high in counted:
            if low < end and high > start:
                seconds += (min(end, high) - max(start, low)).total_seconds()
        if seconds > 0:
            lengths.append(seconds)
    return (spread(intersect(counted, online)), spread(intersect(counted, offline)), spread(counted),
            spread(intersect(scheduled, cuts)), len(lengths), round(max(lengths, default=0)))


class PythonReferenceTests(unittest.TestCase):
    """The cases the Rust copy is compared on, run through the Python reference."""

    def test_python_sums_agree_with_the_slow_computation(self):
        for index, case in enumerate(sums_cases()):
            with self.subTest(case=index):
                got, want = availability._sums(*case), slow_sums(*case)
                self.assertEqual(got[4:], want[4:])
                for got_list, want_list in zip(got[:4], want[:4]):
                    self.assertEqual(len(got_list), len(want_list))
                    for value, expected in zip(got_list, want_list):
                        self.assertAlmostEqual(value, expected, delta=1e-6)

    def engine(self, extra_path=None, **environment):
        """lifetime_availability's ENGINE and whether it kept lifetime_fast, in a fresh Python."""
        environment = dict(os.environ, **environment)
        if extra_path:
            environment['PYTHONPATH'] = os.pathsep.join(filter(None, [extra_path, environment.get('PYTHONPATH')]))
        found = subprocess.run([sys.executable, '-c', 'import lifetime_availability as a; print(a.ENGINE); '
                                'print(a.lifetime_fast is not None)'], env=environment, capture_output=True, text=True,
                               timeout=60, cwd=HERE)
        return found.stdout.splitlines(), found.stderr

    def test_the_engine_can_be_forced_to_python(self):
        current = lifetime_fast is not None and getattr(lifetime_fast, 'API', None) == availability.RUST_API
        expected = 'Rust accelerator' if current and os.environ.get('LIFETIME_NO_RUST') != '1' else 'Python'
        self.assertEqual(availability.ENGINE.split(' (')[0], expected)
        self.assertEqual(self.engine(LIFETIME_NO_RUST='1')[0], ['Python', 'False'])

    def test_an_out_of_date_rust_module_is_not_used(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, 'lifetime_fast.py'), 'w', encoding='utf-8') as module:
                module.write('API = 0\ndef sums(*arguments):\n    raise AssertionError("never called")\n')
            lines, errors = self.engine(folder, LIFETIME_NO_RUST='0')
        self.assertEqual(lines, ['Python (Rust accelerator out of date)', 'False'], errors)

    def test_a_failing_rust_copy_falls_back_to_python_for_that_call_and_says_so_once(self):
        class Panic(BaseException):     # pyo3's PanicException is a BaseException, not an Exception
            pass

        class Broken:
            calls = 0

            def sums(self, *arguments):
                Broken.calls += 1
                raise Panic('index out of bounds')
        case = list(sums_cases())[5]
        with patch.object(availability, 'lifetime_fast', Broken()), patch.object(availability, '_rust_failed', False), \
                patch('sys.stderr') as stderr:
            self.assertEqual(availability._fast_sums(*case), availability._sums(*case))
            self.assertEqual(availability._fast_sums(*case), availability._sums(*case))
        self.assertEqual(Broken.calls, 2)
        self.assertEqual(sum('Rust accelerator failed (Panic)' in str(call) for call in stderr.write.call_args_list), 1)
        with patch.object(availability, 'lifetime_fast', Broken()), patch.object(Broken, 'sums', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                availability._fast_sums(*case)


class RustCopyTests(unittest.TestCase):
    """lifetime_fast gives exactly what the Python gives: equal values and an equal repr (float stays float)."""

    def setUp(self):
        if lifetime_fast is None:
            if os.environ.get('LIFETIME_REQUIRE_RUST') == '1':
                self.fail('LIFETIME_REQUIRE_RUST=1 but lifetime_fast (the Rust accelerator) cannot be imported')
            self.skipTest('lifetime_fast (the Rust accelerator, rust/lifetime_fast) is not built here, so only the '
                          'Python reference was checked. CI builds it and runs this comparison.')

    def assertSame(self, got, want):
        self.assertEqual(got, want, FIX)
        self.assertEqual(repr(got), repr(want), FIX)

    def test_the_built_module_is_the_version_this_python_expects(self):
        self.assertEqual(getattr(lifetime_fast, 'API', None), availability.RUST_API,
                         'lifetime_fast was built from an older or newer rust/lifetime_fast/src/lib.rs (API) than '
                         'lifetime_availability.RUST_API: rebuild it (rust/lifetime_fast/README.md) or raise both together')

    def test_rust_sums_are_identical(self):
        with patch.object(availability, 'lifetime_fast', lifetime_fast):
            for index, case in enumerate(sums_cases()):
                with self.subTest(case=index):
                    self.assertSame(availability._fast_sums(*case), availability._sums(*case))

    def test_device_availability_is_identical_with_either_engine(self):
        for index, (arguments, options) in enumerate(device_cases()):
            with self.subTest(case=index):
                with patch.object(availability, 'lifetime_fast', None):
                    python = availability.device_availability(*arguments, **options)
                with patch.object(availability, 'lifetime_fast', lifetime_fast):
                    rust = availability.device_availability(*arguments, **options)
                self.assertSame(rust, python)


if __name__ == '__main__':
    unittest.main()
