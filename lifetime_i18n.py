"""Small English/Vietnamese catalogue. English copy is also the stable lookup key."""
import datetime as dt
import locale
import os
import sys
import weakref

_POWER_VI = {
    'Disk almost full': 'Ổ đĩa gần đầy',
    'Battery worn': 'Pin đã chai',
    'Running hot': 'Đang quá nóng',
    'Waiting to run a command': 'Đang chờ thực hiện lệnh',
    'Command accepted': 'Đã chấp nhận lệnh',
    'Unsupported command action': 'Thao tác lệnh không được hỗ trợ',
    'Remote power consent is required': 'Cần sự đồng ý cho phép điều khiển nguồn từ xa',
    'Power action failed': 'Thao tác nguồn thất bại',
    'Remote power': 'Điều khiển nguồn từ xa',
    'Invalid command action': 'Thao tác lệnh không hợp lệ',
    'Device must be approved and online': 'Máy tính phải được phê duyệt và đang trực tuyến',
    'Device already has a pending command': 'Máy tính đã có một lệnh đang chờ',
    'Unknown command': 'Lệnh không xác định',
    'Command is no longer pending': 'Lệnh không còn ở trạng thái chờ',
    'Wake requires an approved offline device with remote power consent': 'Chỉ đánh thức được máy tính đã phê duyệt, đang ngoại tuyến và đã đồng ý điều khiển nguồn từ xa',
    'No MAC addresses are available': 'Không có địa chỉ MAC',
    'Lifetime: your IT team is restarting this computer in 1 minute. Save your work now.': 'Lifetime: bộ phận IT sẽ khởi động lại máy tính này sau 1 phút. Hãy lưu công việc ngay.',
    'Lifetime: your IT team is shutting down this computer in 1 minute. Save your work now.': 'Lifetime: bộ phận IT sẽ tắt máy tính này sau 1 phút. Hãy lưu công việc ngay.',
}


def default_language():
    try:
        if sys.platform == 'win32':
            import ctypes
            name = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), '')
        else:
            name = (os.environ.get('LANGUAGE', '').split(':')[0] or
                    locale.getlocale(getattr(locale, 'LC_MESSAGES', locale.LC_CTYPE))[0] or locale.getlocale()[0] or '')
        return 'vi' if name.lower().startswith('vi') else 'en'
    except (ValueError, AttributeError):
        return 'en'


_language = default_language()
_listeners = []


class Text(str):
    """A string with its source key retained for live Qt relabeling."""
    def __new__(cls, value, key, fmt):
        result = super().__new__(cls, value)
        result.key, result.fmt = key, fmt
        return result


def language():
    return _language


def on_language_change(callback):
    reference = weakref.WeakMethod(callback)
    _listeners.append(reference)


def set_language(value):
    global _language
    if value not in TABLES:
        raise ValueError('Unsupported language')
    if value == _language:
        return
    _language = value
    obsolete = []
    for reference in list(_listeners):
        callback = reference()
        if callback is not None:
            try:
                callback()
            except RuntimeError as error:
                # Qt can delete a native widget before Python releases its
                # wrapper. It is no longer a live language-change subscriber.
                if 'has been deleted' not in str(error):
                    raise
                obsolete.append(reference)
    _listeners[:] = [reference for reference in _listeners if reference() is not None and reference not in obsolete]


def tr(key, **fmt):
    if isinstance(key, Text):
        fmt = {**key.fmt, **fmt}
        key = key.key
    fmt = {name: tr(value) if isinstance(value, Text) else value for name, value in fmt.items()}
    return Text(TABLES[_language][key].format(**fmt), key, fmt)


def ui_text(value):
    """Translate known OS state codes; leave hardware and network names intact."""
    return tr(value) if isinstance(value, Text) or isinstance(value, str) and value in EN else value


def month(number):
    return tr('month.'+str(number))


def weekdays():
    return tuple(tr('weekday.'+str(i)) for i in range(7))


def date_text(value, year=True):
    if not isinstance(value, (dt.date, dt.datetime)):
        value = dt.date.fromisoformat(str(value)[:10])
    return tr('date.full' if year else 'date.short', day=value.day,
              month=month(value.month), year=value.year)


def datetime_text(value):
    return tr('date.time', date=date_text(value), time=time_text(value))


def time_text(value, seconds=False):
    return tr('time.seconds' if seconds else 'time.clock', hour=value.hour, minute=value.minute, second=value.second)


def duration(seconds, days=True):
    minutes = int(seconds//60)
    if days:
        return tr('duration.days', days=minutes//1440, hours=minutes//60 % 24, minutes=minutes % 60)
    return tr('duration.hours', hours=minutes//60, minutes=minutes % 60)


VI = {
    'Lifetime': 'Lifetime',
    'Lifetime · JPL Innovation': 'Lifetime · JPL Innovation',
    'Lifetime · Made by JPL Innovation': 'Lifetime · Phát triển bởi JPL Innovation',
    'Settings': 'Cài đặt', 'Overview': 'Tổng quan', 'Insights': 'Phân tích',
    'Total time awake': 'Tổng thời gian hoạt động',
    'First used': 'Ngày bắt đầu sử dụng', 'Times started': 'Số lần khởi động',
    'Current session': 'Phiên hiện tại', 'Longest session': 'Phiên dài nhất',
    'Average awake / day': 'Hoạt động trung bình / ngày',
    'Since tracking began': 'Kể từ khi bắt đầu theo dõi',
    'since tracking began': 'kể từ khi bắt đầu theo dõi',
    'Awake this session': 'Thời gian hoạt động trong phiên', 'Longest recorded session': 'Phiên dài nhất đã ghi nhận',
    'Needs 1 day of data': 'Cần ít nhất 1 ngày dữ liệu',
    'Sleep and hibernation are not counted.': 'Không tính thời gian ngủ và ngủ đông.',
    '+  Import starting hours': '+  Nhập số giờ ban đầu',
    'Known hours before tracking. Replaces the existing baseline.': 'Số giờ đã sử dụng trước khi theo dõi. Giá trị này sẽ thay thế số giờ ban đầu hiện có.',
    'Starting hours': 'Số giờ ban đầu', 'Set baseline': 'Lưu số giờ ban đầu',
    'Add earlier usage': 'Thêm thời gian đã dùng trước đó',
    'Select all': 'Chọn tất cả',
    'Auto': 'Tự động', 'Light': 'Sáng', 'Dark': 'Tối', 'Theme': 'Giao diện', 'Style': 'Kiểu giao diện', 'Glass': 'Kính', 'Classic': 'Cổ điển',
    'history.banner': 'Lifetime hiện chỉ tính thời gian từ lần khởi động gần nhất. Thêm thời gian đã dùng trước đó để tổng thể hiện đúng máy đã được dùng bao lâu. Việc này đọc nhật ký của chính máy; không có dữ liệu nào rời khỏi máy.',
    'Start tracker': 'Bắt đầu theo dõi', 'Tracking active': 'Đang theo dõi', 'Tracker offline': 'Đã dừng theo dõi',
    "Can't read saved data": 'Không đọc được dữ liệu',
    'Recording awake time\nTracker saves every 30 s · view refreshes every 1 s': 'Đang ghi thời gian hoạt động\nLưu dữ liệu mỗi 30 giây · cập nhật giao diện mỗi giây',
    'Saved totals are available.\nStart the tracker to resume recording.': 'Đang hiển thị dữ liệu đã lưu.\nBắt đầu theo dõi để tiếp tục ghi nhận.',
    'Baseline could not be saved': 'Không thể lưu số giờ ban đầu', 'Could not start tracker': 'Không thể bắt đầu theo dõi',
    'Starting tracker…': 'Đang bắt đầu theo dõi…',
    'Verify': 'Đối chiếu', 'Verifying…': 'Đang đối chiếu…',
    'Windows event log': 'nhật ký sự kiện Windows', 'system journal': 'nhật ký hệ thống',
    'Not available · verification timed out or could not complete': 'Chưa có kết quả · quá thời gian chờ hoặc không thể hoàn tất đối chiếu',
    'Earlier usage could not be added. Check that the history importer is installed.': 'Không thể thêm thời gian đã dùng trước đó. Hãy kiểm tra công cụ nhập lịch sử đã được cài đặt.',
    "Couldn't change the start-automatically setting": 'Không đổi được cài đặt tự chạy khi mở máy',
    "Couldn't change the start-automatically setting. Try again, or ask the person who looks after this computer to check that Lifetime is allowed to start by itself.": 'Không đổi được cài đặt tự chạy khi mở máy. Hãy thử lại, hoặc nhờ người quản lý máy tính kiểm tra xem Lifetime có được phép tự chạy không.',
    'No record': 'Chưa ghi nhận',
    'Not available · Windows event log could not be read': 'Chưa có dữ liệu · không đọc được nhật ký sự kiện Windows',
    'Not available · the Windows event log does not go back to the last start-up': 'Chưa có dữ liệu · nhật ký Windows không bao quát lần khởi động gần nhất',
    'Not available · Windows event log has incomplete sleep intervals': 'Chưa có dữ liệu · nhật ký Windows thiếu các mốc ngủ và thức',
    'Not available · system journal could not be read': 'Chưa có dữ liệu · không đọc được nhật ký hệ thống',
    'Not available · this system has no systemd journal': 'Chưa có dữ liệu · hệ thống này không có nhật ký systemd',
    'Not available · journal has insufficient suspend evidence': 'Chưa có dữ liệu · nhật ký không đủ thông tin về thời gian ngủ',
    'Not available · journal has incomplete suspend intervals': 'Chưa có dữ liệu · nhật ký thiếu các mốc ngủ và thức',
    'Not available · journal clocks are inconsistent': 'Chưa có dữ liệu · các mốc thời gian trong nhật ký không nhất quán',
    'Compared journal suspend intervals with the awake clock': 'Đã đối chiếu thời gian ngủ trong nhật ký với bộ đếm thời gian hoạt động',
    'Reading local system…': 'Đang đọc thông tin hệ thống…',
    'System readings are off. Choose what to collect in Settings.': 'Đang tắt thông tin hệ thống. Chọn dữ liệu cần thu thập trong Cài đặt.',
    'Power': 'Nguồn điện', 'Network': 'Mạng',
    'Battery': 'Pin', 'State': 'Trạng thái', 'Time remaining': 'Thời gian còn lại',
    'Voltage': 'Điện áp', 'Design capacity': 'Dung lượng thiết kế', 'Battery wear': 'Độ chai pin',
    'No battery': 'Không có pin', '0 W · battery idle': '0 W · pin không nạp hoặc xả',
    'Charging': 'Đang sạc', 'Discharging': 'Đang dùng pin', 'Plugged in': 'Đang cắm nguồn',
    'Fully charged': 'Đã sạc đầy', 'Full': 'Đã sạc đầy',
    'On battery': 'Đang dùng pin', 'AC power': 'Nguồn điện ngoài', 'On AC power': 'Đang dùng nguồn điện ngoài', 'Not charging': 'Không sạc',
    'Local time': 'Giờ địa phương', 'Not available from this system': 'Hệ thống chưa cung cấp dữ liệu này',
    'live.latest': '{value:,.1f} {unit} · mới nhất',
    'Adapter': 'Bộ điều hợp', 'Active adapters': 'Bộ điều hợp đang hoạt động',
    'No active adapters': 'Không có bộ điều hợp đang hoạt động',
    'Download': 'Tải xuống', 'Upload': 'Tải lên', 'Wi-Fi SSID': 'Tên mạng Wi-Fi', 'Disconnected': 'Chưa kết nối',
    'Wi-Fi signal': 'Tín hiệu Wi-Fi', 'Band / channel': 'Băng tần / kênh',
    'Wi-Fi receive': 'Tốc độ nhận Wi-Fi', 'Wi-Fi transmit': 'Tốc độ gửi Wi-Fi',
    'Wi-Fi': 'Wi-Fi', 'No Wi-Fi adapter': 'Không có bộ điều hợp Wi-Fi',
    'Graphics card': 'Card đồ họa', 'No graphics adapter detected': 'Không phát hiện card đồ họa',
    'Model': 'Mẫu thiết bị', 'Frequency': 'Xung nhịp', 'Temperature': 'Nhiệt độ', 'Utilization': 'Mức sử dụng', 'Memory': 'Bộ nhớ',
    'Processor': 'Bộ xử lý', 'CPU': 'CPU', 'RAM': 'RAM', 'Disks': 'Ổ đĩa',
    'Time since start-up · incl. sleep': 'Thời gian từ lúc khởi động · gồm cả ngủ',
    'Awake since start-up': 'Hoạt động từ lúc khởi động',
    'Fast Startup': 'Khởi động nhanh', 'On': 'Bật', 'Off': 'Tắt',
    'Local readings · 2 s / 10 s': 'Dữ liệu trên máy · mỗi 2 giây / 10 giây',
    'Awake time by hour of day': 'Thời gian hoạt động theo giờ trong ngày', 'Daily awake time': 'Thời gian hoạt động theo ngày',
    '90 days': '90 ngày', '1 year': '1 năm', 'All': 'Tất cả',
    '<15m': '<15p', '15–60m': '15–60p', '1–3h': '1–3g', '3–6h': '3–6g', '6–12h': '6–12g', '12h+': '12g+',
    'Needs recorded days': 'Cần dữ liệu theo ngày',
    'Recorded hourly totals / recorded days; time from start-up until recording began is left out': 'Tổng theo giờ / số ngày đã ghi nhận; không gồm thời gian từ lúc khởi động đến khi bắt đầu ghi',
    'Recorded hourly totals / recorded days': 'Tổng theo giờ / số ngày đã ghi nhận',
    'Dated hourly history is unavailable for this range': 'Khoảng này chưa có dữ liệu theo giờ gắn với ngày cụ thể',
    'Chart; use the Table button for underlying numbers': 'Biểu đồ; chọn Bảng để xem số liệu chi tiết',
    'No recorded data': 'Chưa có dữ liệu', 'No recorded data in this range': 'Chưa có dữ liệu trong khoảng này',
    'Live tracking began': 'Bắt đầu theo dõi trực tiếp', '−5m': '−5p', '−2m 30s': '−2p 30s', 'Now': 'Hiện tại',
    'Latest ': 'Mới nhất: ', 'Max ': 'Cao nhất: ', '\n7-day average: ': '\nTrung bình 7 ngày: ',
    '\nToday · partial day': '\nHôm nay · ngày chưa kết thúc',
    'Awake hours scale: 0 to 24 hours, five increasing steps. Outline means no recorded data.': 'Thang thời gian hoạt động từ 0 đến 24 giờ, gồm 5 mức tăng dần. Ô viền rỗng là ngày chưa có dữ liệu.',
    '0 h': '0 g', '24 h': '24 g', 'Outline = no record': 'Ô rỗng = chưa ghi nhận',
    'Table': 'Bảng', 'Chart': 'Biểu đồ', 'Show table: ': 'Xem bảng: ', ' data table': ' · bảng dữ liệu',
    'Range': 'Khoảng thời gian', 'Insights date range; applies to every chart': 'Khoảng thời gian phân tích; áp dụng cho mọi biểu đồ',
    'Calendar · awake hours': 'Lịch hoạt động', 'Local days · outlined: today': 'Ngày theo giờ địa phương · viền: hôm nay',
    'Daily awake hours': 'Thời gian hoạt động mỗi ngày',
    'Daily hours incl. time since start-up · line: 7-day average': 'Gồm thời gian từ lúc khởi động · đường: trung bình 7 ngày',
    'Cumulative lifetime hours': 'Tổng thời gian tích lũy', 'Includes imported history and the opening balance': 'Gồm lịch sử đã nhập và số giờ ban đầu',
    'Hour-of-day profile': 'Mức sử dụng theo giờ', 'Average awake hours in each local hour slot': 'Thời gian hoạt động trung bình ở mỗi khung giờ địa phương',
    'Weekday average': 'Trung bình theo thứ', 'Average per recorded weekday; today is a partial day': 'Trung bình theo thứ đã ghi nhận; hôm nay chưa kết thúc',
    'Session length distribution': 'Phân bố thời lượng phiên', 'Retained sessions, grouped by local start date': 'Các phiên đã lưu, nhóm theo ngày bắt đầu tại địa phương',
    'Local date': 'Ngày địa phương', 'Daily': 'Theo ngày', '7-day average': 'Trung bình 7 ngày',
    'Needs 7 days of data': 'Cần ít nhất 7 ngày dữ liệu', 'Needs 7 consecutive days': 'Cần 7 ngày liên tiếp',
    'Lifetime total': 'Tổng tích lũy', 'Live start date unavailable': 'Chưa có ngày bắt đầu theo dõi trực tiếp',
    'Opening balance included · ': 'Đã gồm số giờ ban đầu · ', 'Local hour': 'Giờ địa phương', 'Average awake': 'Hoạt động trung bình',
    'Weekday': 'Thứ', 'No recorded sessions in this range': 'Chưa có phiên nào trong khoảng này',
    ' total awake': ' hoạt động tổng cộng', 'Session length': 'Thời lượng phiên', 'Sessions': 'Số phiên',
    'Total awake time': 'Tổng thời gian hoạt động', 'Selected date range: ': 'Khoảng thời gian đã chọn: ',
    'Collecting live samples': 'Đang thu thập dữ liệu trực tiếp',
    'Battery power': 'Công suất pin', 'GPU power': 'Công suất GPU', 'Waiting for a local reading': 'Đang chờ dữ liệu từ máy',
    'Lifetime settings': 'Cài đặt Lifetime', 'Set up Lifetime': 'Thiết lập Lifetime',
    'What would you like to track?': 'Bạn muốn theo dõi những gì?',
    'Lifetime records how long this computer is awake. Everything stays on this computer. Nothing is uploaded.': 'Lifetime ghi lại thời gian máy tính hoạt động. Mọi dữ liệu được lưu trên máy, không tải lên mạng.',
    'Required. Counts time while this computer is awake.': 'Bắt buộc. Ghi nhận thời gian máy tính hoạt động.',
    'Daily and hourly history': 'Lịch sử theo ngày và giờ', 'Shows when and how long you use this computer.': 'Hiển thị thời điểm và thời lượng bạn sử dụng máy tính.',
    'Battery and power': 'Pin và nguồn điện', 'Shows charge, power use and battery condition.': 'Hiển thị mức pin, công suất tiêu thụ và tình trạng pin.',
    'Shows connection type, link speed and transfer speed.': 'Hiển thị loại kết nối, tốc độ liên kết và tốc độ truyền dữ liệu.',
    'Wi-Fi network name': 'Tên mạng Wi-Fi', 'Optional. Network names can reveal your location.': 'Tùy chọn. Tên mạng có thể tiết lộ vị trí của bạn.',
    'Shows graphics models and available readings.': 'Hiển thị mẫu card đồ họa và các thông số có sẵn.',
    'Computer resources': 'Tài nguyên máy tính', 'Shows CPU, memory and disk use.': 'Hiển thị mức sử dụng CPU, bộ nhớ và ổ đĩa.',
    'Include earlier use of this computer?': 'Tính cả thời gian đã dùng máy này trước đây?',
    'Lifetime has only just been installed, so it can only count from now on. Say Yes and it reads your computer\'s own activity log to work out how long this computer has already been used. Nothing leaves this computer. Older records may be missing, so the earlier total can be approximate.':
        'Lifetime vừa được cài đặt nên chỉ có thể đếm từ bây giờ. Chọn Có để Lifetime đọc nhật ký hoạt động của chính máy và tính xem máy đã được dùng bao lâu. Không có dữ liệu nào rời khỏi máy. Bản ghi cũ có thể bị thiếu nên tổng thời gian trước đó chỉ là ước tính.',
    'Start Lifetime automatically when the computer starts?': 'Tự chạy Lifetime khi mở máy?',
    'Lifetime starts by itself each time you turn on the computer and sign in. You can also open it yourself.': 'Lifetime tự chạy mỗi khi bạn mở máy và đăng nhập. Bạn cũng có thể tự mở Lifetime.',
    'Yes, add my earlier usage': 'Có, thêm thời gian đã dùng trước đó', 'Yes, start automatically': 'Có, tự chạy khi mở máy',
    'No, count from now on': 'Không, chỉ đếm từ bây giờ',
    'Ready to start': 'Sẵn sàng bắt đầu', 'You can change these choices at any time in Settings.': 'Bạn có thể thay đổi các lựa chọn này bất cứ lúc nào trong Cài đặt.',
    'Only the readings you choose will be collected.': 'Chỉ thu thập những dữ liệu bạn đã chọn.',
    "Workplace computers only: to report to your IT team's Lifetime Server, open Connect after setup.": 'Chỉ dành cho máy tính ở nơi làm việc: để báo cáo cho Lifetime Server của bộ phận IT, hãy mở mục Kết nối sau khi cài đặt.',
    'Delete collected history': 'Xóa lịch sử đã thu thập', 'Back': 'Quay lại', 'Next': 'Tiếp tục',
    'Save settings': 'Lưu cài đặt', 'Start': 'Bắt đầu', 'Tracking\n': 'Nội dung theo dõi\n',
    '\n\nEarlier usage: ': '\n\nThời gian đã dùng trước đó: ', 'Add from the computer\'s own log': 'Thêm từ nhật ký của máy', 'Do not add': 'Không thêm',
    '\nStart automatically with the computer: ': '\nTự chạy khi mở máy: ', 'Start automatically with the computer': 'Tự chạy khi mở máy', 'Yes': 'Có', 'No': 'Không',
    'Settings could not be saved': 'Không thể lưu cài đặt', 'Delete collected history?': 'Xóa lịch sử đã thu thập?',
    'This resets recorded totals, starting hours and history. A backup will be kept on this computer. Tracking will continue from now.': 'Thao tác này đặt lại tổng thời gian, số giờ ban đầu và lịch sử. Một bản sao lưu sẽ được giữ trên máy. Việc theo dõi sẽ tiếp tục từ thời điểm hiện tại.',
    'History deleted': 'Đã xóa lịch sử', 'History could not be deleted': 'Không thể xóa lịch sử',
    'Language / Ngôn ngữ': 'Ngôn ngữ / Language', 'English': 'English', 'Tiếng Việt': 'Tiếng Việt',
    'Choose your language. You can change it at any time.': 'Chọn ngôn ngữ hiển thị. Bạn có thể thay đổi bất cứ lúc nào.',
    'EN': 'EN', 'VI': 'VI',
    '&Yes': '&Có', '&No': '&Không', 'OK': 'Đồng ý',
    'Close': 'Đóng', '&Copy': '&Sao chép', '&Paste': '&Dán', 'Select All': 'Chọn tất cả',
    'history.note': 'Ngày thiếu dữ liệu được xem là chưa xác định. Giá trị trung bình chỉ tính các ngày đã ghi nhận, gồm cả ngày có giá trị 0. Mỗi ngày không vượt quá 24 giờ. Lưu tối đa 100 phiên; phạm vi dữ liệu theo giờ được ghi trên biểu đồ.',
    'history.estimated': 'Phân bổ theo ngày có phần ước tính.',
    'history.logged': 'Đã phân bổ thời gian khởi động theo các mốc ngủ và thức trong nhật ký hệ thống.',
    'date.full': '{day} {month}, {year}', 'date.short': '{day} {month}', 'date.time': '{date}, {time}',
    'date.range': '{start} – {end}',
    'time.hour': '{hour:02}:00', 'time.clock': '{hour:02}:{minute:02}', 'time.seconds': '{hour:02}:{minute:02}:{second:02}',
    'duration.days': '{days} ngày {hours} giờ {minutes} phút', 'duration.hours': '{hours:,} giờ {minutes:02} phút',
    'unit.hour': 'g', 'unit.hour.suffix': ' giờ',
    'hero.duration': 'giờ · {days:,} ngày {hours:02} giờ {minutes:02} phút {seconds:02} giây',
    'hours.value': '{value:.2f} giờ', 'axis.hours': '{value:.3g} g',
    'days.in_use': '{days:,} ngày sử dụng · ước tính', 'step': 'Bước {step} / 5',
    'verify.accessible': 'Đối chiếu thời gian hoạt động với {log}',
    'verify.intro': 'Đối chiếu đọc {log} để kiểm tra thời gian hoạt động.',
    'verify.reading': 'Đang đọc dữ liệu và {log}…',
    'verify.gap': 'So sánh với {log} · {events:,.1f} giờ so với {awake:,.1f} giờ · Chênh lệch {gap} giữa nhật ký và đồng hồ hoạt động',
    'verify.result': 'Đã đối chiếu với {log} · {events:,.1f} giờ so với {awake:,.1f} giờ (chênh lệch {difference:.1f} giờ)',
    'verify.compared': 'So sánh với {log} · {events:,.1f} giờ so với {awake:,.1f} giờ (chênh lệch {difference:.1f} giờ) · Cần kiểm tra chênh lệch',
    'verify.ledger_error': '{message} · Không thể cập nhật dữ liệu',
    'rebuild.busy': 'Đang thêm thời gian đã dùng trước đó từ {log}…', 'rebuild.done': 'Đã thêm thời gian đã dùng trước đó từ {log}.',
    'rebuild.error': 'Không thể thêm thời gian đã dùng trước đó. {error}',
    'rebuild.clock': 'Không thể thêm thời gian đã dùng trước đó: nhật ký sự kiện của Windows có mốc thời gian không khớp nhau (có thể đồng hồ đã bị chỉnh), nên không thể tính chính xác. Lifetime sẽ tính từ bây giờ.','ledger.error': 'Không đọc được dữ liệu: {error}',
    'backup.saved': 'Bản sao lưu trên máy:\n{path}',
    'battery.capacity': 'Dung lượng pin {index}', 'battery.power': 'Công suất pin {index}',
    'disk': 'Ổ đĩa {path}', 'gpu.field': '{prefix}{field}',
    'coverage.partial': '{days} ngày có dữ liệu theo giờ; phạm vi chưa đầy đủ',
    'chart.values': '{count} giá trị. {message}', 'chart.sessions': '{count:g} phiên',
    'scope': '{start} – {end} · {days:,} ngày đã ghi nhận · {duration} hoạt động',
    'live.began': 'Bắt đầu theo dõi trực tiếp ngày {date}',
    'sessions.retained': '{count} phiên đã lưu · theo ngày bắt đầu tại địa phương',
}

EN = {key: key for key in VI}
EN.update({
    'history.note': 'Missing dates are unknown. Averages use recorded days, including recorded zeroes. Each day is limited to 24 h. Session history retains up to 100 sessions. Hourly coverage is described on its chart.',
    'history.estimated': 'Daily allocation includes estimates.',
    'history.logged': 'Time since start-up is placed on days using the logged sleep and wake times.',
    'date.full': '{day} {month} {year}', 'date.short': '{day} {month}', 'date.time': '{date}, {time}',
    'date.range': '{start} – {end}',
    'time.hour': '{hour:02}:00', 'time.clock': '{hour:02}:{minute:02}', 'time.seconds': '{hour:02}:{minute:02}:{second:02}',
    'duration.days': '{days} d {hours} h {minutes} m', 'duration.hours': '{hours:,} h {minutes:02} m',
    'unit.hour': 'h', 'unit.hour.suffix': ' h',
    'hero.duration': 'hours · {days:,} d {hours:02} h {minutes:02} m {seconds:02} s',
    'hours.value': '{value:.2f} h', 'axis.hours': '{value:.3g} h',
    'days.in_use': '{days:,} days in use · estimated', 'step': 'Step {step} of 5',
    'verify.accessible': 'Verify awake time against {log}', 'verify.intro': 'Verify reads the {log} to check awake time.',
    'verify.reading': "Reading Lifetime's record and the {log}…",
    'verify.gap': 'Compared with {log} · {events:,.1f} h vs {awake:,.1f} h · Gap of {gap} between the log and the awake clock',
    'history.banner': 'Lifetime only counts time since this computer last started. Add your earlier usage so the total shows how long it has really been used. This reads your computer\'s own log; nothing leaves this computer.',
    'verify.result': 'Verified against {log} · {events:,.1f} h vs {awake:,.1f} h (difference {difference:.1f} h)',
    'verify.compared': 'Compared with {log} · {events:,.1f} h vs {awake:,.1f} h (difference {difference:.1f} h) · Review the difference',
    'verify.ledger_error': "{message} · Lifetime's record could not be refreshed",
    'rebuild.busy': 'Adding your earlier usage from the {log}…', 'rebuild.done': 'Earlier usage added from the {log}.',
    'rebuild.error': 'Could not add earlier usage. {error}',
    'rebuild.clock': "Could not add earlier usage: the Windows event log has times that do not line up (the clock was probably changed), so they cannot be counted reliably. Lifetime counts from now on.",'ledger.error': "Could not read Lifetime's record: {error}",
    'backup.saved': 'Local backup saved in:\n{path}', 'battery.capacity': 'Battery {index} capacity',
    'battery.power': 'Battery {index} power', 'disk': 'Disk {path}', 'gpu.field': '{prefix}{field}',
    'coverage.partial': '{days} days of dated intervals; partial coverage',
    'chart.values': '{count} values. {message}', 'chart.sessions': '{count:g} sessions',
    'scope': '{start} – {end} · {days:,} recorded days · {duration} awake',
    'live.began': 'Live tracking began {date}', 'sessions.retained': '{count} retained sessions · scoped by local start date',
    'live.latest': '{value:,.1f} {unit} · latest',
})
for index, name in enumerate(('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'), 1):
    EN['month.'+str(index)] = name
    VI['month.'+str(index)] = 'thg '+str(index)
for index, name in enumerate(('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')):
    EN['weekday.'+str(index)] = name
    VI['weekday.'+str(index)] = 'T'+str(index+2) if index < 6 else 'CN'
TABLES = {'en': EN, 'vi': VI}

_RELEASE_TECHNICIAN_VI = {
    'CSV output requires a .csv filename': 'Tên tệp CSV phải có đuôi .csv',
    'PDF output requires a .pdf filename': 'Tên tệp PDF phải có đuôi .pdf',
    'Earliest known date': 'Ngày sớm nhất được biết',
    'Date source': 'Nguồn của ngày',
    'OS installation date': 'Ngày cài đặt hệ điều hành',
    'System file timestamp': 'Dấu thời gian tệp hệ thống',
    'First observed by Lifetime': 'Lần đầu Lifetime ghi nhận',
    'Recovered OS log record': 'Bản ghi hệ điều hành khôi phục được',
    'Unknown source': 'Chưa rõ nguồn',
    'Days since this date: {days}': 'Số ngày kể từ mốc này: {days}',
    'Saved sources · measured: {measured} · OS logs: {imported} · manual baseline: {baseline}': 'Nguồn đã lưu · đo trực tiếp: {measured} · nhật ký: {imported} · số giờ nền nhập tay: {baseline}',
    'All-data sources · measured: {measured} · OS logs: {imported} · manual baseline: {baseline}': 'Nguồn toàn bộ dữ liệu · đo trực tiếp: {measured} · nhật ký: {imported} · số giờ nền nhập tay: {baseline}',
    'Directly measured awake time (seconds)': 'Thời gian hoạt động đo trực tiếp (giây)',
    'Reconstructed OS history (seconds)': 'Lịch sử khôi phục từ hệ điều hành (giây)',
    'The PDF page layout is unavailable.': 'Không xác định được bố cục trang PDF.',
    'PDF generation failed or produced no text.': 'Không tạo được PDF hoặc PDF không có nội dung chữ.',
    'Awake time is the time the computer was running and awake. It does not measure human activity, component health or remaining lifespan.': 'Thời gian hoạt động là thời gian máy tính đang chạy và không ngủ. Chỉ số này không đo hoạt động của người dùng, tình trạng linh kiện hoặc tuổi thọ còn lại.',
    'Could not open details': 'Không mở được chi tiết',
    'Live intervals are split across local days. Time since start-up is placed on days using saved sleep and wake records when available; otherwise it is estimated.': 'Các khoảng ghi trực tiếp được chia theo ngày địa phương. Thời gian khởi động được phân bổ theo bản ghi ngủ/thức còn lưu nếu có; nếu không, phân bố theo ngày là ước tính.',
    'History collection is disabled in Settings. Dated history and retained session records are excluded, including previously stored records.': 'Thu thập lịch sử đang tắt trong Cài đặt. Báo cáo không gồm lịch sử theo ngày hoặc phiên còn lưu, kể cả bản ghi cũ.',
    'History collection is disabled in Settings. Dated history is not included in reports.': 'Thu thập lịch sử đang tắt trong Cài đặt. Báo cáo không gồm lịch sử theo ngày.',
    'System information is disabled in Settings. Export does not change that choice.': 'Thông tin hệ thống đang tắt trong Cài đặt. Xuất báo cáo không thay đổi lựa chọn này.',
    'Daily allocation includes estimates; exact historical awake intervals cannot be recovered.': 'Phân bổ theo ngày có phần ước tính; không thể khôi phục chính xác các khoảng thức trong quá khứ.',
    'Verify only checks the time since the computer last started. It cannot prove complete historical coverage or recover missing OS logs.': 'Đối chiếu chỉ kiểm tra thời gian từ lần khởi động gần nhất. Kết quả không chứng minh lịch sử đầy đủ hoặc khôi phục được nhật ký hệ điều hành bị thiếu.',
    'Verify difference since last start-up (seconds)': 'Chênh lệch đối chiếu từ lần khởi động gần nhất (giây)',
    'Verify comparison captured': 'Thời điểm đối chiếu Kiểm tra',
    'Latest Verify (since last start-up only)': 'Đối chiếu gần nhất (chỉ từ lần khởi động gần nhất)',
}
VI.update(_RELEASE_TECHNICIAN_VI)
EN.update({key: key for key in _RELEASE_TECHNICIAN_VI})


# Optional technician identity, coverage and local report workflow.
_TECHNICIAN_VI = {'Department / location': 'Phòng ban / vị trí',
 'None recorded': 'Chưa có bản ghi',
'Export': 'Xuất',
 'Computer identity': 'Thông tin nhận diện máy tính',
 'Verify / Coverage details': 'Kiểm tra / Chi tiết dữ liệu',
 'Coverage details': 'Chi tiết dữ liệu',
 'Display name': 'Tên hiển thị',
 'Asset tag': 'Mã tài sản',
 'Department': 'Phòng ban',
 'Deployment date': 'Ngày đưa vào sử dụng',
 'Save': 'Lưu',
 'Preview': 'Xem trước',
 'Save PDF': 'Lưu PDF',
 'Save CSV': 'Lưu CSV',
 'All available history': 'Toàn bộ lịch sử hiện có',
 'Custom date range (inclusive)': 'Khoảng ngày tùy chọn (gồm cả hai ngày)',
 'Start date': 'Ngày bắt đầu',
 'End date': 'Ngày kết thúc',
 'Include identity fields': 'Kèm thông tin nhận diện máy tính',
 'Include system fields': 'Kèm thông tin hệ thống',
 'OS version': 'Phiên bản hệ điều hành',
 'Directly tracked': 'Ghi trực tiếp',
 'Reconstructed from OS records': 'Khôi phục từ bản ghi hệ điều hành',
 'Tracked and reconstructed': 'Ghi trực tiếp và khôi phục',
 'Gap observations': 'Các mốc gián đoạn đã ghi',
 'Preview the current selection before saving.': 'Xem trước lựa chọn hiện tại trước khi lưu.',
 'Preview ready. Choose PDF or CSV to save.': 'Đã có bản xem trước. Chọn PDF hoặc CSV để lưu.',
 'Saved data or permissions changed. Review the updated preview, then save again.': 'Dữ liệu hoặc quyền đã '
                                                                                    'thay đổi. Xem lại bản '
                                                                                    'xem trước mới rồi lưu '
                                                                                    'lại.',
 'System name': 'Tên hệ thống',
 'OS date estimate': 'Ngày hệ điều hành ước tính',
 'Optional. Leave blank to omit.': 'Không bắt buộc. Để trống nếu không dùng.',
 'Optional date: YYYY-MM-DD': 'Ngày không bắt buộc: YYYY-MM-DD',
 'Use an ISO date in YYYY-MM-DD format.': 'Nhập ngày theo định dạng YYYY-MM-DD.',
 'Start date must be on or before end date.': 'Ngày bắt đầu phải trước hoặc trùng ngày kết thúc.',
 'Could not save settings': 'Không thể lưu cài đặt',
 'Could not build report': 'Không thể tạo báo cáo',
 'Could not save report': 'Không thể lưu báo cáo',
 'Could not read coverage': 'Không thể đọc chi tiết dữ liệu',
 'Replace file?': 'Thay thế tệp?',
 'This file already exists. Replace it?': 'Tệp này đã tồn tại. Thay thế tệp?',
 'Report saved.': 'Đã lưu báo cáo.',
 'Choose fields, then preview or save. All fields are excluded by default.': 'Chọn thông tin, rồi xem trước '
                                                                             'hoặc lưu. Mặc định không kèm '
                                                                             'thông tin nào.',
 'Read-only evidence from saved records. No import or system scan is run.': 'Bằng chứng chỉ đọc từ dữ liệu '
                                                                            'đã lưu. Không nhập dữ liệu hoặc '
                                                                            'quét hệ thống.',
 'Source': 'Nguồn',
 'Recording started': 'Bắt đầu ghi nhận',
 'Earliest record': 'Bản ghi sớm nhất',
 'First record': 'Bản ghi đầu tiên',
 'Last record': 'Bản ghi cuối cùng',
 'Notes': 'Ghi chú',
 'Coverage: {source} · {status}': 'Dữ liệu: {source} · {status}',
 'Report export is disabled by consent settings.': 'Cài đặt đồng ý đang tắt chức năng xuất báo cáo.',
 'Choose both start and end dates.': 'Chọn cả ngày bắt đầu và ngày kết thúc.',
 'Start date must not follow end date.': 'Ngày bắt đầu không được sau ngày kết thúc.',
 'Computer Awake-Time Report': 'Báo cáo thời gian hoạt động của máy tính',
 'Lifetime version': 'Phiên bản Lifetime',
 'Created': 'Ngày tạo',
 'Selected period': 'Khoảng thời gian đã chọn',
 'All available data': 'Tất cả dữ liệu hiện có',
 'Selected system information': 'Thông tin hệ thống đã chọn',
 'OS release': 'Phiên bản hệ điều hành',
 'Awake time (seconds)': 'Thời gian hoạt động (giây)',
 'Manual baseline (seconds, not dated)': 'Số giờ nền nhập tay (giây, không có ngày)',
 'Retained session starts': 'Số lần bắt đầu phiên còn lưu',
 'Longest recorded session (seconds)': 'Phiên đã ghi dài nhất (giây)',
 'Times started (all available data)': 'Số lần khởi động đã ghi (tất cả dữ liệu)',
 'Coverage and sources': 'Phạm vi dữ liệu và nguồn',
 'Coverage': 'Phạm vi dữ liệu',
 'Recording began (proven timestamp)': 'Bắt đầu ghi (mốc thời gian có bằng chứng)',
 'Earliest recovered OS record': 'Bản ghi hệ điều hành sớm nhất khôi phục được',
 'First recorded date': 'Ngày có bản ghi đầu tiên',
 'Last recorded date': 'Ngày có bản ghi cuối cùng',
 'Limitations': 'Giới hạn',
 'Retained daily records': 'Bản ghi hằng ngày còn lưu',
 'Awake seconds': 'Số giây hoạt động',
 'Awake time means the computer was running and awake. It does not measure human activity, productivity, component health or remaining lifespan.': 'Thời '
                                                                                                                                                   'gian '
                                                                                                                                                   'thức '
                                                                                                                                                   'là '
                                                                                                                                                   'thời '
                                                                                                                                                   'gian '
                                                                                                                                                   'máy '
                                                                                                                                                   'tính '
                                                                                                                                                   'đang '
                                                                                                                                                   'chạy '
                                                                                                                                                   'và '
                                                                                                                                                   'không '
                                                                                                                                                   'ngủ. '
                                                                                                                                                   'Chỉ '
                                                                                                                                                   'số '
                                                                                                                                                   'này '
                                                                                                                                                   'không '
                                                                                                                                                   'đo '
                                                                                                                                                   'hoạt '
                                                                                                                                                   'động '
                                                                                                                                                   'của '
                                                                                                                                                   'người '
                                                                                                                                                   'dùng, '
                                                                                                                                                   'năng '
                                                                                                                                                   'suất, '
                                                                                                                                                   'tình '
                                                                                                                                                   'trạng '
                                                                                                                                                   'linh '
                                                                                                                                                   'kiện '
                                                                                                                                                   'hoặc '
                                                                                                                                                   'tuổi '
                                                                                                                                                   'thọ '
                                                                                                                                                   'còn '
                                                                                                                                                   'lại.',
 'Coverage cannot be proven complete. Missing or rotated logs, restricted permissions and time without tracking may leave unavailable history.': 'Không '
                                                                                                                                                 'thể '
                                                                                                                                                 'xác '
                                                                                                                                                 'nhận '
                                                                                                                                                 'dữ '
                                                                                                                                                 'liệu '
                                                                                                                                                 'đầy '
                                                                                                                                                 'đủ. '
                                                                                                                                                 'Nhật '
                                                                                                                                                 'ký '
                                                                                                                                                 'bị '
                                                                                                                                                 'thiếu '
                                                                                                                                                 'hoặc '
                                                                                                                                                 'xoay '
                                                                                                                                                 'vòng, '
                                                                                                                                                 'quyền '
                                                                                                                                                 'truy '
                                                                                                                                                 'cập '
                                                                                                                                                 'hạn '
                                                                                                                                                 'chế '
                                                                                                                                                 'và '
                                                                                                                                                 'thời '
                                                                                                                                                 'gian '
                                                                                                                                                 'không '
                                                                                                                                                 'ghi '
                                                                                                                                                 'có '
                                                                                                                                                 'thể '
                                                                                                                                                 'làm '
                                                                                                                                                 'thiếu '
                                                                                                                                                 'lịch '
                                                                                                                                                 'sử.',
 'OS dates are observations, not manufacture, purchase or true first-use dates.': 'Ngày từ hệ điều hành là '
                                                                                  'mốc quan sát, không phải '
                                                                                  'ngày sản xuất, mua hoặc '
                                                                                  'sử dụng thực sự đầu tiên.',
 'Daily history is retained for up to 400 days; only the last 100 sessions are retained. Previously saved dates are not relabeled after timezone changes.': 'Lịch '
                                                                                                                                                            'sử '
                                                                                                                                                            'hằng '
                                                                                                                                                            'ngày '
                                                                                                                                                            'được '
                                                                                                                                                            'giữ '
                                                                                                                                                            'tối '
                                                                                                                                                            'đa '
                                                                                                                                                            '400 '
                                                                                                                                                            'ngày; '
                                                                                                                                                            'chỉ '
                                                                                                                                                            'giữ '
                                                                                                                                                            '100 '
                                                                                                                                                            'phiên '
                                                                                                                                                            'gần '
                                                                                                                                                            'nhất. '
                                                                                                                                                            'Ngày '
                                                                                                                                                            'đã '
                                                                                                                                                            'lưu '
                                                                                                                                                            'không '
                                                                                                                                                            'đổi '
                                                                                                                                                            'khi '
                                                                                                                                                            'thay '
                                                                                                                                                            'đổi '
                                                                                                                                                            'múi '
                                                                                                                                                            'giờ.',
 'OS reconstruction is limited to retained accessible observations. Missing sleep/resume events, unclean ends and Windows Fast Startup can limit accuracy.': 'Việc '
                                                                                                                                                             'khôi '
                                                                                                                                                             'phục '
                                                                                                                                                             'chỉ '
                                                                                                                                                             'dựa '
                                                                                                                                                             'trên '
                                                                                                                                                             'bản '
                                                                                                                                                             'ghi '
                                                                                                                                                             'còn '
                                                                                                                                                             'lưu '
                                                                                                                                                             'và '
                                                                                                                                                             'truy '
                                                                                                                                                             'cập '
                                                                                                                                                             'được. '
                                                                                                                                                             'Thiếu '
                                                                                                                                                             'sự '
                                                                                                                                                             'kiện '
                                                                                                                                                             'ngủ/thức, '
                                                                                                                                                             'tắt '
                                                                                                                                                             'máy '
                                                                                                                                                             'không '
                                                                                                                                                             'rõ '
                                                                                                                                                             'ràng '
                                                                                                                                                             'và '
                                                                                                                                                             'Khởi '
                                                                                                                                                             'động '
                                                                                                                                                             'nhanh '
                                                                                                                                                             'Windows '
                                                                                                                                                             'có '
                                                                                                                                                             'thể '
                                                                                                                                                             'giảm '
                                                                                                                                                             'độ '
                                                                                                                                                             'chính '
                                                                                                                                                             'xác.',
 'No recorded data is available for the selected period. This is not evidence of zero usage.': 'Không có bản '
                                                                                               'ghi cho '
                                                                                               'khoảng thời '
                                                                                               'gian đã '
                                                                                               'chọn. Điều '
                                                                                               'này không '
                                                                                               'chứng minh '
                                                                                               'máy không '
                                                                                               'được sử '
                                                                                               'dụng.',
 'The requested range extends outside available recorded dates. Totals include only retained records in the range.': 'Khoảng '
                                                                                                                     'đã '
                                                                                                                     'chọn '
                                                                                                                     'vượt '
                                                                                                                     'ngoài '
                                                                                                                     'các '
                                                                                                                     'ngày '
                                                                                                                     'có '
                                                                                                                     'bản '
                                                                                                                     'ghi. '
                                                                                                                     'Tổng '
                                                                                                                     'chỉ '
                                                                                                                     'gồm '
                                                                                                                     'bản '
                                                                                                                     'ghi '
                                                                                                                     'còn '
                                                                                                                     'lưu '
                                                                                                                     'trong '
                                                                                                                     'khoảng '
                                                                                                                     'này.',
 'Missing dates within the selected range are unavailable, not assumed to be zero.': 'Các ngày thiếu trong '
                                                                                     'khoảng đã chọn là '
                                                                                     'không có dữ liệu, '
                                                                                     'không được coi là bằng '
                                                                                     'không.',
 'Manual baseline hours have no recorded dates and are excluded from period totals.': 'Số giờ nền nhập tay '
                                                                                      'không có ngày ghi và '
                                                                                      'được loại khỏi tổng '
                                                                                      'theo khoảng thời '
                                                                                      'gian.',
 'Session starts are counted from retained records. Exact session durations inside a selected period cannot be reconstructed from aggregate session counters.': 'Số '
                                                                                                                                                                'lần '
                                                                                                                                                                'bắt '
                                                                                                                                                                'đầu '
                                                                                                                                                                'phiên '
                                                                                                                                                                'được '
                                                                                                                                                                'đếm '
                                                                                                                                                                'từ '
                                                                                                                                                                'bản '
                                                                                                                                                                'ghi '
                                                                                                                                                                'còn '
                                                                                                                                                                'lưu. '
                                                                                                                                                                'Không '
                                                                                                                                                                'thể '
                                                                                                                                                                'khôi '
                                                                                                                                                                'phục '
                                                                                                                                                                'chính '
                                                                                                                                                                'xác '
                                                                                                                                                                'thời '
                                                                                                                                                                'lượng '
                                                                                                                                                                'phiên '
                                                                                                                                                                'trong '
                                                                                                                                                                'khoảng '
                                                                                                                                                                'đã '
                                                                                                                                                                'chọn '
                                                                                                                                                                'từ '
                                                                                                                                                                'bộ '
                                                                                                                                                                'đếm '
                                                                                                                                                                'tổng '
                                                                                                                                                                'hợp.',
 'The all-data awake total includes measured and imported counters, but excludes the separately listed manual baseline. Daily rows may cover a shorter retained span.': 'Tổng '
                                                                                                                                                                        'thời '
                                                                                                                                                                        'gian '
                                                                                                                                                                        'thức '
                                                                                                                                                                        'gồm '
                                                                                                                                                                        'bộ '
                                                                                                                                                                        'đếm '
                                                                                                                                                                        'đo '
                                                                                                                                                                        'trực '
                                                                                                                                                                        'tiếp '
                                                                                                                                                                        'và '
                                                                                                                                                                        'nhập '
                                                                                                                                                                        'từ '
                                                                                                                                                                        'nhật '
                                                                                                                                                                        'ký, '
                                                                                                                                                                        'nhưng '
                                                                                                                                                                        'không '
                                                                                                                                                                        'gồm '
                                                                                                                                                                        'số '
                                                                                                                                                                        'giờ '
                                                                                                                                                                        'nền '
                                                                                                                                                                        'nhập '
                                                                                                                                                                        'tay '
                                                                                                                                                                        'được '
                                                                                                                                                                        'liệt '
                                                                                                                                                                        'kê '
                                                                                                                                                                        'riêng. '
                                                                                                                                                                        'Bảng '
                                                                                                                                                                        'hằng '
                                                                                                                                                                        'ngày '
                                                                                                                                                                        'có '
                                                                                                                                                                        'thể '
                                                                                                                                                                        'chỉ '
                                                                                                                                                                        'bao '
                                                                                                                                                                        'phủ '
                                                                                                                                                                        'khoảng '
                                                                                                                                                                        'ngắn '
                                                                                                                                                                        'hơn.',
 'Only the first 90 daily rows are shown in the preview and PDF. CSV contains every selected retained daily row.': 'Bản '
                                                                                                                   'xem '
                                                                                                                   'trước '
                                                                                                                   'và '
                                                                                                                   'PDF '
                                                                                                                   'chỉ '
                                                                                                                   'hiển '
                                                                                                                   'thị '
                                                                                                                   '90 '
                                                                                                                   'ngày '
                                                                                                                   'đầu. '
                                                                                                                   'CSV '
                                                                                                                   'chứa '
                                                                                                                   'mọi '
                                                                                                                   'bản '
                                                                                                                   'ghi '
                                                                                                                   'hằng '
                                                                                                                   'ngày '
                                                                                                                   'còn '
                                                                                                                   'lưu '
                                                                                                                   'trong '
                                                                                                                   'khoảng '
                                                                                                                   'đã '
                                                                                                                   'chọn.',
 'Known awake-clock discontinuities were excluded from counting; their missing duration is unknown.': 'Các '
                                                                                                      'đoạn '
                                                                                                      'gián '
                                                                                                      'đoạn '
                                                                                                      'đồng '
                                                                                                      'hồ '
                                                                                                      'thức '
                                                                                                      'đã '
                                                                                                      'được '
                                                                                                      'loại '
                                                                                                      'khỏi '
                                                                                                      'phép '
                                                                                                      'đếm; '
                                                                                                      'không '
                                                                                                      'biết '
                                                                                                      'thời '
                                                                                                      'lượng '
                                                                                                      'bị '
                                                                                                      'thiếu.',
 'Clock changes were observed. Calendar attribution may be ambiguous; awake-clock totals were not adjusted using wall time.': 'Đã '
                                                                                                                              'phát '
                                                                                                                              'hiện '
                                                                                                                              'thay '
                                                                                                                              'đổi '
                                                                                                                              'đồng '
                                                                                                                              'hồ. '
                                                                                                                              'Việc '
                                                                                                                              'gán '
                                                                                                                              'ngày '
                                                                                                                              'có '
                                                                                                                              'thể '
                                                                                                                              'không '
                                                                                                                              'rõ '
                                                                                                                              'ràng; '
                                                                                                                              'tổng '
                                                                                                                              'thời '
                                                                                                                              'gian '
                                                                                                                              'thức '
                                                                                                                              'không '
                                                                                                                              'được '
                                                                                                                              'điều '
                                                                                                                              'chỉnh '
                                                                                                                              'theo '
                                                                                                                              'giờ '
                                                                                                                              'hệ '
                                                                                                                              'thống.',
 'No proven recording-start timestamp was saved by this installation.': 'Bản cài đặt này không lưu mốc bắt '
                                                                        'đầu ghi có bằng chứng.',
 'Known gap observations (timestamps; duration unknown)': 'Mốc quan sát gián đoạn (không rõ thời lượng)',
 'Dates use saved local calendar labels; creation time includes the current UTC offset. Durations are seconds, not proof of continuous coverage.': 'Ngày '
                                                                                                                                                   'dùng '
                                                                                                                                                   'nhãn '
                                                                                                                                                   'ngày '
                                                                                                                                                   'địa '
                                                                                                                                                   'phương '
                                                                                                                                                   'đã '
                                                                                                                                                   'lưu; '
                                                                                                                                                   'thời '
                                                                                                                                                   'điểm '
                                                                                                                                                   'tạo '
                                                                                                                                                   'có '
                                                                                                                                                   'độ '
                                                                                                                                                   'lệch '
                                                                                                                                                   'UTC '
                                                                                                                                                   'hiện '
                                                                                                                                                   'tại. '
                                                                                                                                                   'Thời '
                                                                                                                                                   'lượng '
                                                                                                                                                   'tính '
                                                                                                                                                   'bằng '
                                                                                                                                                   'giây, '
                                                                                                                                                   'không '
                                                                                                                                                   'chứng '
                                                                                                                                                   'minh '
                                                                                                                                                   'dữ '
                                                                                                                                                   'liệu '
                                                                                                                                                   'liên '
                                                                                                                                                   'tục.',
 'Recording-start and earliest recovered dates describe this installation. Period totals use only selected retained date buckets.': 'Ngày '
                                                                                                                                    'bắt '
                                                                                                                                    'đầu '
                                                                                                                                    'ghi '
                                                                                                                                    'và '
                                                                                                                                    'ngày '
                                                                                                                                    'khôi '
                                                                                                                                    'phục '
                                                                                                                                    'sớm '
                                                                                                                                    'nhất '
                                                                                                                                    'mô '
                                                                                                                                    'tả '
                                                                                                                                    'bản '
                                                                                                                                    'cài '
                                                                                                                                    'đặt '
                                                                                                                                    'này. '
                                                                                                                                    'Tổng '
                                                                                                                                    'theo '
                                                                                                                                    'khoảng '
                                                                                                                                    'thời '
                                                                                                                                    'gian '
                                                                                                                                    'chỉ '
                                                                                                                                    'dùng '
                                                                                                                                    'các '
                                                                                                                                    'ngày '
                                                                                                                                    'còn '
                                                                                                                                    'lưu '
                                                                                                                                    'đã '
                                                                                                                                    'chọn.'}
for _key, _text in _TECHNICIAN_VI.items():
    VI.setdefault(_key, _text)
    EN.setdefault(_key, _key)
EN['Location'] = 'Location'
VI['Location'] = 'Vị trí'
for _key, _text in {
    'Updating history': 'Đang cập nhật lịch sử',
    'History updated': 'Đã cập nhật lịch sử',
    'History update failed': 'Cập nhật lịch sử không thành công',
    'Calendar history could not be updated. Restart Lifetime to retry. Report export remains unavailable.': 'Không thể cập nhật lịch sử theo ngày. Khởi động lại Lifetime để thử lại. Chức năng xuất báo cáo hiện chưa khả dụng.',
}.items():
    EN[_key] = _key
    VI[_key] = _text
_SERVER_VI = {
    'Report scope': 'Phạm vi báo cáo',
    'One server device': 'Một máy tính trên máy chủ',
    'Fleet summary': 'Tổng hợp các máy tính',
    'Client observation time': 'Thời điểm ghi nhận trên máy tính',
    'Server receipt time': 'Thời điểm máy chủ nhận dữ liệu',
    'System reading time': 'Thời điểm ghi nhận thông tin hệ thống',
    'Selected devices': 'Số máy tính đã chọn',
    'Devices with available totals': 'Số máy có tổng thời gian khả dụng',
    'Devices with unavailable totals': 'Số máy không có tổng thời gian khả dụng',
    'Device awake seconds': 'Thời gian máy chạy và thức (giây)',
    'Device': 'Máy tính',
    'Server reports use the latest permitted client snapshot. Missing dates are unavailable, not evidence of zero usage.': 'Báo cáo máy chủ dùng bản ghi gần nhất được phép từ máy tính. Ngày thiếu dữ liệu là không khả dụng, không có nghĩa thời gian sử dụng bằng không.',
    'The server receipt time and the client observation time are different. A stopped report stream does not prove shutdown or sleep.': 'Thời điểm máy chủ nhận và thời điểm máy tính ghi nhận là khác nhau. Ngừng nhận báo cáo không chứng minh máy đã tắt hoặc ngủ.',
    'Daily labels use the recording computer’s saved local dates. Clock and timezone changes can affect calendar attribution.': 'Nhãn ngày dùng ngày địa phương đã lưu trên máy ghi nhận. Thay đổi đồng hồ và múi giờ có thể ảnh hưởng việc phân bổ theo ngày.',
    'Session statistics were not transmitted and are not available.': 'Thống kê phiên không được truyền và không khả dụng.',
    'Server retention removed earlier detail; the latest cumulative snapshot can describe a longer period than retained daily rows.': 'Chính sách lưu giữ trên máy chủ đã xóa chi tiết cũ; tổng tích lũy gần nhất có thể bao gồm khoảng thời gian dài hơn các ngày còn lưu.',
    'Fleet totals sum one latest snapshot per selected device, never overlapping cumulative snapshots. Devices without available totals are excluded from the known subtotal.': 'Tổng nhóm máy cộng một bản ghi tích lũy gần nhất của mỗi máy đã chọn. Các máy không có tổng khả dụng được loại khỏi tổng phụ đã biết.',
    'Fleet daily rows sum matching device-local date labels; different timezones do not describe one shared UTC day. Missing contributions remain unavailable.': 'Các dòng theo ngày cộng những nhãn ngày địa phương trùng nhau trên từng máy; các múi giờ khác nhau không đại diện cho cùng một ngày UTC. Phần dữ liệu thiếu vẫn không khả dụng.',
    'Coverage is unknown. Summing devices does not establish continuous recording, productivity or hardware condition.': 'Chưa xác định độ bao phủ. Cộng dữ liệu các máy không chứng minh việc ghi nhận liên tục, năng suất hay tình trạng phần cứng.',
}
for _key, _text in _SERVER_VI.items():
    EN[_key] = _key
    VI[_key] = _text
_COMPANY_VI = {
    'Company server': 'Máy chủ công ty',
    'Reading local status…': 'Đang đọc trạng thái trên máy…',
    'Enrollment bundle': 'Gói đăng ký',
    'Open enrollment file': 'Mở tệp đăng ký',
    'Send basic system information': 'Gửi thông tin hệ thống cơ bản',
    'Pair computer': 'Ghép nối máy tính',
    'Check enrollment': 'Kiểm tra đăng ký',
    'Refresh local status': 'Làm mới trạng thái trên máy',
    'Stop company reporting': 'Dừng báo cáo về công ty',
    'Reporting enabled': 'Đã bật báo cáo',
    'Reporting status': 'Trạng thái báo cáo',
    'Last successful sync': 'Lần đồng bộ thành công gần nhất',
    'Pending records': 'Bản ghi chờ gửi',
    'Discarded records': 'Bản ghi đã loại bỏ',
    'Company reporting could not be updated. Check administrator access, the server address and its connection, then try again.': 'Không thể cập nhật báo cáo về công ty. Kiểm tra quyền quản trị, địa chỉ máy chủ và kết nối rồi thử lại.',
    'All files (*)': 'Tất cả tệp (*)',
    'disabled': 'Đã tắt', 'enrolling': 'Đang gửi yêu cầu kết nối',
    'pending': 'Chờ phê duyệt', 'approved': 'Đã phê duyệt', 'connected': 'Đã kết nối',
    'offline': 'Ngoại tuyến', 'denied': 'Bị từ chối', 'revoked': 'Đã thu hồi',
}
for _key, _text in _COMPANY_VI.items():
    EN[_key] = _key
    VI[_key] = _text
for _key, _text in {
    'Server exports use current reporting permissions. Optional system fields remain excluded unless selected and permitted by the computer.': 'Báo cáo máy chủ dùng quyền báo cáo hiện tại. Các trường hệ thống tùy chọn chỉ được kèm khi đã chọn và máy tính cho phép.',
    'Preparing report…': 'Đang chuẩn bị báo cáo…',
    'Could not export the server report. Check the local service, administrator access and output folder, then try again.': 'Không thể xuất báo cáo máy chủ. Kiểm tra dịch vụ trên máy, quyền quản trị và thư mục lưu rồi thử lại.',
    'unreachable': 'Không thể liên lạc', 'conflict': 'Xung đột dữ liệu',
}.items():
    EN[_key] = _key
    VI[_key] = _text

SERVER_DASHBOARD_VI = {'Lifetime Server': 'Lifetime Server',
    'Awake time per day': 'Thời gian hoạt động mỗi ngày', '14 days': '14 ngày', '30 days': '30 ngày',
    'Disk free': 'Dung lượng trống',
    'History is off on this computer': 'Máy tính này đang tắt lịch sử',
    'System readings are off on this computer': 'Máy tính này đang tắt thông tin hệ thống',
    'Health readings are off on this computer': 'Máy tính này đang tắt thông tin sức khỏe',
    'No activity history reported yet': 'Chưa nhận được lịch sử hoạt động',
    'No health details reported yet': 'Chưa nhận được chi tiết sức khỏe',
    'Started {boots} times · current session {current} · longest session {longest}': 'Khởi động {boots} lần · phiên hiện tại {current} · phiên dài nhất {longest}',
    'No awake time recorded yet': 'Chưa ghi nhận thời gian hoạt động',
    'Average {average} a day · longest day {longest} on {day}': 'Trung bình {average} mỗi ngày · ngày dài nhất {longest} vào {day}',
    'Live': 'Trực tiếp', '24 hours': '24 giờ', 'Performance range': 'Khoảng hiệu suất',
    'Live: {seconds}s ago': 'Trực tiếp: {seconds} giây trước',
    'Last seen: {seconds}s ago': 'Kết nối cuối: {seconds} giây trước',
 'Devices': 'Thiết bị',
 'Administration': 'Quản trị',
 'Refresh': 'Làm mới',
 'Language': 'Ngôn ngữ',
 'Local administration': 'Quản trị cục bộ',
 'Loading…': 'Đang tải…',
 'Ready': 'Sẵn sàng',
 'Request failed. Check the service, admin port and administrator permissions, then refresh.': 'Yêu '
                                                                                               'cầu '
                                                                                               'thất '
                                                                                               'bại. '
                                                                                               'Kiểm '
                                                                                               'tra '
                                                                                               'dịch '
                                                                                               'vụ, '
                                                                                               'cổng '
                                                                                               'quản '
                                                                                               'trị '
                                                                                               'và '
                                                                                               'quyền '
                                                                                               'quản '
                                                                                               'trị '
                                                                                               'viên, '
                                                                                               'rồi '
                                                                                               'làm '
                                                                                               'mới.',
 'Working…': 'Đang xử lý…',
 'Changes saved.': 'Đã lưu thay đổi.',
 'Loaded devices': 'Thiết bị đã tải',
 'Reporting': 'Đang báo cáo',
 'Report overdue': 'Quá hạn báo cáo',
 'Pending approval': 'Chờ phê duyệt',
 'Revoked': 'Đã thu hồi',
 'Approved': 'Đã phê duyệt',
 'Denied': 'Đã từ chối',
 'Unavailable': 'Chưa có dữ liệu',
 'All statuses': 'Tất cả trạng thái',
 'Search': 'Tìm kiếm',
 'Group': 'Nhóm',
 'Page size': 'Số dòng mỗi trang',
 'Previous': 'Trước',
 'Next': 'Tiếp',
 'Last report received': 'Lần nhận báo cáo gần nhất',
 'Computer ID': 'Mã máy tính',
 'Select a computer to view its usage, permissions and server-owned identity.': 'Chọn máy tính để xem thời gian sử dụng, quyền và thông tin do máy chủ quản lý.',
 'Computer details': 'Chi tiết máy tính',
 'Server-owned identity': 'Thông tin do máy chủ quản lý',
 'Save identity': 'Lưu thông tin',
 'Revoke credential': 'Thu hồi thông tin xác thực',
 'Enrollment requests': 'Yêu cầu đăng ký',
 'Approve': 'Phê duyệt',
 'Deny': 'Từ chối',
 'Export selected': 'Xuất các máy đã chọn',
 'Export fleet': 'Xuất toàn bộ máy tính',
 'Usage snapshots (hours; never added together)': 'Thời gian tích lũy (giờ; không cộng các bản '
                                                  'chụp)',
 'Measured': 'Đo trực tiếp',
 'Imported': 'Nhập từ nhật ký',
 'Baseline': 'Ước tính ban đầu',
 'Coverage': 'Độ đầy đủ',
 'Unknown': 'Chưa xác định',
 'Not available': 'Chưa có dữ liệu',
 'Optional system readings': 'Thông tin hệ thống tùy chọn',
 'Not permitted': 'Không được cho phép',
 'Observed at': 'Thời điểm ghi nhận',
 'Received at': 'Thời điểm nhận',
 'Current report': 'Báo cáo hiện tại',
 'Stale report': 'Báo cáo đã cũ',
 'Reading age is unknown.': 'Chưa xác định độ cũ của thông tin.',
 'Optional history': 'Lịch sử tùy chọn',
 'Available': 'Có dữ liệu',
 'Pair a computer': 'Ghép nối máy tính',
 'Server HTTPS address': 'Địa chỉ HTTPS của máy chủ',
 'Code lifetime (seconds)': 'Thời hạn mã (giây)',
 'Allowed uses': 'Số lần sử dụng',
 'Create enrollment bundle': 'Tạo gói đăng ký',
 'Enrollment bundle': 'Gói đăng ký',
 'Share this bundle only with the technician pairing the computer. Approval is still required.': 'Chỉ chia sẻ gói này với kỹ thuật viên đang kết nối máy tính. Vẫn cần phê duyệt.',
 'Expires at {time}': 'Hết hạn lúc {time}',
 'Expires {date} at {time}': 'Hết hạn ngày {date} lúc {time}',
 'Use this instead when the computer cannot find the server automatically.': 'Dùng dòng này khi máy tính không tự tìm thấy máy chủ.',
 'Retention': 'Lưu giữ dữ liệu',
 'Delete records before (UTC)': 'Xóa bản ghi trước ngày (UTC)',
 'Apply retention': 'Áp dụng thời hạn lưu',
 'Backup and restore': 'Sao lưu và khôi phục',
 'Save backup': 'Lưu bản sao',
 'Restore backup': 'Khôi phục bản sao',
 'Continue?': 'Tiếp tục?',
 'Continue': 'Tiếp tục',
 'Found {server}': 'Đã tìm thấy {server}',
 'Waiting for the server': 'Đang chờ máy chủ',
 'The server removed this computer. Ask for a new code on the server to connect again.': 'Máy chủ đã gỡ máy tính này. Hãy lấy mã mới trên máy chủ để kết nối lại.',
 'Delete older records permanently? Latest totals remain, with their original timestamps.': 'Xóa '
                                                                                            'vĩnh '
                                                                                            'viễn '
                                                                                            'các '
                                                                                            'bản '
                                                                                            'ghi '
                                                                                            'cũ? '
                                                                                            'Tổng '
                                                                                            'gần '
                                                                                            'nhất '
                                                                                            'vẫn '
                                                                                            'được '
                                                                                            'giữ '
                                                                                            'cùng '
                                                                                            'thời '
                                                                                            'điểm '
                                                                                            'gốc.',
 'Identity is invalid. Use an optional YYYY-MM-DD deployment date.': 'Thông tin không hợp lệ. Ngày '
                                                                     'đưa vào sử dụng là tùy chọn, '
                                                                     'theo dạng NNNN-TT-NN.',
 'SQLite backups (*.sqlite *.db);;All files (*)': 'Bản sao SQLite (*.sqlite *.db);;Tất cả tệp (*)',
 'Export could not open. Check the reporting components and try again.': 'Không thể mở chức năng '
                                                                         'xuất. Kiểm tra các thành '
                                                                         'phần báo cáo rồi thử '
                                                                         'lại.',
 'Pending requests': 'Yêu cầu đang chờ',
 'System': 'Hệ thống',
 'Release': 'Phiên bản hệ thống',
 'Machine': 'Kiến trúc',
 'Python version': 'Phiên bản Python',
 'Reporting interval (seconds)': 'Khoảng thời gian báo cáo (giây)',
 'Save reporting interval': 'Lưu khoảng thời gian báo cáo',
 'Request fresh report': 'Yêu cầu báo cáo mới',
 'A request is delivered on the next sync; this does not contact or control the computer.': 'Yêu '
                                                                                            'cầu '
                                                                                            'được '
                                                                                            'gửi '
                                                                                            'trong '
                                                                                            'lần '
                                                                                            'đồng '
                                                                                            'bộ '
                                                                                            'tiếp '
                                                                                            'theo; '
                                                                                            'không '
                                                                                            'kết '
                                                                                            'nối '
                                                                                            'hoặc '
                                                                                            'điều '
                                                                                            'khiển '
                                                                                            'máy '
                                                                                            'tính.',
 'No pending requests': 'Không có yêu cầu đang chờ',
 'Configuration': 'Cấu hình',
 'Current boundary (UTC): {time}': 'Mốc lưu giữ hiện tại (UTC): {time}',
 'All computers': 'Tất cả máy tính',
 'Fleet overview; independent of the computer search and filters.': 'Tổng quan toàn bộ máy tính; độc lập với tìm kiếm và bộ lọc.',
 'Latest cumulative snapshots (hours)': 'Tổng tích lũy gần nhất (giờ)',
 'One latest snapshot per computer; snapshots are not added over time.': 'Một bản chụp gần nhất cho mỗi máy tính; không cộng các bản chụp theo thời gian.',
 '{known} computers have snapshots; {unknown} have none. Totals include only computers with a snapshot.': '{known} máy tính có bản chụp; {unknown} máy chưa có. Tổng chỉ gồm các máy có bản chụp.',
 'Recent events': 'Sự kiện gần đây',
 'Event': 'Sự kiện',
 'Time': 'Thời điểm',
 'Report received': 'Đã nhận báo cáo',
 'Enrollment pending': 'Đăng ký đang chờ',
 'Service available · schema {version} · server time {time}': 'Dịch vụ khả dụng · cấu trúc '
                                                              '{version} · thời gian máy chủ '
                                                              '{time}',
 'Overview unavailable. Showing the last successful data, if any.': 'Không tải được tổng quan. '
                                                                    'Hiển thị dữ liệu tải thành '
                                                                    'công gần nhất, nếu có.'}
for _key, _text in SERVER_DASHBOARD_VI.items():
    EN.setdefault(_key, _key)
    VI.setdefault(_key, _text)

# Desktop dashboard shell. Source keys remain stable for instant relabeling.
DASHBOARD_VI = {
    'Activity': 'Hoạt động',
    'Reports': 'Báo cáo',
    'Last 14 days': '14 ngày gần đây',
    'Last 14 days; open Activity': '14 ngày gần đây; mở Hoạt động',
    'View Activity': 'Xem hoạt động',
    'How this is counted': 'Cách tính thời gian',
    'Hide counting details': 'Ẩn chi tiết cách tính',
    'Reports and details for this computer.': 'Báo cáo và thông tin chi tiết về máy tính này.',
    'Save a report of recorded awake time and coverage.': 'Lưu báo cáo thời gian hoạt động đã ghi và phạm vi dữ liệu.',
    'Manage the computer name, asset tag and owner.': 'Quản lý tên máy tính, mã tài sản và chủ sở hữu.',
    'Review sources, dates and gaps in the record.': 'Xem nguồn, mốc thời gian và khoảng trống trong dữ liệu.',
    'Open': 'Mở',
}
EN.update({key: key for key in DASHBOARD_VI})
VI.update(DASHBOARD_VI)


# R2 System and Activity additions; existing catalogue entries stay unchanged.
_R2_VI = {
    'Show data': 'Hiện dữ liệu',
    'Show data for all charts': 'Hiện dữ liệu của mọi biểu đồ',
    'Show data for all live series': 'Hiện dữ liệu của mọi chuỗi trực tiếp',
    'Chart; use Show data for underlying numbers': 'Biểu đồ; chọn Hiện dữ liệu để xem số liệu',
    'Recorded days': 'Ngày đã ghi',
    'Awake': 'Hoạt động',
}
VI.update(_R2_VI)
EN.update({key: key for key in _R2_VI})

VI.update({'Total GPU power': 'Tổng công suất GPU'})
EN.update({'Total GPU power': 'Total GPU power'})

_TRAY_VI = {'Connecting securely': 'Đang kết nối an toàn', "Sending this computer's details": 'Đang gửi thông tin máy tính', 'Server found': 'Đã tìm thấy máy chủ', 'Almost done': 'Sắp xong', 'Quit': 'Thoát', 'Still running here. Right-click the icon to quit.': 'Ứng dụng vẫn đang chạy ở đây. Nhấp chuột phải vào biểu tượng để thoát.'}
VI.update(_TRAY_VI)
EN.update({key: key for key in _TRAY_VI})

_HEALTH_VI = {
    'Sleep is grouped into local noon-to-noon nights. Only completed sleep intervals are included.': 'Thời gian ngủ được nhóm theo đêm từ trưa hôm trước đến trưa hôm sau theo giờ địa phương. Chỉ tính các khoảng ngủ đã kết thúc.',
    '{count} events': '{count} sự kiện',
    '{count} cycles': '{count} chu kỳ',
    'Health': 'Sức khỏe',
    'Health and stability': 'Sức khỏe và độ ổn định',
    "Reads shutdown and sleep events, battery capacity and the computer's model and key dates.": 'Đọc sự kiện tắt máy và ngủ, dung lượng pin, mẫu máy và các mốc ngày của máy.',
    "Health readings are off. Turn on Health and stability in Settings to see shutdown and sleep events, battery condition and the computer's model and key dates.": 'Đang tắt dữ liệu sức khỏe. Bật Sức khỏe và độ ổn định trong Cài đặt để xem sự kiện tắt máy và ngủ, tình trạng pin, mẫu máy và các mốc ngày của máy.',
    'Open Settings': 'Mở Cài đặt',
    'Health date range': 'Khoảng thời gian sức khỏe',
    'Stability': 'Độ ổn định',
    'Sleep': 'Giấc ngủ',
    'Unexpected shutdowns and crashes per week': 'Lần tắt máy bất ngờ và lỗi hệ thống mỗi tuần',
    'New batteries can exceed their design capacity, so health can be above 100%.': 'Pin mới có thể vượt dung lượng thiết kế, vì vậy sức khỏe pin có thể trên 100%.',
    'Full charge capacity over time': 'Dung lượng sạc đầy theo thời gian',
    'On battery · hours per day': 'Dùng pin · giờ mỗi ngày',
    'Plugged in · hours per day': 'Cắm điện · giờ mỗi ngày',
    'Sleep/wake cycles per day': 'Chu kỳ ngủ/thức mỗi ngày',
    'Time asleep per day': 'Thời gian ngủ mỗi ngày',
    'Retained system logs may be incomplete. Missing readings stay unavailable; sleep averages include completed intervals only.': 'Nhật ký hệ thống lưu lại có thể không đầy đủ. Dữ liệu thiếu được để trống; trung bình giấc ngủ chỉ tính các khoảng đã kết thúc.',
    'Health reading unavailable. Check system log access and try again later.': 'Chưa đọc được dữ liệu sức khỏe. Kiểm tra quyền truy cập nhật ký hệ thống và thử lại sau.',
    'Model {model} · BIOS {bios} · OS installed {installed}': 'Mẫu máy {model} · BIOS {bios} · Cài hệ điều hành {installed}',
    'Linux OS install dates are estimates from system file dates.': 'Ngày cài Linux được ước tính từ ngày của tệp hệ thống.',
    'Computer model and dates unavailable': 'Chưa có thông tin mẫu máy và các mốc ngày',
    '{shutdowns} unexpected shutdowns · {crashes} crashes': '{shutdowns} lần tắt máy bất ngờ · {crashes} lần lỗi hệ thống',
    'Unexpected shutdown': 'Tắt máy bất ngờ',
    'Crash': 'Lỗi hệ thống',
    'No unexpected shutdowns or crashes in retained logs for this range': 'Không có lần tắt máy bất ngờ hay lỗi hệ thống trong nhật ký lưu lại ở khoảng này',
    'No battery in this computer': 'Máy tính này không có pin',
    'Cycles {cycles} · Design {design} {unit} · Full {full} {unit} · {chemistry}': 'Chu kỳ {cycles} · Thiết kế {design} {unit} · Sạc đầy {full} {unit} · {chemistry}',
    'Chemistry unavailable': 'Chưa có loại hóa học',
    'Multiple batteries: showing the first pack': 'Có nhiều pin: đang hiển thị pin đầu tiên',
    'Average sleep per night · {hours} h': 'Thời gian ngủ trung bình mỗi đêm · {hours} giờ',
    'Unstable': 'Không ổn định',
    'Crashes in 30 days': 'Lỗi hệ thống trong 30 ngày',
    'Unexpected shutdowns in 30 days': 'Tắt máy bất ngờ trong 30 ngày',
    'Battery health': 'Sức khỏe pin',
    'Cycle count': 'Số chu kỳ',
    'Last crash': 'Lần lỗi hệ thống gần nhất',
    'Health summary is shared only when Health and stability is enabled.': 'Chỉ chia sẻ tóm tắt sức khỏe khi bật Sức khỏe và độ ổn định.',
}
VI.update(_HEALTH_VI)
EN.update({key: key for key in _HEALTH_VI})

# Server edition and the shared, small Company server connection dialog.
SERVER_S1_VI = {
    'Check the connection code and server address.': 'Kiểm tra mã kết nối và địa chỉ máy chủ.',
    'Awake on selected date': 'Chạy trong ngày đã chọn',
    'Organisation': 'Tổ chức',
    'Save organisation': 'Lưu tên tổ chức',
    'Computer access': 'Quyền kết nối của máy tính',
    'Choose computer': 'Chọn máy tính',
    'Select a computer in Devices.': 'Chọn một máy tính trong mục Thiết bị.',
    'Revoke selected computer': 'Thu hồi máy tính đã chọn',
    'Re-enable selected computer': 'Cho phép máy tính đã chọn kết nối lại',
    'Re-enabling requires a new invitation and approval on the same computer. Its computer ID and history are kept.': 'Để kết nối lại, cần lời mời mới và phê duyệt trên cùng máy tính. Mã máy tính và lịch sử được giữ nguyên.',
    'Local network': 'Mạng nội bộ',
    'Server unavailable': 'Không kết nối được máy chủ',
    'Server running': 'Máy chủ đang chạy',
    'Export report': 'Xuất báo cáo',
    'Fleet overview': 'Tổng quan máy tính',
    'Report date': 'Ngày báo cáo',
    'Today · {date}': 'Hôm nay · {date}',
    'Add device': 'Thêm thiết bị',
    'Awake today': 'Chạy hôm nay',
    'Review': 'Xem xét',
    'No computers yet. Add device': 'Chưa có máy tính nào. Thêm thiết bị',
    'Search computers': 'Tìm máy tính',
    'All groups': 'Tất cả nhóm',
    'Computer list': 'Danh sách máy tính',
    'Device': 'Thiết bị',
    'Last report': 'Báo cáo gần nhất',
    'Daily awake hours': 'Giờ chạy mỗi ngày',
    'Last 7 days': '7 ngày qua',
    'Last 30 days': '30 ngày qua',
    'Chart range': 'Khoảng thời gian biểu đồ',
    "Known daily contributions only · each computer's local dates": 'Chỉ tính dữ liệu đã nhận · theo ngày trên từng máy',
    'No dated reports yet': 'Chưa có báo cáo theo ngày',
    'Local server: {name}': 'Máy chủ nội bộ: {name}',
    'Updated {time}': 'Cập nhật lúc {time}',
    'Check the reporting connection': 'Kiểm tra kết nối gửi báo cáo',
    '{count} reporting computers': '{count} máy tính có dữ liệu',
    'Administrator review required': 'Cần quản trị viên xét duyệt',
    'Showing {start} to {end} of {total} computers': 'Hiển thị {start} đến {end} trên {total} máy tính',
    '{hours} h {minutes:02} m': '{hours} giờ {minutes:02} phút',
    '{hours} h': '{hours} giờ',
    'h': 'g',
    '{n} s ago': '{n} giây trước',
    '{n} min ago': '{n} phút trước',
    '{n} h ago': '{n} giờ trước',
    'Open computer': 'Mở máy tính',
    'Select a computer to view its recorded usage.': 'Chọn máy tính để xem thời gian hoạt động đã ghi nhận.',
    'No group': 'Chưa có nhóm',
    'Recorded awake time': 'Thời gian chạy đã ghi nhận',
    'Client version': 'Phiên bản ứng dụng',
    'Partial': 'Một phần',
    'Tracking since': 'Bắt đầu ghi nhận',
    'Selected date': 'Ngày đã chọn',
    'Selected date awake time, out of 24 hours': 'Thời gian chạy ngày đã chọn, tối đa 24 giờ',
    'Synchronized': 'Đã đồng bộ',
    'Awaiting approval': 'Đang chờ phê duyệt',
    'Invitation used': 'Đã dùng lời mời',
    'Review pending approvals': 'Xem yêu cầu chờ phê duyệt',
    'Invitations': 'Lời mời kết nối',
    'Invitation': 'Lời mời',
    'Expires': 'Hết hạn',
    'Uses left': 'Lượt còn lại',
    'Wrong guesses': 'Lần nhập sai',
    'Revoke invitation': 'Thu hồi lời mời',
    'Expires in (hours)': 'Hết hạn sau (giờ)',
    'Locked': 'Đã khóa',
    'Used': 'Đã dùng hết',
    'Expired': 'Đã hết hạn',
    'Active': 'Còn hiệu lực',
    'Made by JPL Innovation': 'Phát triển bởi JPL Innovation',
    'Connect a computer': 'Kết nối máy tính',
    'Connection code': 'Mã kết nối',
    'Copy code': 'Sao chép mã',
    'Connection line': 'Dòng kết nối',
    'Copy connection line': 'Sao chép dòng kết nối',
    'Allowed uses: {uses}': 'Số lượt được dùng: {uses}',
    'Approval is required. Share privately. Revoke this invitation in Administration.': 'Cần được phê duyệt. Chỉ chia sẻ riêng. Thu hồi lời mời trong Quản trị.',
    'Done': 'Xong',
    'Company servers': 'Máy chủ công ty',
    'Find local servers': 'Tìm máy chủ nội bộ',
    'Server address': 'Địa chỉ máy chủ',
    'Connection code or full connection line': 'Mã kết nối hoặc dòng kết nối đầy đủ',
    'Advanced: import an enrollment bundle': 'Nâng cao: nhập gói đăng ký',
}
for _key, _text in SERVER_S1_VI.items():
    EN.setdefault(_key, _key)
    VI.setdefault(_key, _text)
    SERVER_DASHBOARD_VI.setdefault(_key, _text)

# Server Devices and Administration presentation (S2). Existing translations stay intact.
SERVER_S2_VI = {
    'Server name': 'Tên máy chủ',
    'Apply': 'Áp dụng',
    'Pending approvals': 'Yêu cầu chờ phê duyệt',
    'No pending approvals': 'Không có yêu cầu chờ phê duyệt',
    'Server': 'Máy chủ',
    'Listening address': 'Địa chỉ lắng nghe',
    'Certificate SHA-256': 'SHA-256 chứng chỉ',
    'Data retention': 'Lưu giữ dữ liệu',
    'About': 'Giới thiệu',
    'Actions': 'Thao tác',
    'Revoke': 'Thu hồi',
    'Page {page}': 'Trang {page}',
}
for _key, _text in SERVER_S2_VI.items():
    EN.setdefault(_key, _key)
    VI.setdefault(_key, _text)
    SERVER_DASHBOARD_VI.setdefault(_key, _text)

# Count wording that reads correctly for 0, 1 and many in English and Vietnamese (no plural rules needed).
EN.update({
    '{shutdowns} unexpected shutdowns · {crashes} crashes': 'Unexpected shutdowns: {shutdowns} · Crashes: {crashes}',
    '{count} events': 'Events: {count}',
    '{count} cycles': 'Cycles: {count}',
})

EN.update({key: key for key in _POWER_VI})
VI.update(_POWER_VI)

# Server shell and computer cards (P2a).
SERVER_P2_VI = {
    'User: {user}': 'Người dùng: {user}',
    'This Server': 'Máy chủ này', 'Needs attention': 'Cần chú ý',
    'Online': 'Trực tuyến', 'Offline': 'Ngoại tuyến',
    'Sort by problem': 'Sắp xếp theo vấn đề', 'Sort by name': 'Sắp xếp theo tên',
    'Sort by last seen': 'Sắp xếp theo lần kết nối cuối', 'Sort computers': 'Sắp xếp máy tính',
    'Computer specifications': 'Thông số máy tính', 'Manufacturer': 'Nhà sản xuất',
    'Model': 'Mẫu máy', 'Cores / threads': 'Nhân / luồng', 'Graphics': 'Đồ họa',
    'Storage': 'Ổ lưu trữ',
    'Battery design capacity': 'Dung lượng pin thiết kế',
    'Performance in the last 24 hours': 'Hiệu suất trong 24 giờ qua',
    'Commands are delivered on the next sync. Turn on uses Wake-on-LAN.':
        'Lệnh được gửi vào lần đồng bộ tiếp theo. Bật máy sử dụng Wake-on-LAN.',
    'Select a computer first.': 'Hãy chọn một máy tính trước.',
    'A request is in progress.': 'Đang xử lý yêu cầu.',
    'The owner has not allowed remote power control.': 'Chủ máy chưa cho phép điều khiển nguồn từ xa.',
    'A group with that name already exists.': 'Đã có một nhóm mang tên này.',
    'This computer does not allow remote sleep, restart, shut down or turn on. Turn on power control on that computer first.':
        'Máy tính này không cho phép ngủ, khởi động lại, tắt hoặc bật máy từ xa. Hãy bật điều khiển nguồn trên máy đó trước.',
    'This computer must be approved first.': 'Máy tính này cần được phê duyệt trước.',
    'A command is already pending for this computer.': 'Máy tính này đã có lệnh đang chờ.',
    'Turn on is available when the computer is offline.': 'Có thể bật máy khi máy tính ngoại tuyến.',
    'No network adapter address has been recorded.': 'Chưa ghi nhận địa chỉ bộ điều hợp mạng.',
    'This computer needs a recent sync first.': 'Máy tính này cần đồng bộ gần đây trước.',
    'Command status: {status}': 'Trạng thái lệnh: {status}',
    'Low disk space': 'Sắp hết dung lượng ổ đĩa', 'High temperature': 'Nhiệt độ cao',
    'Command pending': 'Lệnh đang chờ', 'CPU: {cpu} · RAM: {ram}': 'CPU: {cpu} · RAM: {ram}',
    '{metric}: {value}': '{metric}: {value}',
    'Disk free: {disk} · Battery: {battery} · Last seen: {seen}':
        'Ổ đĩa trống: {disk} · Pin: {battery} · Kết nối cuối: {seen}',
    'Actions for {name}': 'Thao tác cho {name}',
    '{name}: {free} free of {total}': '{name}: trống {free} trên {total}',
    'Uptime': 'Thời gian đã chạy', 'Last seen': 'Kết nối cuối',
    'CPU usage': 'Mức sử dụng CPU', 'Memory used': 'Bộ nhớ đã dùng',
    'Database size': 'Kích thước cơ sở dữ liệu', 'Just now': 'Vừa xong',
    'power.sleep': 'Ngủ', 'Restart': 'Khởi động lại', 'Shut down': 'Tắt máy', 'Turn on': 'Bật máy',
    'command.pending': 'Đang chờ', 'command.delivered': 'Đã gửi',
    'command.done': 'Đã chấp nhận thực thi', 'command.failed': 'Thất bại',
    'command.expired': 'Đã hết hạn', 'command.cancelled': 'Đã hủy',
}
for _key, _text in SERVER_P2_VI.items():
    EN.setdefault(_key, _key)
    VI.setdefault(_key, _text)
    SERVER_DASHBOARD_VI.setdefault(_key, _text)

# The power action, not the Health page's noun 'Sleep' (Giấc ngủ): its own key so it always reads Ngủ.
EN.update({'power.sleep': 'Sleep', 'command.pending': 'Pending', 'command.delivered': 'Delivered', 'command.done': 'Accepted for execution', 'command.failed': 'Failed', 'command.expired': 'Expired', 'command.cancelled': 'Cancelled'})

# P4 connection flow: English keys are shared by desktop and server.
CONNECT_VI = {
    'Connect': 'Kết nối',
    'Connect to a server': 'Kết nối với máy chủ',
    'Enter the code shown on the server.': 'Nhập mã hiển thị trên máy chủ.',
    "For workplace computers: connect to your IT team's Lifetime Server. Not needed for personal use — Lifetime works fully on its own.": 'Dành cho máy tính ở nơi làm việc: kết nối với Lifetime Server của bộ phận IT. Không cần nếu bạn dùng cá nhân — Lifetime vẫn hoạt động đầy đủ khi không kết nối.',
    'Refreshing…': 'Đang làm mới…',
    'Online now': 'Đang trực tuyến',
    '{connected} of {total} computers online': '{connected} trên {total} máy tính đang trực tuyến',
    'Hardware': 'Phần cứng',
    'Right now': 'Hiện tại',
    'Connected computers': 'Máy tính đã kết nối',
    'Command sent': 'Đã gửi lệnh',
    'Command delivered': 'Lệnh đã đến máy',
    'Command finished': 'Lệnh đã hoàn tất',
    'Command failed': 'Lệnh thất bại',
    'Command cancelled': 'Đã hủy lệnh',
    'Command expired': 'Lệnh đã hết hạn',
    'Turn on sent': 'Đã gửi lệnh bật máy',
    'On this computer': 'Trên máy này',
    'Type that code below.': 'Nhập mã đó vào ô bên dưới.',
    'On the server': 'Trên máy chủ',
    'Then, on this computer': 'Sau đó, trên máy này',
    'Type the code below. 0 or O, either works.': 'Nhập mã vào ô bên dưới. Số 0 hay chữ O đều được.',
    'First, on the server': 'Trước tiên, trên máy chủ',
    'Press Add device to get a code.': 'Nhấn Thêm thiết bị để lấy mã.',
    'Next you choose what to share. You can change it or disconnect any time.': 'Tiếp theo, bạn chọn nội dung chia sẻ. Bạn có thể đổi hoặc ngắt kết nối bất cứ lúc nào.',
    'Went offline': 'Đã ngoại tuyến',
    'Not heard from recently': 'Lâu rồi chưa nhận tin',
    'Disconnect computer': 'Ngắt kết nối máy tính',
    'Remove computer': 'Xóa máy tính',
    'Remove this computer and everything saved about it? This cannot be undone.': 'Xóa máy tính này và mọi dữ liệu đã lưu về nó? Không thể hoàn tác.',
    'Looking for your server...': 'Đang tìm máy chủ của bạn...',
    'No server found with this code. Check that the server is on and that this computer is on the same network.': 'Không tìm thấy máy chủ với mã này. Kiểm tra máy chủ đã bật và máy tính này dùng cùng mạng.',
    'Connected to {server}': 'Đã kết nối với {server}',
    'Choose what this server can see.': 'Chọn thông tin máy chủ này có thể xem.',
    'Awake time': 'Thời gian hoạt động',
    'Required to connect.': 'Bắt buộc để kết nối.',
    'Awake history by day': 'Lịch sử hoạt động theo ngày',
    'Daily totals of awake time.': 'Tổng thời gian hoạt động mỗi ngày.',
    'Computer specs and live performance': 'Thông số máy tính và hiệu suất trực tiếp',
    'Processor, memory, disk, battery and network.': 'Bộ xử lý, bộ nhớ, ổ đĩa, pin và mạng.',
    'Health and crashes': 'Tình trạng máy và sự cố',
    'Health summaries and crash counts.': 'Tóm tắt tình trạng máy và số lần gặp sự cố.',
    'Let the server sleep or restart this computer': 'Cho phép máy chủ đưa máy này vào chế độ ngủ hoặc khởi động lại',
    'Restart and shut down give a one minute warning first. Sleep is immediate.':
        'Khởi động lại và tắt máy có báo trước một phút. Chế độ ngủ diễn ra ngay.',
    'Finish': 'Hoàn tất',
    'Cancel': 'Hủy',
    'Cancel connection': 'Hủy kết nối',
    'Could not cancel the connection. Try again.': 'Không thể hủy kết nối. Hãy thử lại.',
    'Waiting for approval': 'Đang chờ phê duyệt',
    '{server} needs to approve this computer.': '{server} cần phê duyệt máy tính này.',
    'Connected': 'Đã kết nối',
    'Last report: {time}': 'Báo cáo gần nhất: {time}',
    'Shared: {items}': 'Đang chia sẻ: {items}',
    'Change what is shared': 'Thay đổi thông tin chia sẻ',
    'Disconnect': 'Ngắt kết nối',
    'Disconnect from this server?': 'Ngắt kết nối với máy chủ này?',
    "Can't reach the server right now. Lifetime will keep trying.": 'Hiện không thể kết nối với máy chủ. Lifetime sẽ tiếp tục thử.',
    "The server's security certificate has changed. Connect again with a new code from the server.": 'Chứng chỉ bảo mật của máy chủ đã thay đổi. Hãy kết nối lại bằng mã mới từ máy chủ.',
    'The secure connection to the server failed.': 'Kết nối bảo mật tới máy chủ không thành công.',
    "The server's name could not be found on this network.": 'Không tìm thấy tên máy chủ trên mạng này.',
    'The server computer answered, but Lifetime Server is not running on it.': 'Máy chủ có phản hồi, nhưng Lifetime Server không chạy trên máy đó.',
    'No answer from the server. It may be switched off, or a firewall may be blocking it.': 'Máy chủ không phản hồi. Có thể máy đang tắt hoặc bị tường lửa chặn.',
    'The connection to the server was interrupted.': 'Kết nối tới máy chủ bị gián đoạn.',
    'This computer has no network path to the server.': 'Máy tính này không có đường mạng tới máy chủ.',
    'The server sent a reply this version does not understand. Update Lifetime on both computers.': 'Máy chủ gửi phản hồi mà phiên bản này không hiểu. Hãy cập nhật Lifetime trên cả hai máy.',
    'Lifetime hit an internal error while reporting. It will keep trying.': 'Lifetime gặp lỗi nội bộ khi gửi báo cáo. Lifetime sẽ tiếp tục thử.',
    'The server refused the report (error {code}).': 'Máy chủ từ chối báo cáo (lỗi {code}).',
    'Windows permission is needed to finish.': 'Cần quyền Windows để hoàn tất.',
    'Could not finish connecting. Check that the server is on and try again.': 'Không thể hoàn tất kết nối. Kiểm tra máy chủ đã bật rồi thử lại.',
    'Could not finish connecting (error {code}). Check that the server is on and try again.':
        'Không thể hoàn tất kết nối (lỗi {code}). Kiểm tra máy chủ đã bật rồi thử lại.',
    "Windows could not set up Lifetime's reporting service with these details. Check the code and try again. If the folder "
    "Program Files\\LifetimeAgent holds files that are not from Lifetime, move them away first.":
        'Windows không thể cài đặt dịch vụ báo cáo của Lifetime với các thông tin này. Hãy kiểm tra mã rồi thử lại. Nếu thư mục '
        'Program Files\\LifetimeAgent chứa tệp không phải của Lifetime, hãy chuyển chúng đi trước.',
    "Could not reach the server, or it is not the server this code belongs to (its security certificate does not "
    "match). Check that the server is on and on the same network, and that the code is from that server.":
        'Không thể kết nối tới máy chủ, hoặc đó không phải máy chủ của mã này (chứng chỉ bảo mật không khớp). '
        'Hãy kiểm tra máy chủ đã bật, cùng mạng, và mã được tạo từ chính máy chủ đó.',
    'The server refused this code: it was already used, has expired or was cancelled. Create a new code on the server.':
        'Máy chủ từ chối mã này: mã đã được dùng, đã hết hạn hoặc đã bị hủy. Hãy tạo mã mới trên máy chủ.',
    "Setting up Lifetime's reporting service needs administrator approval. Sign in with an administrator account and try again.":
        'Cài đặt dịch vụ báo cáo của Lifetime cần quyền quản trị viên. Hãy đăng nhập bằng tài khoản quản trị viên rồi thử lại.',
    "Lifetime's reporting service is already installed on this computer and was not replaced. Ask your IT team to replace it, then try again.":
        'Máy tính này đã có dịch vụ báo cáo của Lifetime và dịch vụ đó chưa được thay thế. Hãy nhờ bộ phận IT thay thế rồi thử lại.',
    'This code cannot be used. Ask for a new code on the server.': 'Không thể dùng mã này. Hãy lấy mã mới trên máy chủ.',
    'Add a computer': 'Thêm máy tính',
    'On the computer, open Lifetime, choose Connect, and enter this code.': 'Trên máy tính, mở Lifetime, chọn Kết nối và nhập mã này.',
    'Copy': 'Sao chép',
    'Valid until {time}': 'Có hiệu lực đến {time}',
    'Valid until {date}, {time}': 'Có hiệu lực đến {date}, {time}',
    'Select a computer to see its details.': 'Chọn một máy tính để xem chi tiết.',
    'Other network?': 'Mạng khác?',
    '{name} wants to connect': '{name} muốn kết nối',
    '{name} connected': '{name} đã kết nối',
    'Choose or type a group': 'Chọn hoặc nhập một nhóm',
    'Group: {group}': 'Nhóm: {group}',
    'The group was not saved. Set it on the Devices page under Group.': 'Chưa lưu được nhóm. Hãy đặt nhóm trong trang Thiết bị, mục Nhóm.',
    'Group (department)': 'Nhóm (phòng ban)',
    'Edit details': 'Sửa thông tin',
    'Edit details…': 'Sửa thông tin…',
    'YYYY-MM-DD (optional)': 'YYYY-MM-DD (không bắt buộc)',
    'Check the details. The deployment date must look like 2026-11-16, or be left empty.': 'Hãy kiểm tra thông tin. Ngày đưa vào sử dụng phải có dạng 2026-11-16 hoặc để trống.',
    'Manage groups': 'Quản lý nhóm',
    'Create groups here first, then pick them for any computer. A computer is in a group when its Group field matches the name.': 'Tạo nhóm ở đây trước, rồi chọn nhóm cho bất kỳ máy tính nào. Một máy tính thuộc nhóm khi trường Nhóm của nó trùng với tên nhóm.',
    'New group name': 'Tên nhóm mới',
    'Add group': 'Thêm nhóm',
    'Groups': 'Các nhóm',
    'Rename…': 'Đổi tên…',
    'Rename group': 'Đổi tên nhóm',
    'New name for {name}. Its computers and settings move with it.': 'Tên mới cho {name}. Các máy tính và cài đặt của nhóm được chuyển theo.',
    'No groups yet': 'Chưa có nhóm nào',
    'Add a group above, then pick it for any computer.': 'Thêm một nhóm ở trên, rồi chọn nhóm đó cho bất kỳ máy tính nào.',
    "Couldn't update groups. Check that the Lifetime server is running.": 'Không thể cập nhật nhóm. Hãy kiểm tra máy chủ Lifetime đang chạy.',
    '{name}  ·  {count} computers': '{name}  ·  {count} máy tính',
    '{name}  ·  1 computer': '{name}  ·  1 máy tính',
    'Computers are still in this group. Move them to another group first.': 'Vẫn còn máy tính trong nhóm này. Hãy chuyển chúng sang nhóm khác trước.',
    'Connection declined.': 'Đã từ chối kết nối.',
    "Can't reach the Lifetime server. Make sure it is running.": 'Không thể kết nối với máy chủ Lifetime. Hãy kiểm tra máy chủ đang chạy.',
    'Not connected': 'Chưa kết nối',
    'Server: Online': 'Máy chủ: Trực tuyến',
    'Server: Offline': 'Máy chủ: Ngoại tuyến',
    'Server: Offline · last reached {time}': 'Máy chủ: Ngoại tuyến · lần cuối {time}',
    'Server: Checking…': 'Máy chủ: Đang kiểm tra',     # no ellipsis: fits the 200 px sidebar chip with Linux fallback fonts too
    "Waiting for Lifetime's background service on this computer.": 'Đang chờ dịch vụ Lifetime trên máy tính này.',
    'Awake time keeps counting on this computer and is sent when the server is back.': 'Thời gian hoạt động vẫn được đếm trên máy tính này và sẽ được gửi khi máy chủ trở lại.',
}
for _key, _text in CONNECT_VI.items():
    EN[_key] = _key
    VI[_key] = _text
    SERVER_DASHBOARD_VI[_key] = _text

_CONNECT_PLAIN_VI = {
    'No computers match. Choose Add device to connect a computer.': 'Không có máy tính phù hợp. Chọn Thêm thiết bị để kết nối máy tính.',
    'To reconnect, get a new code and approve this computer again. Its history is kept.': 'Để kết nối lại, lấy mã mới và phê duyệt máy tính này. Lịch sử được giữ lại.',
    'After restoring, computers need a new code and approval to connect again.': 'Sau khi khôi phục, máy tính cần mã mới và được phê duyệt để kết nối lại.',
    'Disconnect this computer? Connecting again needs approval.': 'Ngắt kết nối máy tính này? Kết nối lại cần được phê duyệt.',
    'of {count} computers': 'trên {count} máy tính',
    'Could not change Health sharing. Open Connect to change what is shared.': 'Không thể thay đổi chia sẻ tình trạng máy. Mở Kết nối để thay đổi thông tin chia sẻ.',
}
for _key, _text in _CONNECT_PLAIN_VI.items():
    EN[_key] = _key
    VI[_key] = _text
    SERVER_DASHBOARD_VI[_key] = _text

EN['Connection requests'] = 'Connection requests'
VI['Connection requests'] = 'Yêu cầu kết nối'
SERVER_DASHBOARD_VI['Connection requests'] = VI['Connection requests']

_REPORT_SOURCES_VI = {
    'Where the saved time comes from': 'Nguồn của thời gian đã lưu',
    'Measured: {time}': 'Đo trực tiếp: {time}',
    'OS logs: {time}': 'Nhật ký hệ điều hành: {time}',
    'Manual baseline: {time}': 'Số giờ nền nhập tay: {time}',
    'Shared with the server': 'Đang chia sẻ với máy chủ',
}
for _key, _text in _REPORT_SOURCES_VI.items():
    EN[_key] = _key
    VI[_key] = _text

_SWITCH_VI = {
    'Choose what Lifetime tracks and how it looks. Changes apply and save straight away.':
        'Chọn nội dung Lifetime theo dõi và giao diện. Thay đổi được áp dụng và lưu ngay.',
}
VI.update(_SWITCH_VI)
EN.update({key: key for key in _SWITCH_VI})

_SERVER_SWITCH_VI = {
    'Language: {current}. Click to switch to {next}.': 'Ngôn ngữ: {current}. Bấm để chuyển sang {next}.',
    'Theme: {current}. Click to switch to {next}.': 'Giao diện: {current}. Bấm để chuyển sang {next}.',
}
SERVER_DASHBOARD_VI.update(_SERVER_SWITCH_VI)

_HARDWARE_VI = {
    '{used} of {total} memory slots occupied': 'Đã dùng {used} / {total} khe RAM',
    'MAC address': 'Địa chỉ MAC',
    'Processor & cooling': 'Bộ xử lý và làm mát',
    'Graphics & displays': 'Đồ họa và màn hình',
    'Motherboard & BIOS': 'Bo mạch chủ và BIOS',
    'Operating system': 'Hệ điều hành',
    'Current clock': 'Xung nhịp hiện tại',
    'Maximum clock': 'Xung nhịp tối đa',
    'Reported fans': 'Số quạt được báo cáo',
    'Fan speed': 'Tốc độ quạt',
    'Fan {number}': 'Quạt {number}',
    'Slots used': 'Khe đã dùng',
    'Total slots': 'Tổng số khe',
    'Memory module {number}': 'Thanh RAM {number}',
    'Memory type': 'Loại RAM',
    'Memory speed': 'Tốc độ RAM',
    'Capacity': 'Dung lượng',
    'Drive {number}': 'Ổ đĩa {number}',
    'Drive type': 'Loại ổ đĩa',
    'Disk health': 'Tình trạng ổ đĩa',
    'Connection type': 'Loại kết nối',
    'Network card {number}': 'Card mạng {number}',
    'Graphics adapter {number}': 'Bộ xử lý đồ họa {number}',
    'Video memory': 'Bộ nhớ đồ họa',
    'Display {number}': 'Màn hình {number}',
    'Resolution': 'Độ phân giải',
    'Refresh rate': 'Tần số quét',
    'BIOS vendor': 'Nhà cung cấp BIOS',
    'BIOS version': 'Phiên bản BIOS',
    'BIOS date': 'Ngày BIOS',
    'OS name': 'Tên hệ điều hành',
    'OS build': 'Bản dựng hệ điều hành',
    'Architecture': 'Kiến trúc',
    'Installed': 'Ngày cài đặt',
    'Last started': 'Lần khởi động gần nhất', 'Time since start-up': 'Thời gian từ lúc khởi động',
    'Wi-Fi standard': 'Chuẩn Wi-Fi',
    'Wi-Fi band': 'Băng tần Wi-Fi',
    'Unavailable readings: {reasons}': 'Thông số chưa có: {reasons}',
    'Windows did not expose an identified CPU temperature sensor': 'Windows không cung cấp cảm biến nhiệt độ CPU được xác định rõ',
    'Access denied by Windows': 'Windows từ chối quyền truy cập',
    'Windows did not expose network driver information': 'Windows không cung cấp thông tin trình điều khiển mạng',
    'Link speed': 'Tốc độ liên kết',
    'Driver': 'Trình điều khiển',
    'Hardware details': 'Chi tiết phần cứng',
    'Hardware inventory refreshes about every six hours': 'Thông tin phần cứng được cập nhật khoảng sáu giờ một lần',
    'The operating system did not expose this reading': 'Hệ điều hành không cung cấp thông số này',
    "Not reported by this computer's firmware": 'Firmware của máy tính này không cung cấp thông tin',
    'CPU temperature': 'Nhiệt độ CPU',
    'CPU current / maximum clock': 'Xung nhịp CPU hiện tại / tối đa',
    'Cooling fans': 'Quạt làm mát',
    'Network cards': 'Card mạng',
    'RAM modules': 'Các thanh RAM',
    'RAM slots used / total': 'Khe RAM đã dùng / tổng số',
    'Physical disks': 'Ổ đĩa vật lý',
    'Motherboard': 'Bo mạch chủ',
    'BIOS vendor / version / date': 'Nhà cung cấp / phiên bản / ngày BIOS',
    'GPU driver / VRAM': 'Trình điều khiển GPU / VRAM',
    'Displays': 'Màn hình',
    'OS build / architecture / installed': 'Bản dựng / kiến trúc / ngày cài hệ điều hành',
    'Time since start-up / last started': 'Thời gian từ lúc khởi động / lần khởi động gần nhất',
}
for _key, _text in _HARDWARE_VI.items():
    EN[_key] = _key
    VI[_key] = _text
    SERVER_DASHBOARD_VI[_key] = _text

_TRUST_VI = {
    'Certificate expires': 'Chứng chỉ hết hạn',
    'Server key SHA-256': 'SHA-256 khóa máy chủ',
    '{date} · renewed automatically': '{date} · tự động gia hạn',
    'Other server names and IPs': 'Tên và IP khác của máy chủ',
    'Save names': 'Lưu tên',
    'Separate them with commas. Lifetime renews the certificate with the same key, so connected computers keep working without a new code.':
        'Phân tách bằng dấu phẩy. Lifetime gia hạn chứng chỉ với cùng một khóa, nên các máy tính đã kết nối vẫn hoạt động mà không cần mã mới.',
    'Lifetime Server could not renew its security certificate. The current one works until {date}. Check the server names under Administration.':
        'Lifetime Server không thể gia hạn chứng chỉ bảo mật. Chứng chỉ hiện tại còn dùng được đến {date}. Hãy kiểm tra tên máy chủ trong mục Quản trị.',
    "The server's security certificate is out of date, or this computer's clock is wrong. Check the date and time, or ask the administrator to check Lifetime Server.":
        'Chứng chỉ bảo mật của máy chủ đã hết hạn, hoặc đồng hồ của máy tính này bị sai. Hãy kiểm tra ngày giờ, hoặc nhờ quản trị viên kiểm tra Lifetime Server.',
    "This address is not one of the server's names. Ask the administrator to add it under Administration on Lifetime Server.":
        'Địa chỉ này không nằm trong các tên của máy chủ. Hãy nhờ quản trị viên thêm địa chỉ này trong mục Quản trị của Lifetime Server.',
    'The server runs an older version of Lifetime, so some details are not shared until it is updated.':
        'Máy chủ đang chạy phiên bản Lifetime cũ hơn, nên một số chi tiết chưa được chia sẻ cho đến khi máy chủ được cập nhật.',
    'Last problem': 'Sự cố gần nhất',
    'None reported': 'Chưa có',
    'Server error {code}': 'Lỗi máy chủ {code}',
    'Server certificate not trusted': 'Không tin cậy chứng chỉ máy chủ',
    'Certificate out of date or wrong clock': 'Chứng chỉ hết hạn hoặc đồng hồ sai',
    'Address not in the certificate': 'Địa chỉ không có trong chứng chỉ',
    'Secure connection failed': 'Kết nối bảo mật không thành công',
    'Server name not found': 'Không tìm thấy tên máy chủ',
    'Connection refused': 'Kết nối bị từ chối',
    'No answer (timeout)': 'Không phản hồi (hết thời gian chờ)',
    'Connection interrupted': 'Kết nối bị gián đoạn',
    'No network path': 'Không có đường mạng',
    'Reply not understood': 'Không hiểu phản hồi',
    'Internal error on the computer': 'Lỗi nội bộ trên máy tính',
    'Report rejected by the server': 'Máy chủ từ chối báo cáo',
    'Report conflicts with stored data': 'Báo cáo mâu thuẫn với dữ liệu đã lưu',
    'Server log': 'Nhật ký máy chủ',
    'No server events yet': 'Chưa có sự kiện máy chủ',
    'No events yet': 'Chưa có sự kiện',
    'No other account or group has a role yet.': 'Chưa có tài khoản hay nhóm nào khác được cấp vai trò.',
    'No maintenance windows. Alerts open as usual.': 'Không có khoảng bảo trì. Cảnh báo được mở như bình thường.',
    'No alert messages sent yet.': 'Chưa gửi tin nhắn cảnh báo nào.',
    'Recent server events: starts and stops, certificate renewals and refused requests. No report contents or codes are kept.':
        'Các sự kiện gần đây của máy chủ: khởi động và dừng, gia hạn chứng chỉ và các yêu cầu bị từ chối. Không lưu nội dung báo cáo hay mã.',
    '{event} ({count} times)': '{event} ({count} lần)',
    'Server started': 'Máy chủ đã khởi động',
    'Server stopped': 'Máy chủ đã dừng',
    'Server names changed': 'Đã đổi tên máy chủ',
    'Certificate renewed (before expiry)': 'Đã gia hạn chứng chỉ (trước khi hết hạn)',
    'Certificate renewed (new names)': 'Đã gia hạn chứng chỉ (tên mới)',
    'Certificate renewal failed': 'Gia hạn chứng chỉ không thành công',
    'Report refused: invalid data': 'Từ chối báo cáo: dữ liệu không hợp lệ',
    'Report refused: not a connected computer': 'Từ chối báo cáo: không phải máy tính đã kết nối',
    'Report refused: conflicts with stored data': 'Từ chối báo cáo: mâu thuẫn với dữ liệu đã lưu',
    'Report refused: too many attempts': 'Từ chối báo cáo: quá nhiều lần thử',
    'Connection refused: invalid request': 'Từ chối kết nối: yêu cầu không hợp lệ',
    'Connection refused: wrong or used code': 'Từ chối kết nối: mã sai hoặc đã dùng',
    'Connection refused: conflicting request': 'Từ chối kết nối: yêu cầu mâu thuẫn',
    'Connection refused: too many attempts': 'Từ chối kết nối: quá nhiều lần thử',
}
for _key, _text in _TRUST_VI.items():
    EN[_key] = _key
    VI[_key] = _text
    SERVER_DASHBOARD_VI[_key] = _text

# Alerts and incidents (Lifetime Server).
_ALERTS_VI = {
    'Incidents': 'Sự cố',
    'Problems': 'Các sự cố',
    'Open alerts': 'Cảnh báo đang mở',
    '1 open alert': '1 cảnh báo đang mở',
    '{count} open alerts': '{count} cảnh báo đang mở',
    'Current problems': 'Sự cố hiện tại',
    'Acknowledged': 'Đã xác nhận',
    'Paused by maintenance': 'Tạm dừng do bảo trì',
    'Resolved': 'Đã khắc phục',
    'Starting': 'Đang bắt đầu',
    'Show': 'Hiển thị',
    'Problem': 'Sự cố',
    'Since': 'Từ lúc',
    'Started': 'Bắt đầu',
    'Opened': 'Mở lúc',
    'Recovered at': 'Khắc phục lúc',
    'Outcome': 'Kết quả',
    'Recovered': 'Đã khắc phục',
    'No longer monitored': 'Không còn theo dõi',
    'Timeline': 'Diễn biến',
    'Problem started': 'Sự cố bắt đầu',
    'Incident opened': 'Đã mở sự cố',
    'Acknowledged by {who}': 'Đã được {who} xác nhận',
    'Problem returned': 'Sự cố quay lại',
    'Acknowledge': 'Xác nhận',
    '{who} · {time}': '{who} · {time}',
    'Select an incident to see its timeline.': 'Chọn một sự cố để xem diễn biến.',
    'No incidents': 'Không có sự cố',
    'No incidents. Lifetime opens one when a rule under Administration finds a problem.':
        'Không có sự cố. Lifetime sẽ mở một sự cố khi một quy tắc trong mục Quản trị phát hiện vấn đề.',
    'Alerts are unavailable. Showing the last data received, if any.':
        'Không tải được cảnh báo. Đang hiển thị dữ liệu nhận được gần nhất, nếu có.',
    'The server did not accept this. Check the values and try again.':
        'Máy chủ không chấp nhận thay đổi này. Hãy kiểm tra các giá trị rồi thử lại.',
    'Not expected online now': 'Hiện không cần trực tuyến',
    'Readings missing: {rules}': 'Thiếu số liệu: {rules}',
    'Computer offline': 'Máy tính ngoại tuyến',
    'No report received': 'Không nhận được báo cáo',
    'Processor busy': 'Bộ xử lý quá tải',
    'Memory almost full': 'Bộ nhớ gần đầy',
    'Repeated crashes': 'Lỗi hệ thống lặp lại',
    'Lifetime version too old': 'Phiên bản Lifetime quá cũ',
    'Alert rules': 'Quy tắc cảnh báo',
    'A problem must last the whole duration before an incident opens, and stay away five minutes before it resolves. Rules use only what each computer shares.':
        'Một vấn đề phải kéo dài hết thời lượng thì sự cố mới được mở, và phải biến mất năm phút thì mới được coi là đã khắc phục. Quy tắc chỉ dùng dữ liệu mà mỗi máy tính chia sẻ.',
    'Threshold': 'Ngưỡng',
    'For (minutes)': 'Trong (phút)',
    'min': 'phút',
    'Minimum version, e.g. 1.2.0': 'Phiên bản tối thiểu, ví dụ 1.2.0',
    'Save rules': 'Lưu quy tắc',
    'Expected online hours': 'Giờ cần trực tuyến',
    'Offline alerts count only inside these hours. Automatic expects desktops always and laptops never, so a sleeping laptop raises nothing.':
        'Cảnh báo ngoại tuyến chỉ tính trong các giờ này. Tự động: máy để bàn luôn cần trực tuyến, máy xách tay thì không, nên máy xách tay đang ngủ không gây cảnh báo.',
    'Everyone': 'Tất cả máy tính',
    'Computer: {name}': 'Máy tính: {name}',
    'Group: {name}': 'Nhóm: {name}',
    'Automatic (desktops always, laptops never)': 'Tự động (máy để bàn luôn luôn, máy xách tay không bao giờ)',
    'Always': 'Luôn luôn',
    'Business hours': 'Giờ làm việc',
    'Never': 'Không bao giờ',
    'Applies to': 'Áp dụng cho',
    'Hours': 'Giờ',
    'Opens at': 'Bắt đầu lúc',
    'Closes at': 'Kết thúc lúc',
    'Time zone': 'Múi giờ',
    'Holidays (YYYY-MM-DD, comma separated)': 'Ngày nghỉ (YYYY-MM-DD, phân tách bằng dấu phẩy)',
    'server time': 'giờ máy chủ',
    '{days} · {start}–{end} ({zone})': '{days} · {start}–{end} ({zone})',
    '{count} holidays': '{count} ngày nghỉ',
    'Remove selected': 'Xóa mục đã chọn',
    'Maintenance windows': 'Thời gian bảo trì',
    'No alert opens during a window. A problem still there when it ends opens then; one that clears is closed quietly.':
        'Không có cảnh báo nào được mở trong thời gian bảo trì. Vấn đề vẫn còn khi kết thúc sẽ được mở lúc đó; vấn đề đã hết sẽ được đóng mà không thông báo.',
    'From': 'Từ',
    'Until': 'Đến',
    'Note': 'Ghi chú',
    'Add window': 'Thêm thời gian',
    'Remove': 'Xóa',
    'Notifications': 'Thông báo',
    'Email alerts': 'Cảnh báo qua email',
    'Mail server and port': 'Máy chủ thư và cổng',
    'Port': 'Cổng',
    'Security': 'Bảo mật',
    'User name': 'Tên người dùng',
    'Password': 'Mật khẩu',
    'Saved. Type to replace it.': 'Đã lưu. Nhập để thay thế.',
    'Sender address': 'Địa chỉ người gửi',
    'Recipients (comma separated)': 'Người nhận (phân tách bằng dấu phẩy)',
    'Message language': 'Ngôn ngữ tin nhắn',
    'Daily summary': 'Tóm tắt hằng ngày',
    'Daily summary at': 'Gửi tóm tắt hằng ngày lúc',
    'Webhook': 'Webhook',
    'Webhook (https://)': 'Webhook (https://)',
    'Send test email': 'Gửi email thử',
    'Send test webhook': 'Gửi webhook thử',
    'Save notifications': 'Lưu thông báo',
    'Sending…': 'Đang gửi…',
    'Test message sent.': 'Đã gửi tin nhắn thử.',
    'Test message failed: {reason}': 'Gửi tin nhắn thử không thành công: {reason}',
    'The password and webhook address are stored encrypted for the account that runs the server and are never shown again. Messages name the computer and the problem only.':
        'Mật khẩu và địa chỉ webhook được mã hóa cho tài khoản chạy máy chủ và không bao giờ hiển thị lại. Tin nhắn chỉ nêu tên máy tính và sự cố.',
    'The saved password cannot be read by this server account. Enter it again.':
        'Tài khoản chạy máy chủ không đọc được mật khẩu đã lưu. Hãy nhập lại.',
    'Delivery log': 'Nhật ký gửi',
    'Channel': 'Kênh',
    'Message': 'Tin nhắn',
    'Email': 'Email',
    'Test message': 'Tin nhắn thử',
    'Waiting to retry': 'Đang chờ thử lại',
    'Sending': 'Đang gửi',
    'Sent': 'Đã gửi',
    'Failed': 'Không thành công',
    'Could not connect': 'Không kết nối được',
    'Sign-in refused': 'Đăng nhập bị từ chối',
    'Address refused': 'Địa chỉ bị từ chối',
    'Settings incomplete': 'Cài đặt chưa đủ',
    'Interrupted by a server stop': 'Bị gián đoạn do máy chủ dừng',
    'Mail server error': 'Lỗi máy chủ thư',
    'Incident removed': 'Sự cố đã bị xóa',
    'Time zone names such as Asia/Ho_Chi_Minh need the tzdata package on this server (install requirements-server.txt). Use local, UTC or a fixed offset such as +07:00 meanwhile.':
        'Tên múi giờ như Asia/Ho_Chi_Minh cần gói tzdata trên máy chủ này (cài đặt requirements-server.txt). Trong lúc chờ, hãy dùng local, UTC hoặc độ lệch cố định như +07:00.',
    'time zone unavailable, using server time': 'không tải được múi giờ, đang dùng giờ máy chủ',
    'Group name': 'Tên nhóm',
    'No approved computers yet': 'Chưa có máy tính nào được phê duyệt',
    'Choose a computer': 'Hãy chọn một máy tính',
    'Enter a group name': 'Hãy nhập tên nhóm',
    'Minimum coverage for availability': 'Độ bao phủ tối thiểu cho mức sẵn sàng',
    'Availability reports count only these hours. Below this coverage (the share of them actually observed) a result says insufficient evidence.':
        'Báo cáo mức sẵn sàng chỉ tính các giờ này. Nếu độ bao phủ (phần thời gian thực sự quan sát được) thấp hơn mức này, kết quả sẽ ghi là chưa đủ bằng chứng.',
    'Monitoring history and alert settings are saved beside the backup as a second file (name.monitoring.sqlite) and restored with it when that file is there. The mail password and webhook address are not included: enter them again after restoring on another server.':
        'Lịch sử theo dõi và cài đặt cảnh báo được lưu cạnh bản sao lưu thành một tệp thứ hai (tên.monitoring.sqlite) và được khôi phục cùng khi có tệp đó. Mật khẩu thư và địa chỉ webhook không được đưa vào: hãy nhập lại sau khi khôi phục trên máy chủ khác.',
    'Backup saved. Monitoring history is in {name} beside it: keep both files together.':
        'Đã lưu bản sao lưu. Lịch sử theo dõi nằm trong {name} bên cạnh: hãy giữ cả hai tệp cùng nhau.',
    'Restored, including monitoring history and alert settings.': 'Đã khôi phục, gồm cả lịch sử theo dõi và cài đặt cảnh báo.',
    'Restored. No monitoring file was beside this backup, so the current alert history and settings were kept.':
        'Đã khôi phục. Không có tệp theo dõi bên cạnh bản sao lưu này nên lịch sử cảnh báo và cài đặt hiện tại được giữ nguyên.',
    'Time zone: local, UTC, +07:00 or Asia/Ho_Chi_Minh': 'Múi giờ: local, UTC, +07:00 hoặc Asia/Ho_Chi_Minh',
}
for _key, _text in _ALERTS_VI.items():
    EN.setdefault(_key, _key)
    VI.setdefault(_key, _text)
    SERVER_DASHBOARD_VI.setdefault(_key, _text)

_REPORTS2_VI = {
    'Availability (server monitoring)': 'Mức sẵn sàng (máy chủ theo dõi)',
    'Health summary': 'Tóm tắt tình trạng',
    'All computers': 'Tất cả máy tính',
    'Replacement candidates': 'Máy tính nên cân nhắc thay thế',
    'Observed availability': 'Mức sẵn sàng quan sát được',
    'Monitoring coverage': 'Độ bao phủ theo dõi',
    'Coverage floor': 'Độ bao phủ tối thiểu',
    'Observed online (seconds)': 'Quan sát thấy trực tuyến (giây)',
    'Observed offline (seconds)': 'Quan sát thấy ngoại tuyến (giây)',
    'Unknown, not observed (seconds)': 'Không rõ, không quan sát được (giây)',
    'Maintenance, excluded (seconds)': 'Bảo trì, không tính (giây)',
    'Expected online (seconds)': 'Cần trực tuyến (giây)',
    'Availability period': 'Khoảng thời gian tính mức sẵn sàng',
    'Calendar time zone': 'Múi giờ của lịch',
    'Incidents in the period': 'Sự cố trong khoảng thời gian',
    'Incident': 'Sự cố',
    'Battery health (%)': 'Tình trạng pin (%)',
    'Battery charge cycles': 'Số chu kỳ sạc pin',
    'Crashes in the last 30 days': 'Số lần lỗi hệ thống trong 30 ngày qua',
    'Unexpected shutdowns in the last 30 days': 'Số lần tắt máy bất thường trong 30 ngày qua',
    'System disk free (%)': 'Dung lượng trống ổ hệ thống (%)',
    'Health reading time': 'Thời điểm đọc tình trạng',
    'Health readings': 'Số đo tình trạng',
    'Replacement candidate': 'Nên cân nhắc thay thế',
    'Reason': 'Lý do',
    'Data source': 'Nguồn dữ liệu',
    'Computers with availability data': 'Số máy tính có dữ liệu mức sẵn sàng',
    'Not expected online in this period': 'Không cần trực tuyến trong khoảng thời gian này',
    'Insufficient evidence (observed {value}, coverage {coverage})': 'Chưa đủ bằng chứng (quan sát được {value}, độ bao phủ {coverage})',
    'Battery health {value} % is below {limit} %': 'Tình trạng pin {value} % thấp hơn {limit} %',
    'System disk has less than {limit} % free': 'Ổ hệ thống còn trống dưới {limit} %',
    '{value} crashes in 30 days (limit {limit})': '{value} lần lỗi hệ thống trong 30 ngày (ngưỡng {limit})',
    'Not shared by this computer': 'Máy tính này không chia sẻ',
    'ongoing': 'đang diễn ra',
    'Network check failing': 'Kiểm tra mạng không thành công',
    'Day {date}': 'Ngày {date}',
    'online {good} s · offline {bad} s · unknown {unknown} s · maintenance {excluded} s': 'trực tuyến {good} giây · ngoại tuyến {bad} giây · không rõ {unknown} giây · bảo trì {excluded} giây',
    'each computer’s own calendar ({zones})': 'lịch riêng của từng máy tính ({zones})',
    'Lifetime Server {version} · Time zone: {zone} · Source: {source}': 'Lifetime Server {version} · Múi giờ: {zone} · Nguồn: {source}',
    'Lifetime Server monitoring and the latest snapshot each computer permits': 'Dữ liệu theo dõi của Lifetime Server và bản chụp mới nhất mà mỗi máy tính cho phép',
    'from this computer’s schedule': 'theo lịch của máy tính này',
    'from its group’s schedule': 'theo lịch của nhóm',
    'from the default schedule': 'theo lịch mặc định',
    'Awake time and availability are different measures: awake time comes from the computer’s own record, availability from the server observing whether it was reporting during its expected hours.': 'Thời gian hoạt động và mức sẵn sàng là hai thước đo khác nhau: thời gian hoạt động lấy từ bản ghi của chính máy tính, còn mức sẵn sàng là việc máy chủ quan sát máy tính có gửi báo cáo trong giờ cần trực tuyến hay không.',
    'Availability counts only expected-online hours. Unknown time (server down, no report yet, before monitoring) is never counted as up or down; maintenance windows are excluded.': 'Mức sẵn sàng chỉ tính trong giờ cần trực tuyến. Thời gian không rõ (máy chủ tắt, chưa có báo cáo, trước khi bắt đầu theo dõi) không bao giờ được tính là hoạt động hay ngừng hoạt động; thời gian bảo trì không được tính.',
    'Day labels for availability use each computer’s calendar: the time zone of its expected-online schedule.': 'Nhãn ngày của mức sẵn sàng dùng lịch của từng máy tính: múi giờ trong lịch giờ cần trực tuyến của máy đó.',
    'Replacement candidates are suggestions from the stated readings and the alert thresholds, not a diagnosis.': 'Danh sách nên cân nhắc thay thế chỉ là gợi ý dựa trên các số đo đã nêu và ngưỡng cảnh báo, không phải chẩn đoán.',
}
for _key, _text in _REPORTS2_VI.items():
    EN.setdefault(_key, _key)
    VI.setdefault(_key, _text)
    SERVER_DASHBOARD_VI.setdefault(_key, _text)

_CHECKS_VI = {
    'Network checks': 'Kiểm tra mạng',
    'Watch printers, routers, NAS and websites that do not run Lifetime. A check that fails as many times in a row as the Network check failing rule says opens one incident.': 'Theo dõi máy in, bộ định tuyến, NAS và trang web không chạy Lifetime. Một kiểm tra thất bại liên tiếp đủ số lần đặt trong quy tắc Kiểm tra mạng không thành công sẽ mở một sự cố.',
    "Checks reach only these networks: private (LAN addresses), public (internet addresses) or ranges such as 192.168.10.0/24. Loopback and link-local addresses need an explicit range; cloud metadata addresses and this server's admin port are always blocked.": 'Các kiểm tra chỉ được truy cập những mạng này: private (địa chỉ mạng nội bộ), public (địa chỉ internet) hoặc dải như 192.168.10.0/24. Địa chỉ loopback và link-local cần một dải ghi rõ; địa chỉ metadata đám mây và cổng quản trị của máy chủ này luôn bị chặn.',
    'Ping': 'Ping',
    'TCP port': 'Cổng TCP',
    'Web page (HTTP or HTTPS)': 'Trang web (HTTP hoặc HTTPS)',
    'Up': 'Hoạt động',
    'Name not found': 'Không tìm thấy tên',
    'Blocked by the allowed networks': 'Bị chặn theo danh sách mạng được phép',
    'No reply': 'Không phản hồi',
    'Unexpected status {detail}': 'Mã trạng thái không mong đợi {detail}',
    'Response too large': 'Phản hồi quá lớn',
    'Keyword not found': 'Không thấy từ khóa',
    'Check failed': 'Kiểm tra không thành công',
    'Not checked yet': 'Chưa kiểm tra',
    'New check': 'Kiểm tra mới',
    'Save check': 'Lưu kiểm tra',
    'Save networks': 'Lưu mạng',
    'Allowed networks': 'Mạng được phép',
    'Name or address, e.g. printer.local': 'Tên hoặc địa chỉ, ví dụ printer.local',
    'Name': 'Tên',
    'Target': 'Đích',
    'Result': 'Kết quả',
    'Latency': 'Độ trễ',
    'Last checked': 'Kiểm tra lần cuối',
    'Keyword (optional)': 'Từ khóa (không bắt buộc)',
    'Group (optional)': 'Nhóm (không bắt buộc)',
    'Kind': 'Loại',
    'Method': 'Phương thức',
    'Expected status from': 'Mã trạng thái mong đợi từ',
    'Expected status to': 'Mã trạng thái mong đợi đến',
    'Every': 'Mỗi',
    'Timeout': 'Thời gian chờ',
    'Verify certificate': 'Xác minh chứng chỉ',
    '{count} in a row': '{count} lần liên tiếp',
}
for _key, _text in _CHECKS_VI.items():
    EN.setdefault(_key, _key)
    VI.setdefault(_key, _text)
    SERVER_DASHBOARD_VI.setdefault(_key, _text)


# Stage 3: roles, audit trail, archive backups and remote power confirmation (Lifetime Server dashboard).
_ACCESS_VI = {
    'Roles and access': 'Vai trò và quyền truy cập',
    'Administrators (running as administrator) and SYSTEM are always Admin. Give other Windows accounts or groups a role: Operator (acknowledge incidents, maintenance windows, refresh, and remote power for one computer where its owner allowed it), Auditor (view and audit trail) or Viewer (view only). Anyone who can read the administrator token file is also Admin.': 'Administrators (khi chạy với quyền quản trị) và SYSTEM luôn là Quản trị. Gán vai trò cho tài khoản hoặc nhóm Windows khác: Vận hành (xác nhận sự cố, khung bảo trì, làm mới, và điều khiển nguồn từ xa cho từng máy khi chủ máy cho phép), Kiểm toán (xem và nhật ký kiểm toán) hoặc Người xem (chỉ xem). Ai đọc được tệp mã thông báo quản trị cũng là Quản trị.',
    'Admin': 'Quản trị',
    'Operator': 'Vận hành',
    'Auditor': 'Kiểm toán',
    'Viewer': 'Người xem',
    'No role': 'Không có vai trò',
    'Role': 'Vai trò',
    'Account': 'Tài khoản',
    'Account or group': 'Tài khoản hoặc nhóm',
    'Granted by': 'Người cấp',
    'Action': 'Thao tác',
    'Give role': 'Gán vai trò',
    'Windows account or group': 'Tài khoản hoặc nhóm Windows',
    'Linux user or group': 'Người dùng hoặc nhóm Linux',
    'Linux user or group, e.g. alice or group:helpdesk': 'Người dùng hoặc nhóm Linux, ví dụ alice hoặc group:helpdesk',
    'Remote power needs your own Linux account. The shared administrator token cannot be used for it.': 'Điều khiển nguồn từ xa cần tài khoản Linux của chính bạn. Không thể dùng mã thông báo quản trị dùng chung.',
    'root and members of the lifetime-admin group are always Admin. Other Linux accounts must be in the lifetime-console group to connect, which grants nothing by itself; give them or their groups a role: Operator (acknowledge incidents, maintenance windows, refresh, and remote power for one computer where its owner allowed it), Auditor (view and audit trail) or Viewer (view only). Anyone who can read the administrator token file is also Admin.': 'root và thành viên nhóm lifetime-admin luôn là Quản trị. Tài khoản Linux khác phải thuộc nhóm lifetime-console để kết nối, nhưng điều đó tự nó không cấp quyền gì; hãy gán vai trò cho tài khoản hoặc nhóm của họ: Vận hành (xác nhận sự cố, khung bảo trì, làm mới, và điều khiển nguồn từ xa cho từng máy khi chủ máy cho phép), Kiểm toán (xem và nhật ký kiểm toán) hoặc Người xem (chỉ xem). Ai đọc được tệp mã thông báo quản trị cũng là Quản trị.',
    'Windows account or group, e.g. SCHOOL\\helpdesk': 'Tài khoản hoặc nhóm Windows, ví dụ SCHOOL\\helpdesk',
    'Signed in as {name} · {role}': 'Đăng nhập là {name} · {role}',
    'administrator token': 'mã thông báo quản trị',
    'Your role ({role}) cannot do this.': 'Vai trò của bạn ({role}) không được làm việc này.',
    'Your role does not allow this.': 'Vai trò của bạn không cho phép việc này.',
    'This Windows account has no Lifetime Server role. Ask an administrator for one, or open the dashboard as administrator.': 'Tài khoản Windows này chưa có vai trò trên Lifetime Server. Hãy nhờ quản trị viên cấp, hoặc mở bảng điều khiển với quyền quản trị.',
    'Remote power needs your own Windows account. The shared administrator token cannot be used for it.': 'Điều khiển nguồn từ xa cần tài khoản Windows của chính bạn. Không thể dùng mã thông báo quản trị dùng chung.',
    'Remove this role? The account loses access at its next request.': 'Gỡ vai trò này? Tài khoản sẽ mất quyền truy cập từ yêu cầu tiếp theo.',
    'Audit trail': 'Nhật ký kiểm toán',
    "Every change and every remote command, with the Windows account that did it. Each entry carries the previous entry's hash, so a changed or removed entry is detected. A local administrator can still edit the file, so keep exported copies somewhere else.": 'Mọi thay đổi và mọi lệnh từ xa, kèm tài khoản Windows đã thực hiện. Mỗi mục chứa mã băm của mục trước, nên mục bị sửa hoặc bị xóa sẽ bị phát hiện. Quản trị viên cục bộ vẫn có thể sửa tệp, vì vậy hãy giữ bản xuất ở nơi khác.',
    'Verify audit trail': 'Kiểm tra nhật ký kiểm toán',
    'Export CSV': 'Xuất CSV',
    'CSV files (*.csv)': 'Tệp CSV (*.csv)',
    'Exported {count} audit entries.': 'Đã xuất {count} mục kiểm toán.',
    'The audit trail is intact ({count} entries checked).': 'Nhật ký kiểm toán còn nguyên vẹn (đã kiểm tra {count} mục).',
    'The audit trail is broken at entry {seq}: {reason}.': 'Nhật ký kiểm toán bị đứt tại mục {seq}: {reason}.',
    'an entry was changed': 'một mục đã bị sửa',
    'an entry was removed': 'một mục đã bị xóa',
    'entries were reordered': 'thứ tự các mục đã bị đổi',
    'the newest entries were removed': 'các mục mới nhất đã bị xóa',
    'Verify backup': 'Kiểm tra bản sao lưu',
    'Backup archives (*.zip)': 'Tệp sao lưu (*.zip)',
    'A backup is one protected .zip archive with a .sha256 file beside it: the databases (computers, monitoring, roles and the audit trail), the server key and certificate, and the configuration. It holds the private key, so keep it where only administrators can read it. The mail password, webhook address and administrator token are not included.': 'Bản sao lưu là một tệp .zip được bảo vệ kèm tệp .sha256 bên cạnh: các cơ sở dữ liệu (máy tính, giám sát, vai trò và nhật ký kiểm toán), khóa và chứng chỉ máy chủ, và cấu hình. Tệp chứa khóa riêng, vì vậy chỉ để ở nơi chỉ quản trị viên đọc được. Không gồm mật khẩu thư, địa chỉ webhook và mã thông báo quản trị.',
    'Restore checks the archive and applies it the next time the server starts (restart it with restart_server_windows.ps1). Computers then need a new code and approval to connect again; the current roles and audit trail are kept.': 'Khôi phục sẽ kiểm tra tệp sao lưu và áp dụng ở lần khởi động máy chủ tiếp theo (khởi động lại bằng restart_server_windows.ps1). Sau đó các máy tính cần mã mới và được phê duyệt lại; vai trò và nhật ký kiểm toán hiện tại được giữ nguyên.',
    'Restore this backup when the server next starts? Computers will need a new code and approval to connect again.': 'Khôi phục bản sao lưu này khi máy chủ khởi động lần tới? Các máy tính sẽ cần mã mới và được phê duyệt lại.',
    'Backup saved with its .sha256 file beside it. Keep both together, where only administrators can read them: the archive holds the server key.': 'Đã lưu bản sao lưu kèm tệp .sha256 bên cạnh. Giữ hai tệp cùng nhau, ở nơi chỉ quản trị viên đọc được: tệp sao lưu chứa khóa máy chủ.',
    'The backup is complete and intact (made {time} UTC).': 'Bản sao lưu đầy đủ và nguyên vẹn (tạo lúc {time} UTC).',
    'The backup is checked and will be restored the next time the server starts. Restart the server to restore it.': 'Bản sao lưu đã được kiểm tra và sẽ được khôi phục ở lần khởi động máy chủ tiếp theo. Hãy khởi động lại máy chủ để khôi phục.',
    'The .sha256 file is not beside the archive.': 'Không có tệp .sha256 bên cạnh tệp sao lưu.',
    'The archive does not match its .sha256 file.': 'Tệp sao lưu không khớp với tệp .sha256.',
    'This is not a Lifetime Server backup archive.': 'Đây không phải tệp sao lưu của Lifetime Server.',
    'Files are missing from the archive.': 'Tệp sao lưu bị thiếu tệp.',
    'The list of files in the archive is invalid.': 'Danh sách tệp trong bản sao lưu không hợp lệ.',
    'The archive was made by a newer Lifetime Server.': 'Bản sao lưu được tạo bởi Lifetime Server mới hơn.',
    'A file in the archive is damaged.': 'Một tệp trong bản sao lưu bị hỏng.',
    'A database in the archive cannot be restored by this server.': 'Máy chủ này không khôi phục được một cơ sở dữ liệu trong bản sao lưu.',
    'The audit trail in the archive does not verify.': 'Nhật ký kiểm toán trong bản sao lưu không hợp lệ.',
    'The key in the archive does not match its certificate.': 'Khóa trong bản sao lưu không khớp với chứng chỉ.',
    'Named administration channel unavailable (token only)': 'Kênh quản trị theo tài khoản không khả dụng (chỉ dùng mã thông báo)',
    'Backup restored at start': 'Đã khôi phục bản sao lưu khi khởi động',
    'Staged backup refused at start (kept as restore-failed.zip)': 'Bản sao lưu chờ khôi phục bị từ chối khi khởi động (giữ lại dưới tên restore-failed.zip)',
    '{action} this computer?\n\n{name}\nGroup: {group}\nID: {device_id}': '{action} máy tính này?\n\n{name}\nNhóm: {group}\nID: {device_id}',
    'Unnamed computer': 'Máy tính chưa đặt tên',
}
for _key, _text in _ACCESS_VI.items():
    EN.setdefault(_key, _key)
    VI.setdefault(_key, _text)
    SERVER_DASHBOARD_VI.setdefault(_key, _text)


# Stage 4: staged agent updates (Lifetime Server dashboard and the desktop Connect page).
_UPDATES_VI = {
    'Get Lifetime updates approved by the server': 'Nhận bản cập nhật Lifetime do máy chủ phê duyệt',
    'Downloaded and checked, then installed by your IT team. Off unless you turn it on.': 'Được tải về và kiểm tra, sau đó bộ phận IT cài đặt. Tắt cho đến khi bạn bật.',
    'Agent updates': 'Cập nhật tác nhân',
    'Offer an approved Lifetime agent package to computers whose owner allowed updates. They download it over their secure connection, check its SHA-256 (and its signature when it has one) and keep it ready; install_agent_windows.ps1 -StagedUpdate installs it, for example from a startup script. Start with a small share.': 'Cung cấp gói tác nhân Lifetime đã phê duyệt cho các máy mà chủ máy cho phép cập nhật. Máy tải gói qua kết nối bảo mật, kiểm tra SHA-256 (và chữ ký nếu có) rồi giữ sẵn; install_agent_windows.ps1 -StagedUpdate sẽ cài đặt, ví dụ từ tập lệnh khởi động. Hãy bắt đầu với một tỷ lệ nhỏ.',
    'Choose file': 'Chọn tệp',
    'Upload package': 'Tải gói lên',
    'Save share': 'Lưu tỷ lệ',
    'Pause': 'Tạm dừng',
    'Resume': 'Tiếp tục',
    'Paused': 'Đang tạm dừng',
    'Roll back': 'Quay lại bản trước',
    'Offered to': 'Cung cấp cho',
    'Package file': 'Tệp gói',
    'Version, e.g. 1.3.0': 'Phiên bản, ví dụ 1.3.0',
    'Published SHA-256 of the file': 'SHA-256 đã công bố của tệp',
    'Computer': 'Máy tính',
    'Status': 'Trạng thái',
    'Version': 'Phiên bản',
    'Reported': 'Thời điểm báo cáo',
    'Package {version} · {size} MB · SHA-256 {sha256}': 'Gói {version} · {size} MB · SHA-256 {sha256}',
    'rolled back': 'đã quay lại bản trước',
    'No package uploaded.': 'Chưa tải gói nào lên.',
    'Ready to install: {staged} · Installed: {installed} · Failed: {failed}': 'Sẵn sàng cài đặt: {staged} · Đã cài đặt: {installed} · Thất bại: {failed}',
    'Ready to install': 'Sẵn sàng cài đặt',
    'download failed': 'tải về không thành công',
    'wrong size': 'sai kích thước',
    'SHA-256 did not match': 'SHA-256 không khớp',
    'signature not valid': 'chữ ký không hợp lệ',
    'older version refused': 'đã từ chối phiên bản cũ hơn',
    'could not be saved': 'không lưu được',
    'earlier package': 'gói trước đó',
    'Agent packages (*.exe *.zip *.msi)': 'Gói tác nhân (*.exe *.zip *.msi)',
    'Roll back the agent update? Computers are offered the previous package again, or nothing if there is none, and drop a downloaded copy they have not installed.': 'Quay lại bản cập nhật tác nhân trước? Các máy sẽ được cung cấp lại gói trước đó, hoặc không gì nếu không có, và xóa bản đã tải nhưng chưa cài đặt.',
}
for _key, _text in _UPDATES_VI.items():
    EN.setdefault(_key, _key)
    VI.setdefault(_key, _text)
    SERVER_DASHBOARD_VI.setdefault(_key, _text)
# Dashboard widgets shared by both apps (lifetime_widgets_dashboard.py): chips, heartbeat, cards, health badge, empty states.
_DASHBOARD_WIDGETS_VI = {
    'Last 24 hours': '24 giờ qua',
    'Last 24 hours: online in {online} of 24 hours': '24 giờ qua: trực tuyến {online} trên 24 giờ',
    '{metric}, last 24 hours': '{metric}, 24 giờ qua',
    'No current problems': 'Không có sự cố hiện tại',
    'Nothing in this list': 'Danh sách này trống',
    'No incidents match this filter.': 'Không có sự cố nào khớp bộ lọc này.',
    'Memory & graphics': 'Bộ nhớ & đồ họa',
    '{free} free of {total}': 'trống {free} trên {total}',
    'No computers yet': 'Chưa có máy tính nào',
    'Add a computer with a one-time code; it shows here once you approve it.': 'Thêm máy tính bằng mã dùng một lần; máy sẽ hiện ở đây sau khi bạn phê duyệt.',
    'No report yet': 'Chưa có báo cáo',
    'No computers match': 'Không có máy tính phù hợp',
    'No network checks yet': 'Chưa có kiểm tra mạng',
    'Fill in a name and a printer, router or website below, then choose Save check.': 'Điền tên và máy in, bộ định tuyến hoặc trang web bên dưới, rồi chọn Lưu kiểm tra.',
    'Health readings are off': 'Chỉ số sức khỏe đang tắt',
    '{count} in 30 days': '{count} trong 30 ngày',
    'Good': 'Tốt',
    'Needs a look': 'Cần xem lại',
    'Poor': 'Kém',
    'Score: the average of the readings shown, each good 100, needs a look 50, poor 0. Stability counts unexpected shutdowns and crashes in the last 30 days: none is good, 1 or 2 needs a look, 3 or more is poor. Battery compares full charge with design capacity: 80% or more is good, 60 to 79% needs a look, below 60% is poor. A missing reading is left out; with none the score is unknown.':
        'Điểm: trung bình các chỉ số hiển thị, tốt 100, cần xem lại 50, kém 0. Độ ổn định đếm số lần tắt máy bất thường và sự cố trong 30 ngày qua: không có là tốt, 1 hoặc 2 cần xem lại, từ 3 trở lên là kém. Pin so dung lượng sạc đầy với dung lượng thiết kế: từ 80% là tốt, 60 đến 79% cần xem lại, dưới 60% là kém. Chỉ số thiếu được bỏ qua; nếu không có chỉ số nào thì điểm là không xác định.',
}
for _key, _text in _DASHBOARD_WIDGETS_VI.items():
    EN.setdefault(_key, _key)
    VI.setdefault(_key, _text)
    SERVER_DASHBOARD_VI.setdefault(_key, _text)


# Unique features: energy and cost from true awake time (lifetime_insights), both apps and the reports.
_INSIGHTS_VI = {
    'Energy': 'Năng lượng',
    'Energy and lifecycle': 'Năng lượng và vòng đời',
    'What each computer’s true awake time costs: energy, money, time on after hours, what to replace next and how healthy it is.':
        'Thời gian hoạt động thực của mỗi máy tính tốn bao nhiêu: điện năng, tiền, thời gian bật ngoài giờ, cần thay gì tiếp theo và máy khỏe đến đâu.',
    'Energy figures are unavailable. Showing the last data received, if any.': 'Không có số liệu năng lượng. Đang hiển thị dữ liệu nhận được gần nhất, nếu có.',
    'Energy and cost': 'Điện năng và chi phí',
    'Energy per day, last 30 days (kWh)': 'Điện năng mỗi ngày, 30 ngày qua (kWh)',
    'Top consumers, last 30 days': 'Máy dùng điện nhiều nhất, 30 ngày qua',
    'By group, last 30 days': 'Theo nhóm, 30 ngày qua',
    'Electricity price and typical power': 'Giá điện và công suất điển hình',
    'Price per kWh': 'Giá mỗi kWh',
    'Currency': 'Tiền tệ',
    'Typical laptop': 'Máy xách tay điển hình',
    'Typical desktop': 'Máy để bàn điển hình',
    'Power for a group or computer': 'Công suất cho một nhóm hoặc máy tính',
    'Set': 'Đặt',
    'Clear': 'Xóa',
    'Whole fleet': 'Toàn bộ máy',
    'Today': 'Hôm nay',
    'Cost': 'Chi phí',
    'Power draw': 'Công suất tiêu thụ',
    'Computers': 'Máy tính',
    '{watts} W · {how}': '{watts} W · {how}',
    'measured on battery': 'đo khi chạy pin',
    'estimated: set for this computer': 'ước tính: đặt cho máy này',
    'estimated: set for its group': 'ước tính: đặt cho nhóm của máy',
    'estimated: typical laptop': 'ước tính: máy xách tay điển hình',
    'estimated: typical desktop': 'ước tính: máy để bàn điển hình',
    'estimated: kind unknown, typical desktop': 'ước tính: không rõ loại máy, dùng máy để bàn điển hình',
    'Energy = awake hours × power draw. Power is measured only when the computer reported its own draw on battery; otherwise it is a typical wattage, and it says estimated. Computers that do not share their awake history are left out and counted as unknown.':
        'Điện năng = số giờ hoạt động × công suất tiêu thụ. Công suất chỉ là số đo khi máy tự báo mức tiêu thụ lúc chạy pin; nếu không, đó là công suất điển hình và được ghi là ước tính. Máy không chia sẻ lịch sử thời gian hoạt động được bỏ ra và tính là không rõ.',
    '{measured} of {known} computers measured on battery; the others are estimated from typical power. {unknown} do not share their awake history.':
        '{measured} trên {known} máy được đo khi chạy pin; các máy còn lại được ước tính theo công suất điển hình. {unknown} máy không chia sẻ lịch sử thời gian hoạt động.',
    'No shared awake history yet': 'Chưa có lịch sử thời gian hoạt động được chia sẻ',
    'No energy figures yet': 'Chưa có số liệu năng lượng',
    'Energy appears once approved computers share their awake history.': 'Số liệu năng lượng sẽ hiện khi các máy đã phê duyệt chia sẻ lịch sử thời gian hoạt động.',
    'Set: {list}': 'Đã đặt: {list}',
    'No group or computer has its own power set.': 'Chưa có nhóm hay máy tính nào được đặt công suất riêng.',
    '({count} readings)': '({count} lần đo)',
    'Energy in the period (kWh)': 'Điện năng trong kỳ (kWh)',
    'Power draw (W)': 'Công suất tiêu thụ (W)',
    'Power method': 'Cách xác định công suất',
    'Cost in the period': 'Chi phí trong kỳ',
    'Electricity price': 'Giá điện',
    '{price} per kWh': '{price} mỗi kWh',
    'Computers with energy figures': 'Số máy có số liệu điện năng',
    'Measured on battery': 'Đo khi chạy pin',
    'Estimated from typical power': 'Ước tính theo công suất điển hình',
    'Without shared awake history (unknown)': 'Không chia sẻ lịch sử thời gian hoạt động (không rõ)',
    'Power: {watts} W · {how}': 'Công suất: {watts} W · {how}',
    'Energy needs the awake history. Turn on History in Settings.': 'Cần lịch sử thời gian hoạt động để tính điện năng. Hãy bật Lịch sử trong Cài đặt.',
    'Power of this computer (0 = automatic)': 'Công suất của máy này (0 = tự động)',
    'Wattage must be between 1 and 2000 W': 'Công suất phải từ 1 đến 2000 W',
    'Currency must be a three-letter code such as VND': 'Tiền tệ phải là mã ba chữ cái, ví dụ VND',
    'Invalid electricity price': 'Giá điện không hợp lệ',
}
for _key, _text in _INSIGHTS_VI.items():
    EN.setdefault(_key, _key)
    VI.setdefault(_key, _text)
    SERVER_DASHBOARD_VI.setdefault(_key, _text)


# Unique features: after hours and the sleep-after-hours policy.
_AFTER_HOURS_VI = {
    'After hours': 'Ngoài giờ',
    'Hours on after hours per week (all computers)': 'Số giờ bật ngoài giờ mỗi tuần (tất cả máy)',
    'Most on after hours, last 7 days': 'Bật ngoài giờ nhiều nhất, 7 ngày qua',
    'By group, last 7 days': 'Theo nhóm, 7 ngày qua',
    'By room, last 7 days': 'Theo phòng, 7 ngày qua',
    'Sleep after hours for': 'Cho ngủ sau giờ làm việc với',
    'Turn on for this group': 'Bật cho nhóm này',
    'Turn off for this group': 'Tắt cho nhóm này',
    'Sleep after hours sends the ordinary single-computer Sleep command, as the account that turned it on, to each computer of the group whose owner allows remote power: once a night, 30 minutes after its hours close, within the usual rate limits, and recorded in the audit trail. It skips a computer that is offline, already has a command waiting or looks busy (Lifetime cannot see a keyboard or mouse, so busy means the processor is working), and does not try again that night if the computer is woken or its owner declines. Turning it on needs an Admin or Operator role given to your own account.':
        'Cho ngủ sau giờ làm việc gửi lệnh Ngủ thông thường cho từng máy, dưới tên tài khoản đã bật, tới mỗi máy trong nhóm mà chủ máy cho phép điều khiển nguồn từ xa: mỗi đêm một lần, 30 phút sau khi hết giờ, trong giới hạn tần suất thông thường, và được ghi vào nhật ký kiểm toán. Máy đang ngoại tuyến, đã có lệnh đang chờ hoặc có vẻ đang bận (Lifetime không thấy bàn phím hay chuột, nên bận nghĩa là bộ xử lý đang làm việc) sẽ được bỏ qua, và đêm đó không thử lại nếu máy được đánh thức hoặc chủ máy từ chối. Muốn bật cần vai trò Quản trị hoặc Vận hành được cấp cho chính tài khoản của bạn.',
    'its own business hours': 'giờ làm việc riêng của máy',
    'office hours': 'giờ hành chính',
    'Office hours (for computers without business hours of their own)': 'Giờ hành chính (cho máy không có giờ làm việc riêng)',
    'Opens': 'Mở',
    'Closes': 'Đóng',
    'Save hours': 'Lưu giờ',
    'Sleep after hours needs an Admin or Operator role given to your own Windows account.': 'Cho ngủ sau giờ làm việc cần vai trò Quản trị hoặc Vận hành được cấp cho chính tài khoản Windows của bạn.',
    'Hours used': 'Khung giờ áp dụng',
    'Estimated saving per month': 'Tiết kiệm ước tính mỗi tháng',
    'Sleep after hours': 'Ngủ sau giờ làm việc',
    'Room': 'Phòng',
    'Last 7 days: {hours} h awake outside hours on {known} computers, {kwh} kWh, {cost}. {unknown} do not share their hourly history.':
        '7 ngày qua: {hours} giờ hoạt động ngoài giờ trên {known} máy, {kwh} kWh, {cost}. {unknown} máy không chia sẻ lịch sử theo giờ.',
    'No shared hourly history yet': 'Chưa có lịch sử theo giờ được chia sẻ',
    '{cost} (est., {allowed} allow remote power)': '{cost} (ước tính, {allowed} máy cho phép điều khiển nguồn)',
    '{group}: on since {since}, sent as {name}': '{group}: bật từ {since}, gửi dưới tên {name}',
    'Sleep after hours is off for every group.': 'Cho ngủ sau giờ làm việc đang tắt với mọi nhóm.',
    'office hours {start}–{end}, {days}': 'giờ hành chính {start}–{end}, {days}',
    'This computer was on {hours} h after hours in the last 7 days ({span}): about {kwh} kWh, {cost}.':
        'Máy này đã bật {hours} giờ ngoài giờ trong 7 ngày qua ({span}): khoảng {kwh} kWh, {cost}.',
    'Not on after hours in the last 7 days ({span}).': 'Không bật ngoài giờ trong 7 ngày qua ({span}).',
    'Awake after hours, last 7 days (hours)': 'Thức ngoài giờ, 7 ngày qua (giờ)',
    'Energy after hours, last 7 days (kWh)': 'Điện năng ngoài giờ, 7 ngày qua (kWh)',
    'Cost after hours, last 7 days': 'Chi phí ngoài giờ, 7 ngày qua',
    'Without hourly history (unknown)': 'Không có lịch sử theo giờ (không rõ)',
    'Expected a time as HH:MM': 'Cần giờ dạng HH:MM',
    'Opening and closing times must differ': 'Giờ mở và giờ đóng phải khác nhau',
}
for _key, _text in _AFTER_HOURS_VI.items():
    EN.setdefault(_key, _key)
    VI.setdefault(_key, _text)
    SERVER_DASHBOARD_VI.setdefault(_key, _text)


# Unique features: replacement forecasts and the replacement plan.
_FORECAST_VI = {
    'Replacement plan': 'Kế hoạch thay thế',
    'Forecasts from each computer’s own history: when its battery falls below the replacement level and when its system disk runs out of room, plus disks that report a problem. A date is given only with at least 4 readings over 30 days; otherwise it says not enough data, with what it has.':
        'Dự báo từ lịch sử của chính mỗi máy: khi nào pin giảm dưới mức cần thay và khi nào ổ đĩa hệ thống hết chỗ, cùng các ổ đĩa báo lỗi. Chỉ đưa ra ngày khi có ít nhất 4 lần đo trong 30 ngày; nếu không sẽ ghi chưa đủ dữ liệu, kèm số liệu đang có.',
    'Now and next quarter': 'Ngay bây giờ và quý tới',
    'Later this year': 'Cuối năm nay',
    'Forecast per computer': 'Dự báo theo từng máy',
    'Replace battery below': 'Thay pin khi dưới',
    'Act when disk free below': 'Xử lý khi ổ đĩa trống dưới',
    'Battery price': 'Giá pin',
    'Disk price': 'Giá ổ đĩa',
    'Part': 'Bộ phận',
    'When': 'Khi nào',
    'Why': 'Lý do',
    'System disk space': 'Dung lượng ổ đĩa hệ thống',
    'Age': 'Tuổi máy',
    'Awake so far': 'Tổng thời gian hoạt động',
    'Nothing due.': 'Chưa có gì cần thay.',
    '{count} parts · budget {total} for {priced} priced, {unpriced} without a price': '{count} bộ phận · ngân sách {total} cho {priced} bộ phận có giá, {unpriced} chưa có giá',
    '{years} years': '{years} năm',
    'now: the disk reports {health}': 'ngay: ổ đĩa báo {health}',
    '{date} (from {points} readings over {days} days)': '{date} (từ {points} lần đo trong {days} ngày)',
    'now ({latest} %, limit {threshold} %)': 'ngay ({latest} %, ngưỡng {threshold} %)',
    'not falling ({points} readings over {days} days)': 'không giảm ({points} lần đo trong {days} ngày)',
    'not within ten years ({points} readings over {days} days)': 'không trong vòng mười năm ({points} lần đo trong {days} ngày)',
    'not enough data ({points} readings over {days} days)': 'chưa đủ dữ liệu ({points} lần đo trong {days} ngày)',
    'Disk {model} reports {health}': 'Ổ đĩa {model} báo {health}',
    'Battery health {latest} % now, replace below {threshold} %': 'Sức khỏe pin hiện {latest} %, thay khi dưới {threshold} %',
    'System disk {latest} % free now, act below {threshold} %': 'Ổ đĩa hệ thống hiện trống {latest} %, xử lý khi dưới {threshold} %',
    'Battery forecast': 'Dự báo pin',
    'System disk forecast': 'Dự báo ổ đĩa hệ thống',
    'Age (years)': 'Tuổi máy (năm)',
    'Awake hours so far': 'Tổng số giờ hoạt động',
    'Due now or next quarter': 'Cần thay ngay hoặc trong quý tới',
    'Due later this year': 'Cần thay trong năm nay',
    'Budget (parts with a price)': 'Ngân sách (bộ phận có giá)',
    '{total} ({unpriced} without a price)': '{total} ({unpriced} chưa có giá)',
    'Replacement forecasts are straight lines through each computer’s own readings (battery health, system disk free space). A date is given only with at least 4 readings over 30 days; otherwise the report says not enough data. They are estimates, not a diagnosis.':
        'Dự báo thay thế là đường thẳng qua các lần đo của chính mỗi máy (độ khỏe pin, dung lượng trống ổ đĩa hệ thống). Chỉ đưa ra ngày khi có ít nhất 4 lần đo trong 30 ngày; nếu không, báo cáo ghi chưa đủ dữ liệu. Đây là ước tính, không phải chẩn đoán.',
    'Invalid replacement threshold': 'Ngưỡng thay thế không hợp lệ',
    'Invalid unit prices': 'Đơn giá không hợp lệ',
    'Replacement forecast': 'Dự báo thay thế',
    'A date is given only with at least 4 readings over 30 days. Battery: replace below {battery} %. Disk: act below {disk} % free.':
        'Chỉ đưa ra ngày khi có ít nhất 4 lần đo trong 30 ngày. Pin: thay khi dưới {battery} %. Ổ đĩa: xử lý khi trống dưới {disk} %.',
}
for _key, _text in _FORECAST_VI.items():
    EN.setdefault(_key, _key)
    VI.setdefault(_key, _text)
    SERVER_DASHBOARD_VI.setdefault(_key, _text)


# Unique features: the health score per computer, room, group and fleet.
_SCORE_VI = {
    'Health score': 'Điểm sức khỏe',
    'Known': 'Có số liệu',
    'System disk free': 'Ổ đĩa hệ thống còn trống',
    'Lifetime agent': 'Tác nhân Lifetime',
    'Does not apply': 'Không áp dụng',
    'up to date': 'mới nhất',
    'older': 'cũ hơn',
    'Average of {known} computers with a score; {unknown} have no reading yet and are left out.':
        'Trung bình của {known} máy có điểm; {unknown} máy chưa có số liệu nên được bỏ ra.',
    'No computer shares a health reading yet.': 'Chưa có máy nào chia sẻ số liệu sức khỏe.',
    'Health score (0-100)': 'Điểm sức khỏe (0-100)',
    'Score part: {part}': 'Thành phần điểm: {part}',
    'Average health score (0-100)': 'Điểm sức khỏe trung bình (0-100)',
    'Computers with a score': 'Số máy có điểm',
    'Computers without a reading (left out)': 'Số máy chưa có số liệu (bỏ ra)',
    'Score: the average of the readings shown, each good 100, needs a look 50, poor 0. Stability: unexpected shutdowns and crashes in 30 days, none good, 1 or 2 needs a look, 3 or more poor. Battery: full charge against design capacity, 80% or more good, 60 to 79% needs a look, below 60% poor. System disk free: 20% or more good, 10 to 19% needs a look, below 10% poor. Temperature: below 80 °C good, 80 to 89 °C needs a look, 90 °C or more poor. Lifetime agent: up to date good, older needs a look. An unknown reading is shown but left out of the score; with none the score is unknown.':
        'Điểm: trung bình các chỉ số hiển thị, tốt 100, cần xem lại 50, kém 0. Độ ổn định: số lần tắt máy bất thường và sự cố trong 30 ngày, không có là tốt, 1 hoặc 2 cần xem lại, từ 3 trở lên là kém. Pin: dung lượng sạc đầy so với thiết kế, từ 80% là tốt, 60 đến 79% cần xem lại, dưới 60% là kém. Ổ đĩa hệ thống còn trống: từ 20% là tốt, 10 đến 19% cần xem lại, dưới 10% là kém. Nhiệt độ: dưới 80 °C là tốt, 80 đến 89 °C cần xem lại, từ 90 °C là kém. Tác nhân Lifetime: mới nhất là tốt, cũ hơn cần xem lại. Chỉ số chưa xác định vẫn được hiển thị nhưng không tính vào điểm; nếu không có chỉ số nào thì điểm là chưa xác định.',
}
for _key, _text in _SCORE_VI.items():
    EN.setdefault(_key, _key)
    VI.setdefault(_key, _text)
    SERVER_DASHBOARD_VI.setdefault(_key, _text)

# The AppImage on a computer where Lifetime Server is installed asks which program to open.
_APPIMAGE_VI = {
    'Lifetime Server is installed on this computer. Which program do you want to open?':
        'Máy tính này có cài Lifetime Server. Bạn muốn mở chương trình nào?',
    'Lifetime Server (dashboard)': 'Lifetime Server (bảng điều khiển)',
    "Lifetime (this computer's awake time)": 'Lifetime (thời gian hoạt động của máy này)',
    'Remember my choice': 'Ghi nhớ lựa chọn của tôi',
    'Ask again which program the AppImage opens': 'Hỏi lại AppImage sẽ mở chương trình nào',
}
VI.update(_APPIMAGE_VI)
EN.update({key: key for key in _APPIMAGE_VI})
