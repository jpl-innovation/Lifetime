"""Small English/Vietnamese catalogue. English copy is also the stable lookup key."""
import datetime as dt
import locale
import os
import sys
import weakref


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
    'Start tracker': 'Bắt đầu theo dõi', 'Tracking active': 'Đang theo dõi', 'Tracker offline': 'Đã dừng theo dõi',
    'Ledger unavailable': 'Không đọc được dữ liệu',
    'Recording awake time\nTracker saves every 30 s · view refreshes every 1 s': 'Đang ghi thời gian hoạt động\nLưu dữ liệu mỗi 30 giây · cập nhật giao diện mỗi giây',
    'Saved totals are available.\nStart the tracker to resume recording.': 'Đang hiển thị dữ liệu đã lưu.\nBắt đầu theo dõi để tiếp tục ghi nhận.',
    'Baseline could not be saved': 'Không thể lưu số giờ ban đầu', 'Could not start tracker': 'Không thể bắt đầu theo dõi',
    'Starting tracker…': 'Đang bắt đầu theo dõi…',
    'Verify': 'Đối chiếu', 'Verifying…': 'Đang đối chiếu…',
    'Windows event log': 'nhật ký sự kiện Windows', 'system journal': 'nhật ký hệ thống',
    'Not available · verification timed out or could not complete': 'Chưa có kết quả · quá thời gian chờ hoặc không thể hoàn tất đối chiếu',
    'History rebuild could not start. Check that the history importer is installed.': 'Không thể khôi phục lịch sử. Hãy kiểm tra công cụ nhập lịch sử đã được cài đặt.',
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
    'Calendar · awake hours': 'Lịch hoạt động', 'Local days · white outline: today': 'Ngày theo giờ địa phương · viền trắng: hôm nay',
    'Daily awake hours': 'Thời gian hoạt động mỗi ngày',
    'Daily hours incl. boot credit · white: trailing 7-day average': 'Gồm thời gian từ lúc khởi động · đường trắng: trung bình 7 ngày',
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
    'Rebuild past history?': 'Khôi phục lịch sử trước đây?',
    'Reads retained System event logs on Windows or the system journal on Linux. Past totals may be incomplete if older logs are missing.': 'Đọc nhật ký hệ thống còn lưu trên Windows hoặc Linux. Tổng thời gian trước đây có thể chưa đầy đủ nếu thiếu nhật ký cũ.',
    'Start tracking at login?': 'Theo dõi khi đăng nhập?',
    'Starts the awake-time tracker when you sign in. You can also start it yourself from Lifetime.': 'Bắt đầu ghi thời gian hoạt động khi bạn đăng nhập. Bạn cũng có thể tự bắt đầu từ Lifetime.',
    'Yes, rebuild from system logs': 'Có, khôi phục từ nhật ký hệ thống', 'Yes, start tracking at login': 'Có, theo dõi khi đăng nhập',
    'No, start with current tracking': 'Không, chỉ theo dõi từ hiện tại', 'No, I will start it myself': 'Không, tôi sẽ tự bắt đầu',
    'Ready to start': 'Sẵn sàng bắt đầu', 'You can change these choices at any time in Settings.': 'Bạn có thể thay đổi các lựa chọn này bất cứ lúc nào trong Cài đặt.',
    'Only the readings you choose will be collected.': 'Chỉ thu thập những dữ liệu bạn đã chọn.',
    'Delete collected history': 'Xóa lịch sử đã thu thập', 'Back': 'Quay lại', 'Next': 'Tiếp tục',
    'Save settings': 'Lưu cài đặt', 'Start': 'Bắt đầu', 'Tracking\n': 'Nội dung theo dõi\n',
    '\n\nPast history: ': '\n\nLịch sử trước đây: ', 'Rebuild from system logs': 'Khôi phục từ nhật ký hệ thống', 'Do not rebuild': 'Không khôi phục',
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
    'rebuild.busy': 'Đang khôi phục lịch sử từ {log}…', 'rebuild.done': 'Đã khôi phục lịch sử từ {log}.',
    'rebuild.error': 'Không thể khôi phục lịch sử. {error}', 'ledger.error': 'Không đọc được dữ liệu: {error}',
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
    'verify.result': 'Verified against {log} · {events:,.1f} h vs {awake:,.1f} h (difference {difference:.1f} h)',
    'verify.compared': 'Compared with {log} · {events:,.1f} h vs {awake:,.1f} h (difference {difference:.1f} h) · Review the difference',
    'verify.ledger_error': '{message} · Ledger could not be refreshed',
    'rebuild.busy': 'Rebuilding past history from the {log}…', 'rebuild.done': 'Past history rebuilt from the {log}.',
    'rebuild.error': 'History rebuild unavailable. {error}', 'ledger.error': 'Could not read ledger: {error}',
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
