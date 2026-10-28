"""Build the daily Roster Report from the HotSchedules JSON API.

Mirrors what newReport.hs#roster/<date> renders: fetch the week's shifts plus the
employee/job/role/dayPart lookups, filter to the target day, and join into rows.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .client import HSClient
from .config import STORE_TZ, load_env_file


@dataclass
class RosterShift:
    employee: str
    day_part: str          # "AM" / "PM" / ...
    start_time: str        # "9:00a"
    end_time: str          # "5:00p"
    hours: float
    job: str
    location: str
    start_time_24: str     # "09:00" (for sorting)
    day_part_start: int    # minutes from midnight (for ordering day parts)


def today_in_store_tz(tz: str | None = None) -> date:
    load_env_file()
    return datetime.now(ZoneInfo(tz or os.environ.get("HS_STORE_TZ", STORE_TZ))).date()


def week_window(target: date, week_start: date) -> tuple[str, str]:
    """The Monday-start week window the app requests for a given date.

    week_start comes from bootstrap.currentWeekStartDate (the *current* week); we
    align to the Monday on/just before the target date so any date works.
    """
    # Monday of the target's week.
    monday = target - timedelta(days=target.weekday())
    # The app fetches start = Monday-1day .. end = Monday+8days (10:00 slack window);
    # mirror its generous window: start day before, end ~9 days later.
    start = monday - timedelta(days=1)
    end = monday + timedelta(days=8)
    return start.isoformat(), end.isoformat()


def _fmt_time(hhmm: str) -> str:
    """'09:00' -> '9:00a', '16:30' -> '4:30p', '00:30' -> '12:30a'."""
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    ampm = "a" if h < 12 else "p"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d}{ampm}"


def _add_minutes(hhmm: str, minutes: int) -> str:
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    total = (h * 60 + m + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def fetch_roster(client: HSClient, target: date | None = None) -> tuple[date, list[RosterShift]]:
    """Return (date, sorted roster rows) for the target date (default: today in store tz)."""
    bootstrap = client.get_json("spring/scheduling/bootstrap")
    tz = bootstrap.get("tz", STORE_TZ)
    if target is None:
        target = today_in_store_tz(tz)
    week_start = date.fromisoformat(bootstrap["currentWeekStartDate"])

    day_parts = {
        dp["id"]: {"name": dp["name"], "start": dp.get("startTimeHour", 0) * 60 + dp.get("startTimeMinute", 0)}
        for dp in bootstrap.get("dayParts", [])
    }

    # Lookups: employees, jobs, roles, locations.
    employees = _index(client.get_json("spring/client/employee/"),
                        key=lambda e: e.get("id"),
                        val=lambda e: e.get("displayFullName") or e.get("name")
                        or f'{e.get("firstname","")} {e.get("lastname","")}'.strip())
    jobs = _index(client.get_json("spring/client/jobs/"),
                  key=lambda j: j.get("id"), val=lambda j: j.get("jobName") or j.get("name"))
    try:
        roles = _index(client.get_json("spring/client/roles/"),
                       key=lambda r: r.get("id"), val=lambda r: r.get("roleName") or r.get("name"))
    except Exception:
        roles = {}
    try:
        locations = _index(client.get_json("spring/client/locations/"),
                           key=lambda l: l.get("id"), val=lambda l: l.get("name"))
    except Exception:
        locations = {}

    start, end = week_window(target, week_start)
    shifts = client.get_json(
        "spring/scheduling/shift/",
        params={"start": f"{start}T00:00", "end": f"{end}T00:00"},
    )

    target_iso = target.isoformat()
    store_client_id = bootstrap.get("id")
    rows: list[RosterShift] = []
    for s in shifts:
        if s.get("startDate") != target_iso or not s.get("scheduled", True):
            continue
        # The Roster Report shows only this store's own schedule; other clientIds
        # are shared/other-store employees (the "Schedules" filter).
        if store_client_id is not None and s.get("clientId") != store_client_id:
            continue
        start_t = s.get("startTime", "00:00")
        dur = s.get("duration", 0)
        dp = day_parts.get(s.get("dayPartId"), {"name": "", "start": 9999})
        job = jobs.get(s.get("jobId"), "")
        # The roster's "Job & Location" column shows the job; role/location refine it.
        loc = locations.get(s.get("locationId"), "") if s.get("locationId", -1) != -1 else ""
        rows.append(RosterShift(
            employee=employees.get(s.get("ownerId"), f'#{s.get("ownerId")}'),
            day_part=dp["name"],
            start_time=_fmt_time(start_t),
            end_time=_fmt_time(_add_minutes(start_t, dur)),
            hours=round(dur / 60, 2),
            job=job or roles.get(s.get("roleId"), ""),
            location=loc,
            start_time_24=start_t,
            day_part_start=dp["start"],
        ))

    rows.sort(key=lambda r: (r.day_part_start, r.start_time_24, r.employee.lower()))
    return target, rows


def _index(items, key, val):
    out = {}
    for it in items or []:
        k = key(it)
        if k is not None:
            out[k] = val(it)
    return out
