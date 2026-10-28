from __future__ import annotations

import html
import os
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from hs_roster.cache import acquire_roster, prune_cache, roster_path
from hs_roster.web import WebConfig, create_app, refresh_due, refresh_loop
from hs_roster.config import get_delivery_config

PDF = b"%PDF-1.4\nsynthetic test fixture\n%%EOF"
TODAY = date(2026, 10, 28)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = WebConfig(
            token="test-secret-" + "a" * 32,
            public_url="https://example.com/roster",
            cache_dir=self.directory,
        )
        self.clock = patch.object(WebConfig, "today", return_value=TODAY)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.client = create_app(self.config).test_client()
        self.root = f"/roster/{TODAY.isoformat()}/{self.config.token}/"
        self.archive = f"/roster/all/{self.config.token}/"
        self.today_url = f"/roster/today/{self.config.token}/"
        self.legacy = f"/roster/{self.config.token}/"

    def daily_path(self, value):
        return f"/roster/{value}/{self.config.token}/"

    def save(self, target=TODAY):
        path = roster_path(self.directory, target)
        path.write_bytes(PDF)
        return path

    def test_page_and_pdf_require_secret(self):
        self.save()
        for path in ["/roster/", "/roster/all/wrong/", "/roster/today/wrong/", "/roster/2026-10-28/wrong/",
                     "/roster/2026-10-28/wrong/pdf", "/roster/wrong/",
                     "/roster/wrong/rosters/2026-10-28.pdf"]:
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_ready_page_passes_date_specific_pdf_to_printpdf(self):
        self.save()
        with patch("hs_roster.web.acquire_roster") as acquire:
            response = self.client.get(self.root)
            self.assertEqual(response.status_code, 200)
            body = response.get_data(as_text=True)
            link = html.unescape(body.split('href="shortcuts://', 1)[1].split('"', 1)[0])
            query = parse_qs(urlsplit("shortcuts://" + link).query)
            self.assertEqual(query["name"], ["PrintPDF"])
            self.assertEqual(query["input"], ["text"])
            self.assertEqual(query["text"], [f"https://example.com{self.root}pdf"])
            acquire.assert_not_called()

    def test_missing_pdf_does_not_generate_on_request(self):
        with patch("hs_roster.web.acquire_roster") as acquire:
            response = self.client.get(self.root)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers["Retry-After"], "60")
            self.assertNotIn("shortcuts://", response.get_data(as_text=True))
            acquire.assert_not_called()

    def test_download_and_head(self):
        self.save()
        path = self.root + "pdf"
        with self.client.get(path) as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, PDF)
            self.assertEqual(response.mimetype, "application/pdf")
            self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        with self.client.head(path) as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b"")

    def test_retention_boundaries_future_and_invalid_dates(self):
        self.save(TODAY - timedelta(days=29))
        self.save(TODAY - timedelta(days=30))
        self.save(TODAY + timedelta(days=1)).write_bytes(PDF + b"\ntomorrow")
        with self.client.get(self.daily_path("2026-09-29") + "pdf") as response:
            self.assertEqual(response.status_code, 200)
        for day in ["2026-09-28", "2026-10-29", "20261028", "not-a-date"]:
            self.assertEqual(self.client.get(self.daily_path(day) + "pdf").status_code, 404)
            self.assertEqual(self.client.get(self.daily_path(day)).status_code, 404)

    def test_archive_and_css_prefix(self):
        self.save()
        yesterday = TODAY - timedelta(days=1)
        self.save(yesterday)
        body = self.client.get(self.archive).get_data(as_text=True)
        self.assertIn("All saved rosters", body)
        self.assertIn(self.daily_path("2026-10-27"), body)
        self.assertIn(self.root, body)
        self.assertIn(self.today_url, body)
        self.assertNotIn("shortcuts://", body)
        daily = self.client.get(self.daily_path("2026-10-27")).get_data(as_text=True)
        self.assertIn("Saved roster", daily)
        self.assertIn(self.daily_path("2026-10-27") + "pdf", daily)
        self.assertNotIn("<select", daily)
        with self.client.get("/roster/static/print.css") as response:
            self.assertEqual(response.status_code, 200)

    def test_corrupt_cache_is_not_served(self):
        self.save().write_bytes(b"error page")
        self.assertEqual(self.client.get(self.root).status_code, 503)
        self.assertEqual(self.client.get(self.root + "pdf").status_code, 404)

    def test_empty_archive_is_available_without_fetching(self):
        with patch("hs_roster.web.acquire_roster") as acquire:
            response = self.client.get(self.archive)
            self.assertEqual(response.status_code, 200)
            self.assertIn("Your archive is empty", response.get_data(as_text=True))
            acquire.assert_not_called()

    def test_daily_link_stays_on_its_date_after_midnight_and_ignores_query_date(self):
        self.save()
        self.save(TODAY + timedelta(days=1)).write_bytes(PDF + b"\ntomorrow")
        with patch.object(WebConfig, "today", return_value=TODAY + timedelta(days=1)):
            body = self.client.get(self.root + "?date=2026-10-29").get_data(as_text=True)
            self.assertIn("Wednesday, October 28, 2026", body)
            self.assertIn(self.root + "pdf", body)
            self.assertNotIn("2026-10-29", body)
            self.assertNotIn("<select", body)
            with self.client.get(self.root + "pdf?date=2026-10-29") as response:
                self.assertEqual(response.data, PDF)

    def test_today_page_keeps_bookmark_url_and_rolls_over_with_store_date(self):
        self.save()
        self.save(TODAY + timedelta(days=1)).write_bytes(PDF + b"\ntomorrow")
        for day, label in [(TODAY, "Wednesday, October 28, 2026"),
                           (TODAY + timedelta(days=1), "Thursday, October 29, 2026")]:
            with self.subTest(day=day), patch.object(WebConfig, "today", return_value=day):
                response = self.client.get(self.today_url + "?date=2026-10-27")
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("Location", response.headers)
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")
                body = response.get_data(as_text=True)
                self.assertIn(label, body)
                self.assertIn("Bookmark this page", body)
                self.assertIn("America/Chicago", body)
                link = html.unescape(body.split('href="shortcuts://', 1)[1].split('"', 1)[0])
                pdf_url = parse_qs(urlsplit("shortcuts://" + link).query)["text"][0]
                self.assertEqual(pdf_url, "https://example.com" + self.daily_path(day.isoformat()) + "pdf")

    def test_today_missing_pdf_never_falls_back_to_yesterday_or_fetches(self):
        self.save(TODAY - timedelta(days=1))
        with patch("hs_roster.web.acquire_roster") as acquire:
            response = self.client.get(self.today_url)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers["Retry-After"], "60")
            body = response.get_data(as_text=True)
            self.assertIn("Preparing today’s roster", body)
            self.assertNotIn("shortcuts://", body)
            self.assertNotIn("2026-10-27", body)
            acquire.assert_not_called()

    def test_legacy_links_redirect_to_specific_endpoints(self):
        self.save()
        paths = {
            self.legacy: self.archive,
            self.legacy + "?date=2026-10-28": self.root,
            self.legacy + "rosters/2026-10-28.pdf": self.root + "pdf",
        }
        for old, new in paths.items():
            with self.subTest(old=old):
                response = self.client.get(old)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], new)
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertEqual(self.client.get(self.legacy + "?date=not-a-date").status_code, 404)

    def test_prune_only_expired_roster_pdfs(self):
        old = self.save(TODAY - timedelta(days=30))
        boundary = self.save(TODAY - timedelta(days=29))
        other = self.directory / "notes.pdf"
        other.write_bytes(PDF)
        prune_cache(self.directory, TODAY)
        self.assertFalse(old.exists())
        self.assertTrue(boundary.exists())
        self.assertTrue(other.exists())

    def test_failed_render_preserves_previous_pdf_and_cleans_temporary(self):
        path = self.save()
        with patch("hs_roster.cache.HSClient"), patch("hs_roster.cache.fetch_roster", return_value=(None, [])), patch("hs_roster.cache.render_roster_pdf", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                acquire_roster(self.directory, TODAY)
        self.assertEqual(path.read_bytes(), PDF)
        self.assertEqual(list(self.directory.glob(".roster-*")), [])

    def test_successful_render_replaces_previous_pdf(self):
        path = self.save()
        replacement = PDF + b"\nnew"
        def render(client, target, destination):
            self.assertEqual(path.read_bytes(), PDF)
            destination.write_bytes(replacement)
        with patch("hs_roster.cache.HSClient"), patch("hs_roster.cache.fetch_roster", return_value=(None, [object()])), patch("hs_roster.cache.render_roster_pdf", side_effect=render):
            result, count = acquire_roster(self.directory, TODAY)
        self.assertEqual(result.read_bytes(), replacement)
        self.assertEqual(count, 1)

    def test_refresh_schedule_and_readiness(self):
        path = self.save()
        zone = ZoneInfo("America/Chicago")
        modified = datetime(2026, 10, 28, 7, 40, tzinfo=zone)
        os.utime(path, (modified.timestamp(), modified.timestamp()))
        self.assertFalse(refresh_due(self.config, datetime(2026, 10, 28, 7, 44, tzinfo=zone)))
        self.assertTrue(refresh_due(self.config, datetime(2026, 10, 28, 7, 45, tzinfo=zone)))
        modified = datetime(2026, 10, 28, 7, 46, tzinfo=zone)
        os.utime(path, (modified.timestamp(), modified.timestamp()))
        self.assertFalse(refresh_due(self.config, datetime(2026, 10, 28, 8, 0, tzinfo=zone)))
        self.assertFalse(refresh_due(self.config, datetime(2026, 10, 28, 8, 46, tzinfo=zone)))
        self.assertFalse(refresh_due(self.config, datetime(2026, 10, 29, 0, 0, tzinfo=zone)))
        self.assertTrue(refresh_due(self.config, datetime(2026, 10, 29, 7, 45, tzinfo=zone)))

    def test_worker_failure_retries_without_destroying_cache(self):
        self.save()
        stop = threading.Event()
        def stop_after_wait(seconds):
            stop.set()
            return True
        with patch("hs_roster.web.refresh_due", return_value=True), patch("hs_roster.web.acquire_roster", side_effect=RuntimeError("offline")), patch.object(stop, "wait", side_effect=stop_after_wait), self.assertLogs("hs_roster.web", level="ERROR"):
            refresh_loop(self.config, stop)
        self.assertEqual(roster_path(self.directory, TODAY).read_bytes(), PDF)

    def test_web_configuration_accepts_path_prefix_and_rejects_bad_tokens(self):
        env = {"ROSTER_WEB_TOKEN": "a" * 40, "ROSTER_WEB_PUBLIC_URL": "https://example.com/roster"}
        with patch.dict(os.environ, env, clear=True), patch("hs_roster.web.load_env_file"), patch("hs_roster.config.load_env_file"):
            cfg = WebConfig.from_env()
            self.assertEqual(cfg.public_url, env["ROSTER_WEB_PUBLIC_URL"])
        with patch.dict(os.environ, {"ROSTER_WEB_TOKEN": "short"}, clear=True), patch("hs_roster.web.load_env_file"):
            with self.assertRaises(ValueError):
                WebConfig.from_env()


    def test_daily_delivery_reuses_cache_and_adds_print_link(self):
        import hs_roster.main as job
        self.save()
        env = {"DISCORD_BOT_TOKEN": "fake-bot", "DISCORD_RECIPIENT_ID": "fake-recipient",
               "ROSTER_WEB_PUBLIC_URL": self.config.public_url, "ROSTER_WEB_TOKEN": self.config.token}
        with patch.dict(os.environ, env, clear=True), patch("hs_roster.config.load_env_file"):
            cfg = get_delivery_config()
        with patch.object(job, "get_delivery_config", return_value=cfg), patch.object(job, "get_cache_dir", return_value=self.directory), patch.object(job, "today_in_store_tz", return_value=TODAY), patch.object(job, "acquire_roster") as acquire, patch.object(job, "publish_pdf") as publish, patch.object(job, "send_roster_dm") as send:
            self.assertEqual(job.run(TODAY), 0)
            acquire.assert_not_called()
            args = send.call_args.kwargs
            self.assertEqual(args["pdf_path"], roster_path(self.directory, TODAY))
            publish.assert_not_called()
            print_url = "https://example.com" + self.root
            self.assertEqual(args["embed"]["url"], print_url)
            self.assertEqual(args["embed"]["fields"], [{"name": "Print", "value": f"[Print roster]({print_url})"}])
            body = self.client.get(urlsplit(print_url).path).get_data(as_text=True)
            link = html.unescape(body.split('href="shortcuts://', 1)[1].split('"', 1)[0])
            pdf_url = parse_qs(urlsplit("shortcuts://" + link).query)["text"][0]
            self.assertEqual(pdf_url, print_url + "pdf")
            with self.client.get(urlsplit(pdf_url).path) as response:
                self.assertEqual(response.data, args["pdf_path"].read_bytes())

    def test_delivery_without_print_server_keeps_legacy_upload_link(self):
        import hs_roster.main as job
        from types import SimpleNamespace
        self.save()
        env = {"DISCORD_BOT_TOKEN": "fake-bot", "DISCORD_RECIPIENT_ID": "fake-recipient"}
        with patch.dict(os.environ, env, clear=True), patch("hs_roster.config.load_env_file"):
            cfg = get_delivery_config()
        uploaded = SimpleNamespace(public_url="https://files.example.test/roster.pdf", expires_at="2026-10-29")
        with patch.object(job, "get_delivery_config", return_value=cfg), patch.object(job, "get_cache_dir", return_value=self.directory), patch.object(job, "today_in_store_tz", return_value=TODAY), patch.object(job, "publish_pdf", return_value=uploaded) as publish, patch.object(job, "send_roster_dm") as send:
            self.assertEqual(job.run(TODAY), 0)
            publish.assert_called_once()
            self.assertNotIn("url", send.call_args.kwargs["embed"])
            self.assertIn(uploaded.public_url, send.call_args.kwargs["embed"]["fields"][0]["value"])

    def test_worker_fetches_on_startup_even_when_cache_is_fresh(self):
        self.save()
        stop = threading.Event()
        def stop_after_wait(seconds):
            stop.set()
            return True
        with patch("hs_roster.web.refresh_due", return_value=False), patch("hs_roster.web.acquire_roster") as acquire, patch.object(stop, "wait", side_effect=stop_after_wait):
            refresh_loop(self.config, stop)
            acquire.assert_called_once()


if __name__ == "__main__":
    unittest.main()
