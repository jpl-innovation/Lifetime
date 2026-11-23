"""1.3 server uptime on the dashboard: offscreen Qt with the fake admin connection of test_server_gui."""
import unittest

from test_server_gui import ServerGuiTests as _Base


class UptimeGuiTests(_Base):
    # Only the tests below run here; the inherited ones run in test_server_gui.
    def test_keep_detailed_records_for_loads_saves_and_translates(self):
        uptime = self.window.uptime
        self.window._load_configuration()
        self.wait_idle()
        self.assertTrue(uptime.detail_days.isEnabled())
        self.assertEqual(uptime.detail_days.value(), 400)
        uptime.detail_days.setValue(730)
        uptime.detail_save.click()
        self.wait_idle()
        self.assertEqual(self.arguments('set_detail_retention'), [{'days': 730}])
        self.assertEqual(self.window.status_label.text(), 'Changes saved.')
        self.window.language = 'vi'
        self.window.retranslate()
        self.assertEqual(uptime.detail_days.suffix(), ' ngày')
        self.assertEqual(uptime.detail_days.accessibleName(), 'Giữ dữ liệu chi tiết trong')


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromNames([f'{__name__}.UptimeGuiTests.{name}' for name in dir(UptimeGuiTests)
                                      if name.startswith('test_') and name not in dir(_Base)])


if __name__ == '__main__':
    unittest.main()
