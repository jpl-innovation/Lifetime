## Lifetime 1.0.1

Track how long your computer has really been awake, excluding sleep and hibernation. Made by JPL Innovation.

### Download

- **Windows 10/11:** `Lifetime-Windows.zip` -> unzip -> double-click **Install Lifetime.cmd**
- **Linux:** `Lifetime-Linux.tar.gz` -> `tar -xzf Lifetime-Linux.tar.gz && cd Lifetime-Linux && bash install_linux.sh`

Windows may show a SmartScreen warning because the programs are not code-signed: choose **More info -> Run anyway**.
Check your download against `SHA256SUMS.txt`.

### What is new in 1.0.1

- **Linux Verify:** a boot with no suspend events is now reported correctly instead of "journal could not be read"; the journal gets a longer timeout, and gaps over one minute are shown in minutes and seconds.
- **Linux Wi-Fi:** signal now matches NetworkManager; link speed is shown for Wi-Fi adapters.
- **Linux graphics:** friendly adapter name (for example "Intel Iris Xe Graphics") and the graphics clock.
- **Python 3.10:** fixed a crash when the window closes during its start-up animation, and Windows history import on Python 3.10.
- Python 3.9 is no longer supported (end of life). Python 3.10 or newer is required.

### Known issue

- On some Linux laptops Verify can show a gap of about the length of a suspend. Your total is not affected. See the README for how to help diagnose it.

### Highlights

- Live awake-time counter with seconds, plus a Verify button that cross-checks against the operating system's records
- System dashboard: power, network, graphics, CPU, memory, disks, live sparklines
- Insights: calendar heatmap, daily trend, cumulative hours, hour and weekday profiles, session lengths
- First-run consent: you choose what is tracked, and everything stays on your computer
- English and Tiếng Việt, switchable at any time
