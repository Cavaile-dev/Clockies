#! python3.12

import atexit
import signal
import time
import json
import threading
import winsound
import os
import random
import tkinter as tk
from urllib import parse, request
from tkinter import simpledialog
from datetime import datetime, timedelta
from pynput import keyboard, mouse
import pyautogui
import ctypes
try:
    import pystray
    from PIL import Image
except ImportError:
    pystray = None
    Image = None


def load_local_env(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_local_env(os.path.join(os.path.dirname(__file__), ".env"))

FILE_STORAGE = "clock.json"
START_STOP_KEY = "`"
PLAY_KEY = keyboard.Key.shift_r
AUTONOMOUS_KEY = keyboard.Key.alt_gr
TIMER_KEY = keyboard.Key.ctrl_r
EXIT_KEY = keyboard.Key.esc
WIDTH, HEIGHT = pyautogui.size()

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
IDLE_THRESHOLD_SECONDS = int(os.getenv("IDLE_THRESHOLD_SECONDS", "300"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
LAST_TELEGRAM_CHAT_ID = ""
PENDING_ALERTS = []

pyautogui.PAUSE = 0.001
pyautogui.FAILSAFE = True

RECORD_MIN_INTERVAL_SECONDS = 0.008
RECORD_MIN_DISTANCE_PIXELS = 2
PLAYBACK_STOP_POLL_SECONDS = 0.02


# ============================================================== idle detection ==

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

def get_idle_duration():
    lastInputInfo = LASTINPUTINFO()
    lastInputInfo.cbSize = ctypes.sizeof(lastInputInfo)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lastInputInfo))
    millis = ctypes.windll.kernel32.GetTickCount() - lastInputInfo.dwTime
    return millis / 1000.0


def is_system_online():
    flags = ctypes.c_ulong()
    try:
        return bool(ctypes.windll.wininet.InternetGetConnectedState(ctypes.byref(flags), 0))
    except Exception:
        return False


def classify_status(idle_seconds, system_online, idle_threshold_seconds=IDLE_THRESHOLD_SECONDS):
    if not system_online:
        return "offline"
    if idle_seconds >= idle_threshold_seconds:
        return "idle"
    return "online"


def format_duration(seconds):
    seconds = int(seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def notify_local(title, message):
    def show():
        root = tk.Tk()
        root.title(title)
        root.attributes("-topmost", True)
        root.resizable(False, False)

        frame = tk.Frame(root, padx=16, pady=12)
        frame.pack()
        tk.Label(frame, text=title, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Label(frame, text=message, font=("Segoe UI", 10), justify="left").pack(anchor="w", pady=(8, 0))

        root.update_idletasks()
        width = root.winfo_width()
        height = root.winfo_height()
        x = root.winfo_screenwidth() - width - 24
        y = root.winfo_screenheight() - height - 80
        root.geometry(f"+{x}+{y}")
        root.after(6000, root.destroy)
        root.mainloop()

    threading.Thread(target=show, daemon=True).start()


def telegram_api(method, params=None, timeout=10):
    if not TELEGRAM_BOT_TOKEN:
        return None
    data = None
    if params:
        data = parse.urlencode(params).encode()
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        with request.urlopen(request.Request(url, data=data, method="POST"), timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def send_telegram(message, chat_id=None):
    chat_id = chat_id or TELEGRAM_CHAT_ID or LAST_TELEGRAM_CHAT_ID
    if not chat_id:
        return False
    result = telegram_api("sendMessage", {"chat_id": chat_id, "text": message})
    return bool(result and result.get("ok"))


def flush_pending_alerts():
    while PENDING_ALERTS:
        message = PENDING_ALERTS[0]
        if not send_telegram(f"📬 Missed alert delivered after reconnect\n\n{message}"):
            return False
        PENDING_ALERTS.pop(0)
    return True


def get_status_info():
    idle_sec = get_idle_duration()
    system_online = is_system_online()
    status = classify_status(idle_sec, system_online)
    last_active = datetime.now() - timedelta(seconds=idle_sec)

    return {
        "status": status,
        "idle_seconds": idle_sec,
        "system_online": system_online,
        "last_active": last_active.strftime("%Y-%m-%d %H:%M:%S"),
    }


def format_status_message(title, result):
    status_icons = {"online": "🟢", "idle": "🌙", "offline": "🔴"}
    status = result["status"]
    network = "online" if result["system_online"] else "offline"
    network_icon = "🌐" if result["system_online"] else "📴"
    return (
        f"{title}\n\n"
        f"{status_icons.get(status, '⚪')} Status: {status.upper()}\n"
        f"⏱️ Idle time: {format_duration(result['idle_seconds'])}\n"
        f"🕒 Last activity: {result['last_active']}\n"
        f"{network_icon} Network: {network.upper()}"
    )


def start_message():
    return (
        "👋 Clockies is online.\n\n"
        "I watch your computer locally and report activity changes through Telegram.\n\n"
        "🟢 Online: network connected and recent keyboard/mouse activity\n"
        "🌙 Idle: no keyboard/mouse activity for 5+ minutes\n"
        "🔴 Offline: network disconnected\n\n"
        "I only reply to commands, plus automatic alerts when your status changes."
    )


def online_message():
    return "🟢 Clockies is online.\n\nClockies siap menerima command Telegram."


def offline_message():
    return "🔴 Clockies is offline.\n\nScript Clockies sudah berhenti."


def help_message():
    return (
        "🧭 Commands\n\n"
        "/start - Show what this bot does\n"
        "/help - Show this command list\n"
        "/status - Show current computer status\n"
        "/play - Play recording mouse (Right Shift)\n"
        "/stop - Stop playback recording"
    )


# ============================================================= monitor thread ==

class StatusMonitor:
    def __init__(self):
        self.previous_status = None
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _loop(self):
        while self.running:
            result = get_status_info()
            current = result["status"]
            if self.previous_status is not None and current != self.previous_status:
                self._notify_change(self.previous_status, result)
            self.previous_status = current

            slept = 0
            while slept < POLL_INTERVAL_SECONDS and self.running:
                time.sleep(1)
                slept += 1

    def _notify_change(self, old_status, result):
        new_status = result["status"]
        if result["system_online"]:
            flush_pending_alerts()

        message = format_status_message(f"⚠️ Clockies Alert\n{old_status.upper()} → {new_status.upper()}", result)
        if not send_telegram(message):
            PENDING_ALERTS.append(message)
            notify_local("Clockies Status Change", message)


# ============================================================= telegram bot ==

class TelegramBot:
    def __init__(self, clockies=None):
        self.running = False
        self.thread = None
        self.offset = 0
        self.clockies = clockies

    def start(self):
        if self.running or not TELEGRAM_BOT_TOKEN:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _loop(self):
        while self.running:
            result = telegram_api("getUpdates", {"offset": self.offset, "timeout": 25}, timeout=30)
            if not result or not result.get("ok"):
                time.sleep(5)
                continue

            for update in result.get("result", []):
                self.offset = update["update_id"] + 1
                self._handle_update(update)

    def _handle_update(self, update):
        global LAST_TELEGRAM_CHAT_ID
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat_id = str(message.get("chat", {}).get("id", ""))
        if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
            return
        LAST_TELEGRAM_CHAT_ID = chat_id

        text = (message.get("text") or "").strip().lower()
        command = text.split(maxsplit=1)[0].split("@", 1)[0] if text else ""
        if command == "/start":
            self._cmd_start(chat_id)
        elif command == "/help":
            self._cmd_help(chat_id)
        elif command == "/status":
            self._cmd_status(chat_id)
        elif command == "/play":
            self._cmd_play(chat_id)
        elif command == "/stop":
            self._cmd_stop(chat_id)

    def _cmd_start(self, chat_id):
        send_telegram(start_message(), chat_id)

    def _cmd_help(self, chat_id):
        send_telegram(help_message(), chat_id)

    def _cmd_status(self, chat_id):
        send_telegram(format_status_message("📍 Clockies Status", get_status_info()), chat_id)

    def _cmd_play(self, chat_id):
        if self.clockies:
            self.clockies.start_playback(chat_id)

    def _cmd_stop(self, chat_id):
        if self.clockies:
            self.clockies.stop_playback(chat_id)


# ================================================================= clockies ==

class Clockies:
    def __init__(self):
        self.recording = False
        self.playing = False
        self.autonomous = False
        self.autonomous_thread = None
        self.actions = []
        self.last_time = 0
        self.last_recorded_position = None
        self.press_start_time = None
        self.timer_thread = None
        self.timer_active = False
        self.playback_lock = threading.Lock()
        self.playback_stop_event = threading.Event()
        self.keyboard_listener = None
        self.mouse_listener = None
        self.shutdown_lock = threading.Lock()
        self.shutdown_started = False
        self.lifecycle_started = False
        self.timer_resolution_active = False
        self.monitor = StatusMonitor()
        self.bot = TelegramBot(self)
        self.load_data()

    def beep(self, pitch):
        if pitch == "high":
            winsound.Beep(1000, 200)
        elif pitch == "low":
            winsound.Beep(400, 200)
        elif pitch == "exit":
            for _ in range(3):
                winsound.Beep(300, 100)
        elif pitch == "timer_start":
            winsound.Beep(800, 150)
            winsound.Beep(1000, 150)
        elif pitch == "timer_end":
            winsound.Beep(1000, 200)
            winsound.Beep(700, 200)
            winsound.Beep(400, 300)

    def save_data(self):
        with open(FILE_STORAGE, "w") as f:
            json.dump(self.actions, f)

    def load_data(self):
        if os.path.exists(FILE_STORAGE):
            try:
                with open(FILE_STORAGE, "r") as f:
                    self.actions = json.load(f)
            except:
                self.actions = []

    # --------------------------------------------------------- timer ui ----
    def show_timer_popup(self):
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        result = simpledialog.askstring(
            "Clockies Timer",
            "Masukkan durasi timer (menit):",
            parent=root,
        )
        root.destroy()

        if result is None:
            return
        try:
            minutes = float(result.strip())
            if minutes <= 0:
                raise ValueError
        except ValueError:
            return

        if self.timer_active:
            self.timer_active = False

        self.timer_active = True
        self.beep("timer_start")
        self.timer_thread = threading.Thread(target=self._run_timer, args=(minutes,), daemon=True)
        self.timer_thread.start()

    def _run_timer(self, minutes):
        deadline = time.time() + minutes * 60
        while time.time() < deadline:
            if not self.timer_active:
                return
            time.sleep(0.5)
        if not self.timer_active:
            return
        self.timer_active = False
        self._stop_all_actions()
        self.beep("timer_end")

    def _stop_all_actions(self):
        self.stop_playback(notify=False)
        if self.autonomous:
            self.autonomous = False

    # ------------------------------------------------------- key handler --
    def on_press(self, key):
        if key == EXIT_KEY:
            if self.press_start_time is None:
                self.press_start_time = time.time()
            return

        try:
            k = key.char
        except AttributeError:
            k = key

        if k == START_STOP_KEY:
            if not self.recording:
                self.actions = []
                self.last_time = time.perf_counter()
                self.last_recorded_position = None
                self.recording = True
                self.beep("high")
            else:
                self.recording = False
                self.save_data()
                self.beep("low")

        elif key == PLAY_KEY:
            if not self.recording and not self.autonomous:
                if not self.playing:
                    self.start_playback()
                else:
                    self.stop_playback()

        elif key == AUTONOMOUS_KEY:
            if not self.autonomous:
                if self.autonomous_thread and self.autonomous_thread.is_alive():
                    return
                self.autonomous = True
                self.beep("high")
                self.autonomous_thread = threading.Thread(target=self.run_autonomous, daemon=True)
                self.autonomous_thread.start()
            else:
                self.autonomous = False
                self.beep("low")

        elif key == TIMER_KEY:
            threading.Thread(target=self.show_timer_popup, daemon=True).start()

    def on_release(self, key):
        if key == EXIT_KEY:
            if self.press_start_time:
                duration = time.time() - self.press_start_time
                self.press_start_time = None
                if duration >= 0.5:
                    self.beep("exit")
                    self.shutdown()
                    return False

    # ----------------------------------------------------- mouse handlers --
    def on_move(self, x, y):
        if self.recording:
            if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                now = time.perf_counter()
                if self.last_recorded_position is not None:
                    last_x, last_y = self.last_recorded_position
                    distance = max(abs(x - last_x), abs(y - last_y))
                    elapsed = now - self.last_time
                    if (
                        distance < RECORD_MIN_DISTANCE_PIXELS
                        and elapsed < RECORD_MIN_INTERVAL_SECONDS
                    ):
                        return
                delay = now - self.last_time
                self.actions.append({"type": "move", "x": x, "y": y, "delay": delay})
                self.last_time = now
                self.last_recorded_position = (x, y)

    def on_click(self, x, y, button, pressed):
        if self.recording and pressed:
            now = time.perf_counter()
            delay = now - self.last_time
            self.actions.append({"type": "click", "x": x, "y": y, "delay": delay})
            self.last_time = now

    def on_scroll(self, x, y, dx, dy):
        if self.recording:
            now = time.perf_counter()
            delay = now - self.last_time
            self.actions.append({"type": "scroll", "x": x, "y": y, "dy": dy, "delay": delay})
            self.last_time = now

    # -------------------------------------------------------- play macro --
    def start_playback(self, chat_id=None):
        with self.playback_lock:
            if self.recording or self.autonomous:
                send_telegram("⚠️ Playback tidak bisa dimulai saat recording atau autonomous mode aktif.", chat_id)
                return False
            if self.playing:
                send_telegram("ℹ️ Recording sedang play.", chat_id)
                return False
            if not self.actions:
                send_telegram("⚠️ Belum ada recording mouse untuk dimainkan.", chat_id)
                return False
            self.playing = True
            self.playback_stop_event.clear()

        self.beep("high")
        send_telegram("▶️ Recording mouse sedang play.", chat_id)
        threading.Thread(target=self.play_macro, daemon=True).start()
        return True

    def stop_playback(self, chat_id=None, notify=True):
        with self.playback_lock:
            was_playing = self.playing
            self.playing = False
            self.playback_stop_event.set()

        if was_playing:
            self.beep("low")
            if notify:
                send_telegram("⏹️ Playback recording sudah stop.", chat_id)
        elif notify:
            send_telegram("ℹ️ Tidak ada recording yang sedang play.", chat_id)
        return was_playing

    def _wait_until_playback_deadline(self, deadline):
        while True:
            if self.playback_stop_event.is_set() or not self.playing:
                return False
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return True
            time.sleep(min(remaining, PLAYBACK_STOP_POLL_SECONDS))

    def _move_cursor(self, x, y):
        if os.name == "nt":
            ctypes.windll.user32.SetCursorPos(x, y)
        else:
            pyautogui.moveTo(x, y)

    def play_macro(self):
        previous_pause = pyautogui.PAUSE
        pyautogui.PAUSE = 0
        try:
            while self.playing:
                playback_start = time.perf_counter()
                elapsed = 0.0
                for act in self.actions:
                    elapsed += max(0.0, float(act.get("delay", 0)))
                    if not self._wait_until_playback_deadline(playback_start + elapsed):
                        break
                    tx = min(max(0, act["x"]), WIDTH - 1)
                    ty = min(max(0, act["y"]), HEIGHT - 1)
                    if act["type"] == "move":
                        self._move_cursor(tx, ty)
                    elif act["type"] == "click":
                        pyautogui.click(tx, ty)
                    elif act["type"] == "scroll":
                        pyautogui.scroll(int(act["dy"] * 100), x=tx, y=ty)
        finally:
            pyautogui.PAUSE = previous_pause
            with self.playback_lock:
                self.playing = False

    # ----------------------------------------------------- autonomous mode --
    def _do_random_mouse_move(self):
        margin = 50
        tx = random.randint(margin, WIDTH - margin)
        ty = random.randint(margin, HEIGHT - margin)
        steps = random.randint(5, 15)
        cx, cy = pyautogui.position()
        for i in range(1, steps + 1):
            if not self.autonomous:
                break
            nx = int(cx + (tx - cx) * i / steps)
            ny = int(cy + (ty - cy) * i / steps)
            pyautogui.moveTo(nx, ny)
            time.sleep(random.uniform(0.01, 0.04))

    def run_autonomous(self):
        tab_pool = [1, 2, 3, 4, 5, 6]
        scroll_amounts = [-8, -6, -5, 5, 6, 8, 10, -10, 12, -12]
        min_interval = 20
        max_interval = 50
        event_types = ["scroll", "mouse", "alt_tab"]
        weights = [50, 30, 20]
        last_alt_tab_tabs = None

        while self.autonomous:
            sleep_total = random.uniform(min_interval, max_interval)
            slept = 0
            while slept < sleep_total and self.autonomous:
                if random.random() < 0.08:
                    drift_x = random.randint(-8, 8)
                    drift_y = random.randint(-8, 8)
                    cx, cy = pyautogui.position()
                    nx = min(max(0, cx + drift_x), WIDTH - 1)
                    ny = min(max(0, cy + drift_y), HEIGHT - 1)
                    pyautogui.moveTo(nx, ny)
                time.sleep(0.5)
                slept += 0.5

            if not self.autonomous:
                break

            chosen = random.choices(event_types, weights=weights, k=1)[0]

            if chosen == "scroll":
                amount = random.choice(scroll_amounts)
                cx, cy = pyautogui.position()
                pyautogui.scroll(amount, x=cx, y=cy)
                if random.random() < 0.4:
                    time.sleep(random.uniform(0.3, 0.8))
                    pyautogui.scroll(random.choice(scroll_amounts), x=cx, y=cy)

            elif chosen == "mouse":
                self._do_random_mouse_move()

            elif chosen == "alt_tab":
                available_tabs = [t for t in tab_pool if t != last_alt_tab_tabs]
                num_tabs = random.choice(available_tabs)
                last_alt_tab_tabs = num_tabs
                pyautogui.keyDown("alt")
                time.sleep(0.05)
                for _ in range(num_tabs):
                    pyautogui.press("tab")
                    time.sleep(random.uniform(0.08, 0.18))
                pyautogui.keyUp("alt")
                time.sleep(random.uniform(0.2, 0.5))

    # --------------------------------------------------------- tray icon --
    def _tray_icon(self):
        if pystray is None or Image is None:
            return
        icon = Image.open(os.path.join(os.path.dirname(__file__), "alarm_claymorphism_red.ico"))
        def on_exit(icon, item):
            icon.stop()
            self.shutdown()
        menu = pystray.Menu(pystray.MenuItem("Exit", on_exit))
        pystray.Icon("clockies", icon, "Clockies", menu).run()

    def _begin_high_resolution_timer(self):
        if os.name != "nt" or self.timer_resolution_active:
            return
        try:
            self.timer_resolution_active = ctypes.windll.winmm.timeBeginPeriod(1) == 0
        except (AttributeError, OSError):
            self.timer_resolution_active = False

    def _end_high_resolution_timer(self):
        if not self.timer_resolution_active:
            return
        try:
            ctypes.windll.winmm.timeEndPeriod(1)
        finally:
            self.timer_resolution_active = False

    def shutdown(self):
        with self.shutdown_lock:
            if self.shutdown_started:
                return
            self.shutdown_started = True

        self._stop_all_actions()
        if self.lifecycle_started:
            send_telegram(offline_message())
        self.monitor.stop()
        self.bot.stop()
        if self.keyboard_listener:
            self.keyboard_listener.stop()
        if self.mouse_listener:
            self.mouse_listener.stop()
        self._end_high_resolution_timer()

    def _handle_signal(self, signum, frame):
        self.shutdown()

    # ---------------------------------------------------------------- run --
    def run(self):
        atexit.register(self.shutdown)
        self._begin_high_resolution_timer()
        signal.signal(signal.SIGINT, self._handle_signal)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, self._handle_signal)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, self._handle_signal)
        threading.Thread(target=self._tray_icon, daemon=True).start()
        self.monitor.start()
        self.bot.start()
        self.lifecycle_started = True
        send_telegram(online_message())
        try:
            with keyboard.Listener(
                on_press=self.on_press, on_release=self.on_release
            ) as k_listener, mouse.Listener(
                on_click=self.on_click, on_move=self.on_move, on_scroll=self.on_scroll
            ) as m_listener:
                self.keyboard_listener = k_listener
                self.mouse_listener = m_listener
                if self.shutdown_started:
                    k_listener.stop()
                    m_listener.stop()
                k_listener.join()
        finally:
            self.shutdown()


if __name__ == "__main__":
    Clockies().run()
