"""Python 3.10 compatibility for the test suite: TestCase.enterContext arrived in 3.11."""
import unittest

if not hasattr(unittest.TestCase, 'enterContext'):
    def enterContext(self, cm):
        value = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        return value
    unittest.TestCase.enterContext = enterContext
