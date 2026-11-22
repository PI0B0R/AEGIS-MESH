"""Time windows and repeat frequency.

A schedule says *when* scanning is allowed (window, e.g. 22:00-06:00) and *how often*
a new run starts (daily, weekly, every N hours). A run that does not finish inside its
window is paused when the window ends and resumed when the next window opens.
Times are local to the server (AEGIS_TZ or the system zone).
"""
import os
import re
import threading
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import db as store

TEMPLATES = {
    "nocny": {"name": "Codziennie w nocy", "frequency": "daily", "window_start": "22:00", "window_end": "06:00"},
    "weekend": {"name": "Raz w tygodniu (noc z soboty na niedzielę)", "frequency": "weekly:5",
                "window_start": "22:00", "window_end": "06:00"},
    "co-6-godzin": {"name": "Co 6 godzin, bez okna", "frequency": "every:6h", "window_start": "00:00", "window_end": "00:00"},
    "co-godzine": {"name": "Co godzinę (pokaz)", "frequency": "every:1h", "window_start": "00:00", "window_end": "00:00"},
}
FREQUENCY = re.compile(r"^(daily|weekly:[0-6]|every:([1-9][0-9]?)h)$")
CLOCK = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def zone() -> ZoneInfo:
    return ZoneInfo(os.environ.get("AEGIS_TZ", "Europe/Warsaw"))


def validate(frequency: str, window_start: str, window_end: str) -> None:
    if not FREQUENCY.match(frequency):
        raise ValueError("Częstotliwość: daily, weekly:0-6 (0 = poniedziałek) albo every:Nh.")
    if not CLOCK.match(window_start) or not CLOCK.match(window_end):
        raise ValueError("Okno czasowe podaj jako GG:MM, np. 22:00.")


def _clock(value: str) -> time:
    hours, minutes = map(int, value.split(":"))
    return time(hours, minutes)


def in_window(moment: datetime, start: str, end: str) -> bool:
    if start == end:                      # no window: always allowed
        return True
    t, a, b = moment.timetz().replace(tzinfo=None), _clock(start), _clock(end)
    return a <= t < b if a < b else (t >= a or t < b)


def window_opened_at(moment: datetime, start: str, end: str) -> datetime:
    """Start of the window that contains `moment` (yesterday's start for an overnight window after midnight)."""
    opened = moment.replace(hour=_clock(start).hour, minute=_clock(start).minute, second=0, microsecond=0)
    if start != end and opened > moment:
        opened -= timedelta(days=1)
    return opened


def is_due(frequency: str, start: str, end: str, last_started: datetime | None, moment: datetime) -> bool:
    if not in_window(moment, start, end):
        return False
    if frequency.startswith("every:"):
        hours = int(frequency[6:-1])
        return last_started is None or moment - last_started >= timedelta(hours=hours)
    opened = window_opened_at(moment, start, end) if start != end else moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if frequency.startswith("weekly:") and opened.weekday() != int(frequency[7:]):
        return False
    return last_started is None or last_started < opened


def next_start(frequency: str, start: str, end: str, last_started: datetime | None, moment: datetime,
               horizon_days: int = 8) -> datetime | None:
    """When the scheduler will start the next run (checked in 5-minute steps, like a slow clock)."""
    step = moment.replace(second=0, microsecond=0)
    step += timedelta(minutes=5 - step.minute % 5)
    for _ in range(horizon_days * 24 * 12):
        if is_due(frequency, start, end, last_started, step):
            return step
        step += timedelta(minutes=5)
    return None


class Scheduler:
    """Checks schedules every `interval` seconds: starts due runs, pauses at window end, resumes in the next window."""

    def __init__(self, db_path: str, engine, interval: float = 20):
        self.db_path, self.engine, self.interval = db_path, engine, interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick(datetime.now(zone()))
            except Exception:  # noqa: BLE001 - one bad tick must not kill the scheduler
                pass

    def tick(self, moment: datetime) -> list[str]:
        """One pass. Returns what it did (for logs and tests)."""
        actions = []
        db = store.connect(self.db_path)
        try:
            for sched in db.execute("SELECT * FROM schedules WHERE enabled=1 ORDER BY id").fetchall():
                start, end = sched["window_start"], sched["window_end"]
                open_now = in_window(moment, start, end)
                runs = db.execute("SELECT id, status FROM scan_runs WHERE schedule_id=? AND status IN "
                                  "('queued','running','paused') ORDER BY id DESC", (sched["id"],)).fetchall()
                active = self.engine.active_run_id()
                for run in runs:
                    if run["status"] in ("queued", "running") and run["id"] == active and not open_now:
                        self.engine.pause(run["id"])
                        actions.append(f"pause:{run['id']}")
                    elif run["status"] == "paused" and open_now and active is None:
                        self.engine.start(run["id"])
                        # The resumed run is this window's run: no second run in the same window.
                        db.execute("UPDATE schedules SET last_started_at=? WHERE id=?", (moment.isoformat(), sched["id"]))
                        actions.append(f"resume:{run['id']}")
                        active = run["id"]
                last = store.parse_time(sched["last_started_at"])
                if not runs and active is None and is_due(sched["frequency"], start, end,
                                                          last.astimezone(moment.tzinfo) if last else None, moment):
                    try:
                        run_id = self.engine.create_run("schedule", sched["id"])
                    except ValueError:
                        continue                  # no targets yet
                    db.execute("UPDATE schedules SET last_started_at=? WHERE id=?", (moment.isoformat(), sched["id"]))
                    self.engine.start(run_id)
                    actions.append(f"start:{run_id}")
                    active = run_id
        finally:
            db.close()
        return actions
