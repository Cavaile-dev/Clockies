#! python3.12

import time
import json
import threading
import winsound
import os
import random
import tkinter as tk
from tkinter import simpledialog
from pynput import keyboard, mouse
import pyautogui

FILE_STORAGE = "clock.json"
START_STOP_KEY = "`"
PLAY_KEY = keyboard.Key.shift_r
AUTONOMOUS_KEY = keyboard.Key.alt_gr
TIMER_KEY = keyboard.Key.ctrl_r
EXIT_KEY = keyboard.Key.esc
WIDTH, HEIGHT = pyautogui.size()

pyautogui.PAUSE = 0.001
pyautogui.FAILSAFE = True


class Clockies:
    def __init__(self):
        self.recording = False
        self.playing = False
        self.autonomous = False
        self.autonomous_thread = None
        self.actions = []
        self.last_time = 0
        self.press_start_time = None
        self.timer_thread = None
        self.timer_active = False
        self.load_data()

    # ------------------------------------------------------------------ beep --
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

    # --------------------------------------------------------------- storage --
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

    # ------------------------------------------------------------- timer ui --
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
        self.timer_thread = threading.Thread(
            target=self._run_timer, args=(minutes,), daemon=True
        )
        self.timer_thread.start()

    def _run_timer(self, minutes):
        seconds = minutes * 60
        deadline = time.time() + seconds

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
        if self.playing:
            self.playing = False
        if self.autonomous:
            self.autonomous = False

    # ---------------------------------------------------------- key handler --
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
                self.last_time = time.time()
                self.recording = True
                self.beep("high")
            else:
                self.recording = False
                self.save_data()
                self.beep("low")

        elif key == PLAY_KEY:
            if not self.recording and not self.autonomous:
                if not self.playing:
                    threading.Thread(target=self.play_macro, daemon=True).start()
                else:
                    self.playing = False
                    self.beep("low")

        elif key == AUTONOMOUS_KEY:
            if not self.autonomous:
                if self.autonomous_thread and self.autonomous_thread.is_alive():
                    return
                self.autonomous = True
                self.beep("high")
                self.autonomous_thread = threading.Thread(
                    target=self.run_autonomous, daemon=True
                )
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
                    os._exit(0)

    # ------------------------------------------------------- mouse handlers --
    def on_move(self, x, y):
        if self.recording:
            if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                now = time.time()
                delay = now - self.last_time
                self.actions.append({"type": "move", "x": x, "y": y, "delay": delay})
                self.last_time = now

    def on_click(self, x, y, button, pressed):
        if self.recording and pressed:
            now = time.time()
            delay = now - self.last_time
            self.actions.append({"type": "click", "x": x, "y": y, "delay": delay})
            self.last_time = now

    def on_scroll(self, x, y, dx, dy):
        if self.recording:
            now = time.time()
            delay = now - self.last_time
            self.actions.append(
                {"type": "scroll", "x": x, "y": y, "dy": dy, "delay": delay}
            )
            self.last_time = now

    # ---------------------------------------------------------- play macro --
    def play_macro(self):
        if not self.actions:
            return
        self.playing = True
        self.beep("high")

        while self.playing:
            for act in self.actions:
                if not self.playing:
                    break
                time.sleep(act["delay"])
                tx = min(max(0, act["x"]), WIDTH - 1)
                ty = min(max(0, act["y"]), HEIGHT - 1)

                if act["type"] == "move":
                    pyautogui.moveTo(tx, ty)
                elif act["type"] == "click":
                    pyautogui.click(tx, ty)
                elif act["type"] == "scroll":
                    pyautogui.scroll(int(act["dy"] * 100), x=tx, y=ty)

    # ------------------------------------------------------- autonomous mode --
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

    # ------------------------------------------------------------------ run --
    def run(self):
        with keyboard.Listener(
            on_press=self.on_press, on_release=self.on_release
        ) as k_listener, mouse.Listener(
            on_click=self.on_click, on_move=self.on_move, on_scroll=self.on_scroll
        ) as m_listener:
            k_listener.join()
            m_listener.join()


if __name__ == "__main__":
    Clockies().run()
