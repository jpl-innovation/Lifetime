"""Small English/Vietnamese catalogue. English copy is also the stable lookup key."""
import datetime as dt
import locale
import os
import sys
import weakref

_POWER_VI = {
    'Offline': 'Ngoại tuyến',
    'Disk almost full': 'Ổ đĩa gần đầy',
    'Battery worn': 'Pin đã chai',
    'Running hot': 'Đang quá nóng',
    'Unstable': 'Không ổn định',
    'Waiting to run a command': 'Đang chờ thực hiện lệnh',
    'Command accepted': 'Đã chấp nhận lệnh',
    'Unsupported command action': 'Thao tác lệnh không được hỗ trợ',
    'Remote power consent is required': 'Cần sự đồng ý cho phép điều khiển nguồn từ xa',
    'Power action failed': 'Thao tác nguồn thất bại',
    'Remote power': 'Điều khiển nguồn từ xa',
    'Invalid command action': 'Thao tác lệnh không hợp lệ',
    'Device must be approved and online': 'Thiết bị phải được phê duyệt và trực tuyến',
    'Device already has a pending command': 'Thiết bị đã có một lệnh đang chờ',
    'Unknown command': 'Lệnh không xác định',
    'Command is no longer pending': 'Lệnh không còn ở trạng thái chờ',
    'Wake requires an approved offline device with remote power consent': 'Đánh thức yêu cầu thiết bị ngoại tuyến đã được phê duyệt và đồng ý điều khiển nguồn từ xa',
    'No MAC addresses are available': 'Không có địa chỉ MAC',
    'Lifetime: your IT technician restarted this computer. Save your work.': 'Lifetime: kỹ thuật viên CNTT đã yêu cầu khởi động lại máy tính này. Hãy lưu công việc của bạn.',
    'Lifetime: your IT technician shut down this computer. Save your work.': 'Lifetime: kỹ thuật viên CNTT đã yêu cầu tắt máy tính này. Hãy lưu công việc của bạn.',
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
    'Settings': 'Cài đặt', 'Overview': 'Tổng quan', 'System': 'Hệ thống', 'Insights': 'Phân tích',
    'Total time awake': 'Tổng thời gian hoạt động',
    'First used': 'Ngày bắt đầu sử dụng', 'Boots observed': 'Số lần khởi động',
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
    'Ledger unavailable': 'Không đọc được dữ liệu',
    'Recording awake time\nTracker saves every 30 s · view refreshes every 1 s': 'Đang ghi thời gian hoạt động\nLưu dữ liệu mỗi 30 giây · cập nhật giao diện mỗi giây',
    'Saved totals are available.\nStart the tracker to resume recording.': 'Đang hiển thị dữ liệu đã lưu.\nBắt đầu theo dõi để tiếp tục ghi nhận.',
    'Baseline could not be saved': 'Không thể lưu số giờ ban đầu', 'Could not start tracker': 'Không thể bắt đầu theo dõi',
    'Starting tracker…': 'Đang bắt đầu theo dõi…',
    'Verify': 'Đối chiếu', 'Verifying…': 'Đang đối chiếu…',
    'Windows event log': 'nhật ký sự kiện Windows', 'system journal': 'nhật ký hệ thống',
    'Not available · verification timed out or could not complete': 'Chưa có kết quả · quá thời gian chờ hoặc không thể hoàn tất đối chiếu',
    'Earlier usage could not be added. Check that the history importer is installed.': 'Không thể thêm thời gian đã dùng trước đó. Hãy kiểm tra công cụ nhập lịch sử đã được cài đặt.',
    'Login tracking could not be updated': 'Không thể cập nhật tùy chọn theo dõi khi đăng nhập',
    'Login tracking could not be updated. Check your user task or service permissions.': 'Không thể cập nhật tùy chọn theo dõi khi đăng nhập. Hãy kiểm tra quyền đối với tác vụ hoặc dịch vụ của tài khoản.',
    'Not available': 'Chưa có dữ liệu', 'Unavailable': 'Chưa có dữ liệu', 'No record': 'Chưa ghi nhận',
    'Not available · Windows event log could not be read': 'Chưa có dữ liệu · không đọc được nhật ký sự kiện Windows',
    'Not available · Windows event log does not cover this boot': 'Chưa có dữ liệu · nhật ký Windows không bao quát lần khởi động này',
    'Not available · Windows event log has incomplete sleep intervals': 'Chưa có dữ liệu · nhật ký Windows thiếu các mốc ngủ và thức',
    'Not available · system journal could not be read': 'Chưa có dữ liệu · không đọc được nhật ký hệ thống',
    'Not available · journal has insufficient suspend evidence': 'Chưa có dữ liệu · nhật ký không đủ thông tin về thời gian ngủ',
    'Not available · journal has incomplete suspend intervals': 'Chưa có dữ liệu · nhật ký thiếu các mốc ngủ và thức',
    'Not available · journal clocks are inconsistent': 'Chưa có dữ liệu · các mốc thời gian trong nhật ký không nhất quán',
    'Compared journal suspend intervals with the awake clock': 'Đã đối chiếu thời gian ngủ trong nhật ký với bộ đếm thời gian hoạt động',
    'Reading local system…': 'Đang đọc thông tin hệ thống…',
    'System readings are off. Choose what to collect in Settings.': 'Đang tắt thông tin hệ thống. Chọn dữ liệu cần thu thập trong Cài đặt.',
    'Power': 'Nguồn điện', 'Network': 'Mạng', 'Graphics': 'Đồ họa', 'Computer': 'Máy tính',
    'Battery': 'Pin', 'State': 'Trạng thái', 'Time remaining': 'Thời gian còn lại',
    'Capacity': 'Dung lượng', 'Voltage': 'Điện áp', 'Design capacity': 'Dung lượng thiết kế', 'Battery wear': 'Độ chai pin',
    'No battery': 'Không có pin', '0 W · battery idle': '0 W · pin không nạp hoặc xả',
    'Charging': 'Đang sạc', 'Discharging': 'Đang dùng pin', 'Plugged in': 'Đang cắm nguồn',
    'Fully charged': 'Đã sạc đầy', 'Full': 'Đã sạc đầy', 'Unknown': 'Chưa xác định',
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
    'Uptime · incl. sleep': 'Thời gian từ lúc khởi động · gồm cả ngủ',
    'Awake since boot': 'Hoạt động từ lúc khởi động', 'Last boot': 'Khởi động gần nhất',
    'Fast Startup': 'Khởi động nhanh', 'On': 'Bật', 'Off': 'Tắt',
    'Local readings · 2 s / 10 s': 'Dữ liệu trên máy · mỗi 2 giây / 10 giây',
    'Awake time by hour of day': 'Thời gian hoạt động theo giờ trong ngày', 'Daily awake time': 'Thời gian hoạt động theo ngày',
    '30 days': '30 ngày', '90 days': '90 ngày', '1 year': '1 năm', 'All': 'Tất cả',
    '<15m': '<15p', '15–60m': '15–60p', '1–3h': '1–3g', '3–6h': '3–6g', '6–12h': '6–12g', '12h+': '12g+',
    'Needs recorded days': 'Cần dữ liệu theo ngày',
    'Recorded hourly totals / recorded days; boot credit excluded': 'Tổng theo giờ / số ngày đã ghi nhận; chưa gồm số giờ khởi động cũ',
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
    'Daily hours incl. boot credit · line: trailing 7-day average': 'Gồm thời gian từ lúc khởi động · đường: trung bình 7 ngày',
    'Cumulative lifetime hours': 'Tổng thời gian tích lũy', 'Includes imported history and the opening balance': 'Gồm lịch sử đã nhập và số giờ ban đầu',
    'Hour-of-day profile': 'Mức sử dụng theo giờ', 'Average awake hours in each local hour slot': 'Thời gian hoạt động trung bình ở mỗi khung giờ địa phương',
    'Weekday average': 'Trung bình theo thứ', 'Average per recorded weekday; today is a partial day': 'Trung bình theo thứ đã ghi nhận; hôm nay chưa kết thúc',
    'Session length distribution': 'Phân bố thời lượng phiên', 'Retained sessions, grouped by local start date': 'Các phiên đã lưu, nhóm theo ngày bắt đầu tại địa phương',
    'Local date': 'Ngày địa phương', 'Awake time': 'Thời gian hoạt động', 'Daily': 'Theo ngày', '7-day average': 'Trung bình 7 ngày',
    'Needs 7 days of data': 'Cần ít nhất 7 ngày dữ liệu', 'Needs 7 consecutive days': 'Cần 7 ngày liên tiếp',
    'Lifetime total': 'Tổng tích lũy', 'Live start date unavailable': 'Chưa có ngày bắt đầu theo dõi trực tiếp',
    'Opening balance included · ': 'Đã gồm số giờ ban đầu · ', 'Local hour': 'Giờ địa phương', 'Average awake': 'Hoạt động trung bình',
    'Weekday': 'Thứ', 'No recorded sessions in this range': 'Chưa có phiên nào trong khoảng này',
    ' total awake': ' hoạt động tổng cộng', 'Session length': 'Thời lượng phiên', 'Sessions': 'Số phiên',
    'Total awake time': 'Tổng thời gian hoạt động', 'Selected date range: ': 'Khoảng thời gian đã chọn: ',
    'Collecting live samples': 'Đang thu thập dữ liệu trực tiếp', 'Last 5 minutes · samples stay in memory': '5 phút gần nhất · dữ liệu chỉ lưu trong bộ nhớ',
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
    'Start tracking at login?': 'Theo dõi khi đăng nhập?',
    'Starts the awake-time tracker when you sign in. You can also start it yourself from Lifetime.': 'Bắt đầu ghi thời gian hoạt động khi bạn đăng nhập. Bạn cũng có thể tự bắt đầu từ Lifetime.',
    'Yes, add my earlier usage': 'Có, thêm thời gian đã dùng trước đó', 'Yes, start tracking at login': 'Có, theo dõi khi đăng nhập',
    'No, count from now on': 'Không, chỉ đếm từ bây giờ', 'No, I will start it myself': 'Không, tôi sẽ tự bắt đầu',
    'Ready to start': 'Sẵn sàng bắt đầu', 'You can change these choices at any time in Settings.': 'Bạn có thể thay đổi các lựa chọn này bất cứ lúc nào trong Cài đặt.',
    'Only the readings you choose will be collected.': 'Chỉ thu thập những dữ liệu bạn đã chọn.',
    'Delete collected history': 'Xóa lịch sử đã thu thập', 'Back': 'Quay lại', 'Next': 'Tiếp tục',
    'Save settings': 'Lưu cài đặt', 'Start': 'Bắt đầu', 'Tracking\n': 'Nội dung theo dõi\n',
    '\n\nEarlier usage: ': '\n\nThời gian đã dùng trước đó: ', 'Add from the computer\'s own log': 'Thêm từ nhật ký của máy', 'Do not add': 'Không thêm',
    '\nStart at login: ': '\nTheo dõi khi đăng nhập: ', 'Yes': 'Có', 'No': 'Không',
    'Settings could not be saved': 'Không thể lưu cài đặt', 'Delete collected history?': 'Xóa lịch sử đã thu thập?',
    'This resets recorded totals, starting hours and history. A backup will be kept on this computer. Tracking will continue from now.': 'Thao tác này đặt lại tổng thời gian, số giờ ban đầu và lịch sử. Một bản sao lưu sẽ được giữ trên máy. Việc theo dõi sẽ tiếp tục từ thời điểm hiện tại.',
    'History deleted': 'Đã xóa lịch sử', 'History could not be deleted': 'Không thể xóa lịch sử',
    'Language / Ngôn ngữ': 'Ngôn ngữ / Language', 'English': 'English', 'Tiếng Việt': 'Tiếng Việt',
    'Choose your language. You can change it at any time.': 'Chọn ngôn ngữ hiển thị. Bạn có thể thay đổi bất cứ lúc nào.',
    'Language': 'Ngôn ngữ', 'EN': 'EN', 'VI': 'VI',
    '&Yes': '&Có', '&No': '&Không', 'OK': 'Đồng ý', 'Cancel': 'Hủy',
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
    'rebuild.error': 'Không thể thêm thời gian đã dùng trước đó. {error}', 'ledger.error': 'Không đọc được dữ liệu: {error}',
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
    'history.logged': 'Boot time allocated using logged sleep and resume intervals.',
    'date.full': '{day} {month} {year}', 'date.short': '{day} {month}', 'date.time': '{date}, {time}',
    'date.range': '{start} – {end}',
    'time.hour': '{hour:02}:00', 'time.clock': '{hour:02}:{minute:02}', 'time.seconds': '{hour:02}:{minute:02}:{second:02}',
    'duration.days': '{days} d {hours} h {minutes} m', 'duration.hours': '{hours:,} h {minutes:02} m',
    'unit.hour': 'h', 'unit.hour.suffix': ' h',
    'hero.duration': 'hours · {days:,} d {hours:02} h {minutes:02} m {seconds:02} s',
    'hours.value': '{value:.2f} h', 'axis.hours': '{value:.3g} h',
    'days.in_use': '{days:,} days in use · estimated', 'step': 'Step {step} of 5',
    'verify.accessible': 'Verify awake time against {log}', 'verify.intro': 'Verify reads the {log} to check awake time.',
    'verify.reading': 'Reading the ledger and {log}…',
    'verify.gap': 'Compared with {log} · {events:,.1f} h vs {awake:,.1f} h · Gap of {gap} between the log and the awake clock',
    'history.banner': 'Lifetime only counts time since this computer last started. Add your earlier usage so the total shows how long it has really been used. This reads your computer\'s own log; nothing leaves this computer.',
    'verify.result': 'Verified against {log} · {events:,.1f} h vs {awake:,.1f} h (difference {difference:.1f} h)',
    'verify.compared': 'Compared with {log} · {events:,.1f} h vs {awake:,.1f} h (difference {difference:.1f} h) · Review the difference',
    'verify.ledger_error': '{message} · Ledger could not be refreshed',
    'rebuild.busy': 'Adding your earlier usage from the {log}…', 'rebuild.done': 'Earlier usage added from the {log}.',
    'rebuild.error': 'Could not add earlier usage. {error}', 'ledger.error': 'Could not read ledger: {error}',
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
    'Directly measured awake time (seconds)': 'Thời gian thức đo trực tiếp (giây)',
    'Reconstructed OS history (seconds)': 'Lịch sử khôi phục từ hệ điều hành (giây)',
    'The PDF page layout is unavailable.': 'Không xác định được bố cục trang PDF.',
    'PDF generation failed or produced no text.': 'Không tạo được PDF hoặc PDF không có nội dung chữ.',
    'Awake time is the time the computer was running and awake. It does not measure human activity, component health or remaining lifespan.': 'Thời gian thức là thời gian máy tính đang chạy và không ngủ. Chỉ số này không đo hoạt động của người dùng, tình trạng linh kiện hoặc tuổi thọ còn lại.',
    'OS date clue; true first use unknown': 'Mốc từ hệ điều hành; chưa rõ lần dùng đầu',
    'Could not open details': 'Không mở được chi tiết',
    'Live intervals are split across local days. Boot-time allocation uses retained sleep/resume evidence when available; otherwise its calendar distribution is estimated.': 'Các khoảng ghi trực tiếp được chia theo ngày địa phương. Thời gian khởi động được phân bổ theo bản ghi ngủ/thức còn lưu nếu có; nếu không, phân bố theo ngày là ước tính.',
    'History collection is disabled in Settings. Dated history and retained session records are excluded, including previously stored records.': 'Thu thập lịch sử đang tắt trong Cài đặt. Báo cáo không gồm lịch sử theo ngày hoặc phiên còn lưu, kể cả bản ghi cũ.',
    'History collection is disabled in Settings. Dated history is not included in reports.': 'Thu thập lịch sử đang tắt trong Cài đặt. Báo cáo không gồm lịch sử theo ngày.',
    'System information is disabled in Settings. Export does not change that choice.': 'Thông tin hệ thống đang tắt trong Cài đặt. Xuất báo cáo không thay đổi lựa chọn này.',
    'Daily allocation includes estimates; exact historical awake intervals cannot be recovered.': 'Phân bổ theo ngày có phần ước tính; không thể khôi phục chính xác các khoảng thức trong quá khứ.',
    'Verify compares the current boot only. It cannot prove complete historical coverage or recover missing OS logs.': 'Kiểm tra chỉ đối chiếu lần khởi động hiện tại. Kết quả không chứng minh lịch sử đầy đủ hoặc khôi phục được nhật ký hệ điều hành bị thiếu.',
    'Current-boot Verify difference (seconds)': 'Chênh lệch đối chiếu lần khởi động hiện tại (giây)',
    'Verify comparison captured': 'Thời điểm đối chiếu Kiểm tra',
    'Latest Verify (current boot only)': 'Kiểm tra gần nhất (chỉ lần khởi động hiện tại)',
}
VI.update(_RELEASE_TECHNICIAN_VI)
EN.update({key: key for key in _RELEASE_TECHNICIAN_VI})


# Optional technician identity, coverage and local report workflow.
_TECHNICIAN_VI = {'Department / location': 'Phòng ban / vị trí',
 'None recorded': 'Chưa có bản ghi',
 'Export report': 'Xuất báo cáo',
'Export': 'Xuất',
 'Device identity': 'Thông tin nhận diện thiết bị',
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
 'Include identity fields': 'Kèm thông tin thiết bị',
 'Include system fields': 'Kèm thông tin hệ thống',
 'Operating system': 'Hệ điều hành',
 'OS version': 'Phiên bản hệ điều hành',
 'Architecture': 'Kiến trúc',
 'Python version': 'Phiên bản Python',
 'Directly tracked': 'Ghi trực tiếp',
 'Reconstructed from OS records': 'Khôi phục từ bản ghi hệ điều hành',
 'Tracked and reconstructed': 'Ghi trực tiếp và khôi phục',
 'Partial': 'Một phần',
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
 'Status': 'Trạng thái',
 'Recording started': 'Bắt đầu ghi nhận',
 'Earliest record': 'Bản ghi sớm nhất',
 'First record': 'Bản ghi đầu tiên',
 'Last record': 'Bản ghi cuối cùng',
 'Notes': 'Ghi chú',
 'Coverage: {source} · {status}': 'Dữ liệu: {source} · {status}',
 'Report export is disabled by consent settings.': 'Cài đặt đồng ý đang tắt chức năng xuất báo cáo.',
 'Choose both start and end dates.': 'Chọn cả ngày bắt đầu và ngày kết thúc.',
 'Start date must not follow end date.': 'Ngày bắt đầu không được sau ngày kết thúc.',
 'Computer Awake-Time Report': 'Báo cáo thời gian máy tính thức',
 'Lifetime version': 'Phiên bản Lifetime',
 'Created': 'Ngày tạo',
 'Selected period': 'Khoảng thời gian đã chọn',
 'All available data': 'Tất cả dữ liệu hiện có',
 'Selected system information': 'Thông tin hệ thống đã chọn',
 'OS release': 'Phiên bản hệ điều hành',
 'Awake time (seconds)': 'Thời gian thức (giây)',
 'Manual baseline (seconds, not dated)': 'Số giờ nền nhập tay (giây, không có ngày)',
 'Retained session starts': 'Số lần bắt đầu phiên còn lưu',
 'Longest recorded session (seconds)': 'Phiên đã ghi dài nhất (giây)',
 'Observed boots (all available data)': 'Số lần khởi động đã ghi (tất cả dữ liệu)',
 'Coverage and sources': 'Phạm vi dữ liệu và nguồn',
 'Coverage': 'Phạm vi dữ liệu',
 'Recording began (proven timestamp)': 'Bắt đầu ghi (mốc thời gian có bằng chứng)',
 'Earliest recovered OS record': 'Bản ghi hệ điều hành sớm nhất khôi phục được',
 'First recorded date': 'Ngày có bản ghi đầu tiên',
 'Last recorded date': 'Ngày có bản ghi cuối cùng',
 'Limitations': 'Giới hạn',
 'Retained daily records': 'Bản ghi hằng ngày còn lưu',
 'Awake seconds': 'Số giây thức',
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
    'Company reporting is optional. Standalone Lifetime sends no company-server requests.': 'Báo cáo về công ty là tùy chọn. Lifetime độc lập không gửi yêu cầu đến máy chủ công ty.',
    'Pairing sends awake-time totals to the selected server. History and system information are optional. No employee account is required.': 'Ghép nối gửi tổng thời gian máy chạy và thức đến máy chủ đã chọn. Lịch sử và thông tin hệ thống là tùy chọn. Nhân viên không cần tài khoản.',
    'Reading local status…': 'Đang đọc trạng thái trên máy…',
    'Enrollment bundle': 'Gói ghép nối',
    'Paste an enrollment bundle or open its file.': 'Dán gói ghép nối hoặc mở tệp chứa gói.',
    'Open enrollment file': 'Mở tệp ghép nối',
    'Send dated awake-time history': 'Gửi lịch sử thời gian chạy và thức theo ngày',
    'Send basic system information': 'Gửi thông tin hệ thống cơ bản',
    'Include saved device identity when pairing': 'Kèm thông tin nhận diện máy đã lưu khi ghép nối',
    'Enable reporting to this company server': 'Bật báo cáo đến máy chủ công ty này',
    'Review the server address and fingerprint before pairing.': 'Kiểm tra địa chỉ máy chủ và dấu vân tay chứng chỉ trước khi ghép nối.',
    'Pair computer': 'Ghép nối máy tính',
    'Check enrollment': 'Kiểm tra ghép nối',
    'Refresh local status': 'Làm mới trạng thái trên máy',
    'Stop company reporting': 'Dừng báo cáo về công ty',
    'Pairing and reporting changes require a technician with administrator rights. Install the machine-agent startup task for background recording and reporting.': 'Ghép nối và thay đổi báo cáo cần kỹ thuật viên có quyền quản trị. Cài tác vụ khởi động của bộ ghi máy để ghi nhận và báo cáo trong nền.',
    'Stopping reporting prevents future requests. Data already stored on the server must be managed by its administrator or retention policy.': 'Dừng báo cáo sẽ ngăn các yêu cầu tiếp theo. Dữ liệu đã lưu trên máy chủ cần được quản trị viên hoặc chính sách lưu giữ của máy chủ xử lý.',
    'Server: {server}\nCertificate SHA-256: {fingerprint}': 'Máy chủ: {server}\nSHA-256 chứng chỉ: {fingerprint}',
    'Enter a valid enrollment bundle to review the server.': 'Nhập gói ghép nối hợp lệ để kiểm tra máy chủ.',
    'Reporting enabled': 'Đã bật báo cáo',
    'Server address': 'Địa chỉ máy chủ',
    'Reporting status': 'Trạng thái báo cáo',
    'Last successful sync': 'Lần đồng bộ thành công gần nhất',
    'Pending records': 'Bản ghi chờ gửi',
    'Discarded records': 'Bản ghi đã loại bỏ',
    'Company reporting could not be updated. Check administrator access, the server address and its connection, then try again.': 'Không thể cập nhật báo cáo về công ty. Kiểm tra quyền quản trị, địa chỉ máy chủ và kết nối rồi thử lại.',
    'All files (*)': 'Tất cả tệp (*)',
    'disabled': 'Đã tắt', 'enrolling': 'Đang gửi yêu cầu ghép nối',
    'pending': 'Chờ phê duyệt', 'approved': 'Đã phê duyệt', 'connected': 'Đã kết nối',
    'offline': 'Ngoại tuyến', 'denied': 'Bị từ chối', 'revoked': 'Đã thu hồi',
}
for _key, _text in _COMPANY_VI.items():
    EN[_key] = _key
    VI[_key] = _text
for _key, _text in {
    'Server exports use current reporting permissions. Optional system fields remain excluded unless selected and permitted by the device.': 'Báo cáo máy chủ dùng quyền báo cáo hiện tại. Các trường hệ thống tùy chọn chỉ được kèm khi đã chọn và máy tính cho phép.',
    'Preparing report…': 'Đang chuẩn bị báo cáo…',
    'Could not export the server report. Check the local service, administrator access and output folder, then try again.': 'Không thể xuất báo cáo máy chủ. Kiểm tra dịch vụ trên máy, quyền quản trị và thư mục lưu rồi thử lại.',
    'unreachable': 'Không thể liên lạc', 'conflict': 'Xung đột dữ liệu',
}.items():
    EN[_key] = _key
    VI[_key] = _text

SERVER_DASHBOARD_VI = {'Lifetime Server': 'Lifetime Server',
    'Awake time per day': 'Thời gian thức mỗi ngày', '14 days': '14 ngày', '30 days': '30 ngày',
    'Disk free': 'Dung lượng trống',
    'History is off on this computer': 'Máy tính này đang tắt lịch sử',
    'System readings are off on this computer': 'Máy tính này đang tắt thông tin hệ thống',
    'No awake time recorded yet': 'Chưa ghi nhận thời gian thức',
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
 'Loaded page only; filters apply. Counts are not fleet totals.': 'Chỉ trang đã tải, có áp dụng bộ '
                                                                  'lọc. Số lượng không phải tổng '
                                                                  'toàn bộ thiết bị.',
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
 'Search name or asset tag': 'Tìm tên hoặc mã tài sản',
 'Group': 'Nhóm',
 'All groups (exact department filter)': 'Tất cả nhóm (lọc chính xác phòng ban)',
 'Status': 'Trạng thái',
 'Page size': 'Số dòng mỗi trang',
 'Previous': 'Trước',
 'Next': 'Tiếp',
 'Page {page} · {count} devices': 'Trang {page} · {count} thiết bị',
 'Last report received': 'Lần nhận báo cáo gần nhất',
 'Device ID': 'Mã thiết bị',
 'No devices match. Create a pairing code in Administration to enroll a computer.': 'Không có '
                                                                                    'thiết bị phù '
                                                                                    'hợp. Tạo mã '
                                                                                    'ghép nối '
                                                                                    'trong Quản '
                                                                                    'trị để đăng '
                                                                                    'ký máy tính.',
 'Select a device to view its usage, permissions and server-owned identity.': 'Chọn thiết bị để '
                                                                              'xem thời gian sử '
                                                                              'dụng, quyền và '
                                                                              'thông tin do máy '
                                                                              'chủ quản lý.',
 'Device details': 'Chi tiết thiết bị',
 'Server-owned identity': 'Thông tin do máy chủ quản lý',
 'Save identity': 'Lưu thông tin',
 'Revoke credential': 'Thu hồi thông tin xác thực',
 'Enrollment requests': 'Yêu cầu đăng ký',
 'Approve': 'Phê duyệt',
 'Deny': 'Từ chối',
 'Export selected': 'Xuất thiết bị đã chọn',
 'Export fleet': 'Xuất toàn bộ thiết bị',
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
 'Share this bundle only with the technician pairing the computer. Approval is still required.': 'Chỉ '
                                                                                                 'chia '
                                                                                                 'sẻ '
                                                                                                 'gói '
                                                                                                 'này '
                                                                                                 'với '
                                                                                                 'kỹ '
                                                                                                 'thuật '
                                                                                                 'viên '
                                                                                                 'ghép '
                                                                                                 'nối '
                                                                                                 'máy '
                                                                                                 'tính. '
                                                                                                 'Vẫn '
                                                                                                 'cần '
                                                                                                 'phê '
                                                                                                 'duyệt.',
 'Expires at {time}': 'Hết hạn lúc {time}',
 'Expires {date} at {time}': 'Hết hạn ngày {date} lúc {time}',
 'Use this instead when the computer cannot find the server automatically.': 'Dùng dòng này khi máy tính không tự tìm thấy máy chủ.',
 'Retention': 'Lưu giữ dữ liệu',
 'Delete records before (UTC)': 'Xóa bản ghi trước ngày (UTC)',
 'Apply retention': 'Áp dụng thời hạn lưu',
 'Backup and restore': 'Sao lưu và khôi phục',
 'Save backup': 'Lưu bản sao',
 'Restore backup': 'Khôi phục bản sao',
 'Restore invalidates every device credential, pairing code and polling token. Computers must enroll again.': 'Khôi '
                                                                                                              'phục '
                                                                                                              'vô '
                                                                                                              'hiệu '
                                                                                                              'hóa '
                                                                                                              'mọi '
                                                                                                              'thông '
                                                                                                              'tin '
                                                                                                              'xác '
                                                                                                              'thực '
                                                                                                              'thiết '
                                                                                                              'bị, '
                                                                                                              'mã '
                                                                                                              'ghép '
                                                                                                              'nối '
                                                                                                              'và '
                                                                                                              'mã '
                                                                                                              'thăm '
                                                                                                              'dò. '
                                                                                                              'Máy '
                                                                                                              'tính '
                                                                                                              'phải '
                                                                                                              'đăng '
                                                                                                              'ký '
                                                                                                              'lại.',
 'Continue?': 'Tiếp tục?',
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
 'Immediately reject this device credential? Re-enrollment requires approval.': 'Từ chối ngay '
                                                                                'thông tin xác '
                                                                                'thực của thiết '
                                                                                'bị? Đăng ký lại '
                                                                                'cần phê duyệt.',
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
 'Interval not loaded. Refresh to retry.': 'Chưa tải được khoảng thời gian. Làm mới để thử lại.',
 'No pending requests': 'Không có yêu cầu đang chờ',
 'Configuration': 'Cấu hình',
 'No report has been received.': 'Chưa nhận được báo cáo nào.',
 'Current boundary (UTC): {time}': 'Mốc lưu giữ hiện tại (UTC): {time}',
 'All devices': 'Tất cả thiết bị',
 'Fleet overview; independent of the device search and filters.': 'Tổng quan toàn bộ thiết bị; độc '
                                                                  'lập với tìm kiếm và bộ lọc '
                                                                  'thiết bị.',
 'Latest cumulative snapshots (hours)': 'Tổng tích lũy gần nhất (giờ)',
 'One latest snapshot per device; snapshots are not added over time.': 'Một bản chụp gần nhất cho '
                                                                       'mỗi thiết bị; không cộng '
                                                                       'các bản chụp theo thời '
                                                                       'gian.',
 '{known} devices have snapshots; {unknown} have no snapshot. Totals include known devices only.': '{known} '
                                                                                                   'thiết '
                                                                                                   'bị '
                                                                                                   'có '
                                                                                                   'bản '
                                                                                                   'chụp; '
                                                                                                   '{unknown} '
                                                                                                   'thiết '
                                                                                                   'bị '
                                                                                                   'chưa '
                                                                                                   'có. '
                                                                                                   'Tổng '
                                                                                                   'chỉ '
                                                                                                   'gồm '
                                                                                                   'thiết '
                                                                                                   'bị '
                                                                                                   'có '
                                                                                                   'dữ '
                                                                                                   'liệu.',
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
    'Made by JPL Innovation': 'Phát triển bởi JPL Innovation',
    'Last 14 days': '14 ngày gần đây',
    'Last 14 days; open Activity': '14 ngày gần đây; mở Hoạt động',
    'View Activity': 'Xem hoạt động',
    'How this is counted': 'Cách tính thời gian',
    'Hide counting details': 'Ẩn chi tiết cách tính',
    'The ring repeats every 100 awake hours.': 'Vòng hiển thị lặp lại sau mỗi 100 giờ máy thức.',
    'Reports and details for this computer.': 'Báo cáo và thông tin chi tiết về máy tính này.',
    'Save a report of recorded awake time and coverage.': 'Lưu báo cáo thời gian máy thức và phạm vi dữ liệu.',
    'Manage the device name, asset tag and ownership.': 'Quản lý tên thiết bị, mã tài sản và chủ sở hữu.',
    'Review sources, dates and gaps in the record.': 'Xem nguồn, mốc thời gian và khoảng trống trong dữ liệu.',
    'Manage enrollment and report sharing with your company.': 'Quản lý đăng ký và chia sẻ báo cáo với công ty.',
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
    'Health reporting consent could not be updated. Ask the machine-agent administrator to update its Health policy.': 'Chưa cập nhật được quyền báo cáo sức khỏe. Hãy nhờ quản trị viên tác nhân máy cập nhật chính sách Sức khỏe.',
    '{count} events': '{count} sự kiện',
    '{count} cycles': '{count} chu kỳ',
    'Health': 'Sức khỏe',
    'Health and stability': 'Sức khỏe và độ ổn định',
    'Reads system shutdown and sleep events, battery capacity and machine dates.': 'Đọc sự kiện tắt máy và ngủ, dung lượng pin và ngày tháng của máy.',
    'Health readings are off. Enable Health and stability in Settings to read system events, battery condition and machine dates.': 'Đang tắt dữ liệu sức khỏe. Bật Sức khỏe và độ ổn định trong Cài đặt để đọc sự kiện hệ thống, tình trạng pin và ngày tháng của máy.',
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
    'Machine dates unavailable': 'Chưa có ngày tháng của máy',
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
    'Awake time': 'Thời gian chạy',
    'Organisation': 'Tổ chức',
    'Save organisation': 'Lưu tên tổ chức',
    'Device access': 'Quyền kết nối thiết bị',
    'Choose device': 'Chọn thiết bị',
    'Select a device in Devices.': 'Chọn một máy trong mục Thiết bị.',
    'Revoke selected device': 'Thu hồi thiết bị đã chọn',
    'Re-enable selected device': 'Cho phép thiết bị kết nối lại',
    'Re-enabling requires a new invitation and approval on the same computer. Its device ID and history are preserved.': 'Để kết nối lại, cần lời mời mới và phê duyệt trên cùng máy tính. Mã thiết bị và lịch sử được giữ nguyên.',
    'Local network': 'Mạng nội bộ',
    'Server unavailable': 'Không kết nối được máy chủ',
    'Server running': 'Máy chủ đang chạy',
    'Export report': 'Xuất báo cáo',
    'Fleet overview': 'Tổng quan thiết bị',
    'Report date': 'Ngày báo cáo',
    'Today · {date}': 'Hôm nay · {date}',
    'Add device': 'Thêm thiết bị',
    'Connected': 'Đã kết nối',
    'Awake today': 'Chạy hôm nay',
    'Review': 'Xem xét',
    'No devices yet. Add device': 'Chưa có thiết bị. Thêm thiết bị',
    'Search devices': 'Tìm thiết bị',
    'All groups': 'Tất cả nhóm',
    'Fleet devices': 'Danh sách thiết bị',
    'Device': 'Thiết bị',
    'Last report': 'Báo cáo gần nhất',
    'Daily awake hours': 'Giờ chạy mỗi ngày',
    'Last 7 days': '7 ngày qua',
    'Last 30 days': '30 ngày qua',
    'Chart range': 'Khoảng thời gian biểu đồ',
    'Known daily contributions only · device-local dates': 'Chỉ tính dữ liệu đã nhận · theo ngày trên từng máy',
    'No dated reports yet': 'Chưa có báo cáo theo ngày',
    'Local server: {name}': 'Máy chủ nội bộ: {name}',
    'Updated {time}': 'Cập nhật lúc {time}',
    'of {count} enrolled devices': 'trên {count} thiết bị đã đăng ký',
    'Check the reporting connection': 'Kiểm tra kết nối gửi báo cáo',
    '{count} reporting devices': '{count} thiết bị có dữ liệu',
    'Administrator review required': 'Cần quản trị viên xét duyệt',
    'Showing {start} to {end} of {total} devices': 'Hiển thị {start} đến {end} trên {total} thiết bị',
    '{hours} h {minutes:02} m': '{hours} giờ {minutes:02} phút',
    '{hours} h': '{hours} giờ',
    'h': 'g',
    '{n} s ago': '{n} giây trước',
    '{n} min ago': '{n} phút trước',
    '{n} h ago': '{n} giờ trước',
    'Open device': 'Mở thiết bị',
    'Select a device to view its recorded usage.': 'Chọn thiết bị để xem thời gian chạy đã ghi nhận.',
    'No group': 'Chưa có nhóm',
    'Recorded awake time': 'Thời gian chạy đã ghi nhận',
    'Client version': 'Phiên bản ứng dụng',
    'Partial': 'Một phần',
    'Tracking since': 'Bắt đầu ghi nhận',
    'Selected date': 'Ngày đã chọn',
    'Selected date awake time, out of 24 hours': 'Thời gian chạy ngày đã chọn, tối đa 24 giờ',
    'Synchronized': 'Đã đồng bộ',
    'Awaiting approval': 'Đang chờ duyệt',
    'Invitation used': 'Đã dùng lời mời',
    'Review pending approvals': 'Xem yêu cầu chờ duyệt',
    'Invitations': 'Lời mời kết nối',
    'Invitation': 'Lời mời',
    'Expires': 'Hết hạn',
    'Uses left': 'Lượt còn lại',
    'Wrong guesses': 'Lần nhập sai',
    'Revoke invitation': 'Thu hồi lời mời',
    'Expires in (hours)': 'Hết hạn sau (giờ)',
    'Listening: {address}\nCertificate SHA-256: {fingerprint}': 'Địa chỉ lắng nghe: {address}\nSHA-256 chứng chỉ: {fingerprint}',
    'Locked': 'Đã khóa',
    'Used': 'Đã dùng hết',
    'Expired': 'Đã hết hạn',
    'Active': 'Còn hiệu lực',
    'Made by JPL Innovation': 'Phát triển bởi JPL Innovation',
    'Connect a computer': 'Kết nối máy tính',
    'Open Company server on the computer, select this server and enter the code.': 'Mở Máy chủ công ty trên máy tính, chọn máy chủ này và nhập mã.',
    'Connection code': 'Mã kết nối',
    'Copy code': 'Sao chép mã',
    'Connection line': 'Dòng kết nối',
    'Copy connection line': 'Sao chép dòng kết nối',
    'Allowed uses: {uses}': 'Số lượt được dùng: {uses}',
    'Approval is required. Share privately. Revoke this invitation in Administration.': 'Cần được phê duyệt. Chỉ chia sẻ riêng. Thu hồi lời mời trong Quản trị.',
    'Done': 'Xong',
    'Choose a discovered server': 'Chọn máy chủ tìm thấy',
    'Company servers': 'Máy chủ công ty',
    'Find local servers': 'Tìm máy chủ nội bộ',
    'Discovery takes about 3 seconds. For another subnet or VPN, enter the server address or paste a connection line.': 'Tìm kiếm mất khoảng 3 giây. Với mạng khác hoặc VPN, nhập địa chỉ máy chủ hoặc dán dòng kết nối.',
    'Server name or IP (optional with connection line)': 'Tên hoặc IP máy chủ (có thể bỏ qua nếu dán dòng kết nối)',
    'Server address': 'Địa chỉ máy chủ',
    'Connection code or full connection line': 'Mã kết nối hoặc dòng kết nối đầy đủ',
    'Advanced: import an enrollment bundle': 'Nâng cao: nhập gói đăng ký',
    'The code will verify the certificate before enrollment data is sent. Server: {server}': 'Mã sẽ xác minh chứng chỉ trước khi gửi dữ liệu đăng ký. Máy chủ: {server}',
    'Choose a server, then enter its connection code.': 'Chọn máy chủ rồi nhập mã kết nối.',
    'No servers found. Enter the server name/IP and code, or paste a connection line.': 'Không tìm thấy máy chủ. Nhập tên/IP cùng mã, hoặc dán dòng kết nối.',
    'Linux reporting uses a separate user agent. Run install_linux.sh --company-agent and consent to the systemd user service for background reporting while signed in.': 'Linux dùng tiến trình riêng cho người dùng. Chạy install_linux.sh --company-agent và đồng ý bật dịch vụ systemd để gửi báo cáo nền khi đăng nhập.',
}
for _key, _text in SERVER_S1_VI.items():
    EN.setdefault(_key, _key)
    VI.setdefault(_key, _text)
    SERVER_DASHBOARD_VI.setdefault(_key, _text)

# Server Devices and Administration presentation (S2). Existing translations stay intact.
SERVER_S2_VI = {
    'Server name': 'Tên máy chủ',
    'Apply': 'Áp dụng',
    'Copy': 'Sao chép',
    'Pending approvals': 'Yêu cầu chờ duyệt',
    'No pending approvals': 'Không có yêu cầu chờ duyệt',
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
    'Operating system': 'Hệ điều hành', 'Storage': 'Ổ lưu trữ',
    'Battery design capacity': 'Dung lượng pin thiết kế',
    'Performance in the last 24 hours': 'Hiệu suất trong 24 giờ qua',
    'Commands are delivered on the next sync. Turn on uses Wake-on-LAN.':
        'Lệnh được gửi vào lần đồng bộ tiếp theo. Bật máy sử dụng Wake-on-LAN.',
    'Select a computer first.': 'Hãy chọn một máy tính trước.',
    'A request is in progress.': 'Đang xử lý yêu cầu.',
    'The owner has not allowed remote power control.': 'Chủ máy chưa cho phép điều khiển nguồn từ xa.',
    'This computer must be approved first.': 'Máy tính này cần được phê duyệt trước.',
    'A command is already pending for this computer.': 'Máy tính này đã có lệnh đang chờ.',
    'Turn on is available when the computer is offline.': 'Có thể bật máy khi máy tính ngoại tuyến.',
    'No network adapter address has been recorded.': 'Chưa ghi nhận địa chỉ bộ điều hợp mạng.',
    'This computer needs a recent sync first.': 'Máy tính này cần đồng bộ gần đây trước.',
    'Confirm {action} for {name}?': 'Xác nhận {action} cho {name}?',
    'Command status: {status}': 'Trạng thái lệnh: {status}',
    'Low disk space': 'Sắp hết dung lượng ổ đĩa', 'High temperature': 'Nhiệt độ cao',
    'Command pending': 'Lệnh đang chờ', 'CPU: {cpu} · RAM: {ram}': 'CPU: {cpu} · RAM: {ram}',
    '{metric}: {value}': '{metric}: {value}',
    'Disk free: {disk} · Battery: {battery} · Last seen: {seen}':
        'Ổ đĩa trống: {disk} · Pin: {battery} · Kết nối cuối: {seen}',
    'Actions for {name}': 'Thao tác cho {name}',
    '{name}: {free} free of {total}': '{name}: trống {free} trên {total}',
    'Uptime': 'Thời gian hoạt động', 'Last seen': 'Kết nối cuối',
    'CPU usage': 'Mức sử dụng CPU', 'Memory used': 'Bộ nhớ đã dùng',
    'Database size': 'Kích thước cơ sở dữ liệu', 'Just now': 'Vừa xong',
    'Sleep': 'Ngủ', 'Restart': 'Khởi động lại', 'Shut down': 'Tắt máy', 'Turn on': 'Bật máy',
    'command.pending': 'Đang chờ', 'command.delivered': 'Đã gửi',
    'command.done': 'Đã chấp nhận thực thi', 'command.failed': 'Thất bại',
    'command.expired': 'Đã hết hạn', 'command.cancelled': 'Đã hủy',
}
for _key, _text in SERVER_P2_VI.items():
    EN.setdefault(_key, _key)
    VI.setdefault(_key, _text)
    SERVER_DASHBOARD_VI.setdefault(_key, _text)

EN.update({'command.pending': 'Pending', 'command.delivered': 'Delivered', 'command.done': 'Accepted for execution', 'command.failed': 'Failed', 'command.expired': 'Expired', 'command.cancelled': 'Cancelled'})

# P4 connection flow: English keys are shared by desktop and server.
CONNECT_VI = {
    'Connect': 'Kết nối',
    'Connect to a server': 'Kết nối với máy chủ',
    'Enter the code shown on the server.': 'Nhập mã hiển thị trên máy chủ.',
    'Link this computer to a Lifetime Server on your network so it can see how this computer is doing.': 'Kết nối máy tính này với Lifetime Server trong mạng của bạn để máy chủ xem tình trạng máy.',
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
    'You get a one minute warning first.': 'Bạn sẽ được báo trước một phút.',
    'Finish': 'Hoàn tất',
    'Cancel': 'Hủy',
    'Waiting for approval': 'Đang chờ chấp thuận',
    '{server} needs to approve this computer.': '{server} cần chấp thuận máy tính này.',
    'Connected': 'Đã kết nối',
    'Last report: {time}': 'Báo cáo gần nhất: {time}',
    'Shared: {items}': 'Đang chia sẻ: {items}',
    'Change what is shared': 'Thay đổi thông tin chia sẻ',
    'Disconnect': 'Ngắt kết nối',
    'Disconnect from this server?': 'Ngắt kết nối với máy chủ này?',
    "Can't reach the server right now. Lifetime will keep trying.": 'Hiện không thể kết nối với máy chủ. Lifetime sẽ tiếp tục thử.',
    'Windows permission is needed to finish.': 'Cần quyền Windows để hoàn tất.',
    'Could not finish connecting. Check that the server is on and try again.': 'Không thể hoàn tất kết nối. Kiểm tra máy chủ đã bật rồi thử lại.',
    'This code cannot be used. Ask for a new code on the server.': 'Không thể dùng mã này. Hãy lấy mã mới trên máy chủ.',
    'Add a computer': 'Thêm máy tính',
    'On the computer, open Lifetime, choose Connect, and enter this code.': 'Trên máy tính, mở Lifetime, chọn Kết nối và nhập mã này.',
    'Copy': 'Sao chép',
    'Valid until {time}': 'Có hiệu lực đến {time}',
    'Other network?': 'Mạng khác?',
    '{name} wants to connect': '{name} muốn kết nối',
    '{name} connected': '{name} đã kết nối',
    'Connection declined.': 'Đã từ chối kết nối.',
    "Can't reach the Lifetime server. Make sure it is running.": 'Không thể kết nối với máy chủ Lifetime. Hãy kiểm tra máy chủ đang chạy.',
}
for _key, _text in CONNECT_VI.items():
    EN[_key] = _key
    VI[_key] = _text
    SERVER_DASHBOARD_VI[_key] = _text

_CONNECT_PLAIN_VI = {
    'No computers match. Choose Add device to connect a computer.': 'Không có máy tính phù hợp. Chọn Thêm thiết bị để kết nối máy tính.',
    'To reconnect, get a new code and approve this computer again. Its history is kept.': 'Để kết nối lại, lấy mã mới và chấp thuận máy tính này. Lịch sử được giữ lại.',
    'After restoring, computers need a new code and approval to connect again.': 'Sau khi khôi phục, máy tính cần mã mới và được chấp thuận để kết nối lại.',
    'Disconnect this computer? Connecting again needs approval.': 'Ngắt kết nối máy tính này? Kết nối lại cần được chấp thuận.',
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
