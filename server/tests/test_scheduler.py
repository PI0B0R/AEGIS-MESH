from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from aegis import db as store
from aegis.scheduler import Scheduler, in_window, is_due, validate
from test_engine import setup

TZ = ZoneInfo("Europe/Warsaw")


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)          # 2026-11-21 is a Saturday


def test_overnight_window():
    assert in_window(at(21, 23), "22:00", "06:00")
    assert in_window(at(22, 5, 59), "22:00", "06:00")
    assert not in_window(at(22, 6), "22:00", "06:00")
    assert not in_window(at(21, 12), "22:00", "06:00")
    assert in_window(at(21, 12), "08:00", "16:00") and not in_window(at(21, 16), "08:00", "16:00")
    assert in_window(at(21, 3), "00:00", "00:00")                       # no window


def test_daily_runs_once_per_window():
    assert is_due("daily", "22:00", "06:00", None, at(21, 22, 5))
    started = at(21, 22, 5)
    assert not is_due("daily", "22:00", "06:00", started, at(22, 2))  # same night
    assert is_due("daily", "22:00", "06:00", started, at(22, 22, 1))  # next night
    assert not is_due("daily", "22:00", "06:00", None, at(21, 12))    # outside the window


def test_weekly_and_every():
    assert is_due("weekly:5", "22:00", "06:00", None, at(21, 23))      # Saturday night
    assert is_due("weekly:5", "22:00", "06:00", None, at(22, 1))       # same night after midnight
    assert not is_due("weekly:5", "22:00", "06:00", None, at(22, 23))  # Sunday night
    assert is_due("every:6h", "00:00", "00:00", at(21, 6), at(21, 12))
    assert not is_due("every:6h", "00:00", "00:00", at(21, 6), at(21, 11, 59))


@pytest.mark.parametrize("args", [("hourly", "22:00", "06:00"), ("daily", "25:00", "06:00"), ("every:0h", "1:00", "2:00")])
def test_validation(args):
    with pytest.raises(ValueError):
        validate(*args)


def test_window_end_pauses_and_next_window_resumes(tmp_path, fake_nmap):
    path, cipher, conn, engine = setup(tmp_path, fake_nmap, text="10.0.0.0/24", measured_rate=100,
                                       min_chunk=8, max_chunk=8, chunk_seconds=1)
    conn.execute("INSERT INTO schedules (name, frequency, window_start, window_end, created_at) "
                 "VALUES ('noc', 'daily', '22:00', '06:00', ?)", (store.now(),))
    scheduler = Scheduler(path, engine)
    fake_nmap.gate.clear()                                              # subgroups hang until released
    assert scheduler.tick(at(21, 12)) == []                             # daytime: nothing
    actions = scheduler.tick(at(21, 22, 1))
    assert actions[0].startswith("start:")
    run_id = int(actions[0].split(":")[1])
    assert scheduler.tick(at(21, 23)) == []                             # still running, nothing to do
    assert scheduler.tick(at(22, 6, 0)) == [f"pause:{run_id}"]          # window closed
    engine.wait(run_id, 5)
    assert conn.execute("SELECT status FROM scan_runs WHERE id=?", (run_id,)).fetchone()[0] == "paused"
    assert scheduler.tick(at(22, 12)) == []                             # daytime: stays paused, no new run
    fake_nmap.gate.set()
    assert scheduler.tick(at(22, 22, 0)) == [f"resume:{run_id}"]        # next window continues the same run
    engine.wait(run_id, 10)
    assert conn.execute("SELECT status FROM scan_runs WHERE id=?", (run_id,)).fetchone()[0] == "done"
    assert scheduler.tick(at(22, 23)) == []                             # this night's run is the resumed one...
    assert scheduler.tick(at(23, 22, 0))[0].startswith("start:")        # ...and the next night starts a new run
