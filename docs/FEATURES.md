# Lifetime: every feature / Toàn bộ tính năng

Version 1.3.0. English first, then Tiếng Việt. Untested-on-real-machines notes are in RELEASE_NOTES.md.

## English

### Lifetime on a computer (Windows 10/11, Linux)

- **Awake time** counted every 30 seconds from a clock that stops during sleep and hibernation; changing the time
  cannot distort it. Live counter to the second, and **Verify** against Windows' event log or the Linux journal.
- **Earlier usage:** time before Lifetime was installed, rebuilt from the system logs as far back as they reach.
- **Any date range (new in 1.3.0):** Today, Last 7 days, Last 30 days, This month, Last month, This year, Last 12
  months, Last 5 years, All, or From/To dates. Activity and Uptime & downtime follow it, and Export report starts
  on the same dates. Days without evidence show as unknown, never zero, and a line says where the data starts.
- **Uptime & downtime page (new in 1.3.0):** per day awake, asleep, off and unknown; restarts, shutdowns and
  unexpected stops (crash or power loss); who or what asked for a restart; when it slept and woke; longest
  awake and longest off.
- **Activity:** calendar of busy days, hours per day, total hours, hour-of-day and weekday patterns, session lengths.
- **System:** battery, network and Wi-Fi, graphics, processor, memory, disks, live trends and hardware details.
- **Health:** crashes, battery wear, sleep, a 0–100 health score, and when the battery or disk will need replacing.
- **Reports:** PDF or CSV for any period, with energy and cost, and uptime and downtime.
- **Privacy:** you choose what is tracked; everything stays on the computer unless it is connected to your own
  server and you allowed each kind of data.
- **Look:** English or Vietnamese, light or dark, Classic or Glass.
- **Uninstall (new in 1.3.0):** from Windows' Installed apps; your history is kept unless you ask to remove it.

### Connecting to Lifetime Server (workplace computers)

- One code from the server's **Add device**; the computer finds the server by itself on the local network, or by a
  pasted connection line on another network. It checks the server is the real one before sending anything.
- **Diagnose connection (new in 1.3.0):** checks the name, port, discovery, network search, certificate, clock and
  Windows network profile, says what to do for each problem, and copies a report without secrets.
- **Copy diagnostics (new in 1.3.0):** versions, settings and recent log lines, with codes, passwords and tokens removed.
- Server: Online / Offline chip; awake time keeps counting while the server is away and is sent later.
- Owner's choices: awake time (required), daily history, performance, health, remote power, updates.

### Lifetime Server (Windows Server, Linux, container)

- **Install:** Windows Server by double-clicking **Install Lifetime Server.cmd** (no Python); Linux with one command;
  the dashboard opens from the app menu on Linux (new in 1.3.0).
- **Devices:** every computer with its status, specs, live readings, activity, health and history; groups; approvals.
- **Any date range on the server (new in 1.3.0):** a computer's awake time, Overview totals, Energy and reports for
  any period of up to 10 years, back as far as data exists.
- **Uptime & downtime per computer (new in 1.3.0):** awake, asleep, off and unknown per day; restarts and unexpected
  stops; availability during expected hours, number of outages and the longest one.
- **Fleet uptime bars (new in 1.3.0):** one row per computer, one bar per day (per week or month for long periods),
  coloured by availability, worst first.
- **Reports for any period (new in 1.3.0)** compared with the previous period; PDF or CSV, English or Vietnamese.
- **Alerts and incidents:** offline in working hours, low disk, busy processor, full memory, worn battery, high
  temperature, repeated crashes, old version; email and webhook (Teams, Slack, Telegram).
- **Energy and cost**, time left on after hours, replacement plan, health score per computer, group and fleet.
- **Network checks** for printers, routers, NAS and websites.
- **Self check (new in 1.3.0):** what computers need from the server (address, certificate, discovery, firewall,
  network profile, clock), with what to do.
- **Keep detailed records for (new in 1.3.0):** monitoring's raw online/offline history and alert records are
  trimmed after a chosen time (default 400 days); daily totals and awake-time records are never trimmed automatically.
  **Delete records before** now also removes monitoring's history before that date.
- **Roles** (Admin, Operator, Auditor, Viewer), **audit trail**, **one-file backups**, **staged agent updates**,
  remote sleep, restart, shut down and wake with the owner's permission.

## Tiếng Việt

### Lifetime trên máy tính (Windows 10/11, Linux)

- **Thời gian hoạt động** được đếm mỗi 30 giây bằng đồng hồ dừng lại khi máy ngủ hoặc ngủ đông; đổi giờ không làm
  sai số liệu. Đồng hồ chạy theo từng giây và nút **Kiểm chứng** so với nhật ký của Windows hoặc Linux.
- **Thời gian trước khi cài:** dựng lại từ nhật ký hệ thống, xa nhất trong phạm vi nhật ký còn lưu.
- **Chọn khoảng thời gian bất kỳ (mới ở 1.3.0):** Hôm nay, 7 ngày, 30 ngày, Tháng này, Tháng trước, Năm nay, 12 tháng,
  5 năm, Tất cả, hoặc Từ ngày – Đến ngày. Trang Hoạt động và Thời gian hoạt động & ngừng theo khoảng này, và Xuất báo cáo
  bắt đầu với cùng khoảng đó. Ngày không có dữ liệu hiện
  "không rõ", không bao giờ hiện 0, và có dòng cho biết dữ liệu bắt đầu từ đâu.
- **Trang Thời gian hoạt động & ngừng (mới ở 1.3.0):** mỗi ngày bao lâu máy chạy, ngủ, tắt và không rõ; số lần khởi động lại,
  tắt máy và dừng bất thường (treo máy, mất điện); ai hoặc chương trình nào yêu cầu khởi động lại; lúc ngủ và thức;
  lần chạy lâu nhất và lần tắt lâu nhất.
- **Hoạt động:** lịch các ngày dùng nhiều, số giờ mỗi ngày, tổng số giờ, theo giờ trong ngày và thứ trong tuần.
- **Hệ thống:** pin, mạng và Wi-Fi, card đồ họa, bộ xử lý, bộ nhớ, ổ đĩa, biểu đồ trực tiếp và chi tiết phần cứng.
- **Sức khỏe:** sự cố, độ chai pin, giấc ngủ, điểm sức khỏe 0–100, dự báo khi nào cần thay pin hoặc ổ đĩa.
- **Báo cáo:** PDF hoặc CSV cho khoảng bất kỳ, gồm điện năng, chi phí, thời gian hoạt động và ngừng hoạt động.
- **Quyền riêng tư:** bạn chọn những gì được ghi; mọi thứ nằm trên máy, trừ khi máy được kết nối với máy chủ của
  chính công ty bạn và bạn cho phép từng loại dữ liệu.
- **Giao diện:** tiếng Anh hoặc tiếng Việt, sáng hoặc tối, Classic hoặc Glass.
- **Gỡ cài đặt (mới ở 1.3.0):** từ mục Ứng dụng đã cài của Windows; lịch sử được giữ lại trừ khi bạn chọn xóa.

### Kết nối với Lifetime Server (máy tính ở nơi làm việc)

- Một mã lấy từ nút **Thêm thiết bị** trên máy chủ; máy tính tự tìm máy chủ trong mạng nội bộ, hoặc dán dòng kết nối
  khi ở mạng khác. Máy kiểm tra đúng máy chủ thật trước khi gửi bất cứ dữ liệu nào. Gõ số 0 hay chữ O đều được.
- **Chẩn đoán kết nối (mới ở 1.3.0):** kiểm tra tên, cổng, việc tự tìm máy chủ, chứng chỉ, đồng hồ và loại mạng của
  Windows, nói rõ cần làm gì với từng lỗi, và sao chép báo cáo không chứa thông tin bí mật.
- **Sao chép thông tin chẩn đoán (mới ở 1.3.0):** phiên bản, cài đặt và nhật ký gần đây, đã xóa mã, mật khẩu, token.
- Nhãn Máy chủ: Trực tuyến / Ngoại tuyến; thời gian vẫn được đếm khi mất kết nối và gửi sau.
- Chủ máy chọn chia sẻ: thời gian hoạt động (bắt buộc), lịch sử theo ngày, hiệu năng, sức khỏe, điều khiển nguồn, cập nhật.

### Lifetime Server (Windows Server, Linux, container)

- **Cài đặt:** trên Windows Server nhấp đúp **Install Lifetime Server.cmd** (không cần Python); trên Linux một lệnh;
  bảng điều khiển mở từ menu ứng dụng trên Linux (mới ở 1.3.0).
- **Thiết bị:** mọi máy với trạng thái, cấu hình, số liệu trực tiếp, hoạt động, sức khỏe và lịch sử; nhóm; phê duyệt.
- **Khoảng thời gian bất kỳ trên máy chủ (mới ở 1.3.0):** thời gian hoạt động của từng máy, tổng quan, điện năng và báo
  cáo cho mọi khoảng đến 10 năm, xa nhất đến khi còn dữ liệu.
- **Thời gian hoạt động và ngừng hoạt động của từng máy (mới ở 1.3.0):** chạy, ngủ, tắt, không rõ theo ngày; khởi động
  lại và dừng bất thường; tỷ lệ sẵn sàng trong giờ làm việc, số lần mất kết nối và lần lâu nhất.
- **Thanh thời gian hoạt động cho cả hệ thống (mới ở 1.3.0):** mỗi máy một hàng, mỗi ngày một thanh màu (mỗi tuần
  hoặc mỗi tháng khi khoảng thời gian dài), máy kém nhất xếp đầu.
- **Báo cáo cho mọi khoảng (mới ở 1.3.0)** có so sánh với kỳ trước; PDF hoặc CSV, tiếng Anh hoặc tiếng Việt.
- **Cảnh báo và sự cố:** ngoại tuyến trong giờ làm việc, ổ đĩa đầy, bộ xử lý quá tải, hết bộ nhớ, pin chai, nhiệt độ
  cao, sự cố lặp lại, phiên bản cũ; gửi email và webhook (Teams, Slack, Telegram).
- **Điện năng và chi phí**, thời gian để máy chạy ngoài giờ, kế hoạch thay thế, điểm sức khỏe theo máy, nhóm và cả hệ thống.
- **Kiểm tra mạng** cho máy in, router, NAS và trang web.
- **Tự kiểm tra (mới ở 1.3.0):** những gì máy tính cần từ máy chủ (địa chỉ, chứng chỉ, tự tìm máy chủ, tường lửa, loại
  mạng, đồng hồ), kèm cách khắc phục.
- **Giữ dữ liệu chi tiết trong (mới ở 1.3.0):** lịch sử trực tuyến/ngoại tuyến thô và bản ghi cảnh báo của phần giám
  sát được xóa sau thời gian đã chọn (mặc định 400 ngày); tổng theo ngày và bản ghi thời gian hoạt động không bao giờ
  tự động bị xóa. **Xóa bản ghi trước ngày** nay cũng xóa lịch sử giám sát trước ngày đó.
- **Vai trò** (Quản trị, Vận hành, Kiểm toán, Xem), **nhật ký kiểm toán**, **sao lưu một tệp**, **cập nhật tác nhân
  theo đợt**, cho máy ngủ, khởi động lại, tắt và bật từ xa khi chủ máy cho phép.
