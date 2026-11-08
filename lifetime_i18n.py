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
