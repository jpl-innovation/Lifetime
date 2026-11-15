"""The load-test tool's safety refusal and statistics (no server is started here)."""
import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'tools'))
import fleet_simulator as simulator  # noqa: E402


class FleetSimulatorTests(unittest.TestCase):
    def refused(self, *argv):
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()) as error:
            simulator.main(list(argv))
        self.assertEqual(caught.exception.code, 2)
        return error.getvalue()

    def test_a_server_it_did_not_start_needs_i_understand(self):
        attach = ('--attach-directory', 'D:/elsewhere', '--attach-address', 'https://server.example:8443',
                  '--attach-admin-port', '8444')
        self.assertIn('--i-understand', self.refused(*attach))
        self.assertIn('go together', self.refused('--attach-directory', 'D:/elsewhere', '--i-understand'))
        self.assertIn('only simulated', self.refused(*attach, '--i-understand', '--checks', '3'))
        self.refused('--devices', '0')

    def test_nearest_rank_percentiles(self):
        result = simulator.percentiles(list(range(1, 101)))
        self.assertEqual((result['p50'], result['p95'], result['p99'], result['max'], result['count']), (50, 95, 99, 100, 100))
        self.assertEqual(simulator.percentiles([7])['p99'], 7)
        self.assertIsNone(simulator.percentiles([]))


if __name__ == '__main__':
    unittest.main()
