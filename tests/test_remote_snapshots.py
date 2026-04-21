import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_repo_audit import install_alpaca_stubs


class RemoteSnapshotTests(unittest.TestCase):
    def test_remote_snapshot_writer_tails_recent_decisions_and_trades(self):
        remote_snapshots = importlib.import_module("remote_snapshots")
        importlib.reload(remote_snapshots)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bot_log = root / "bot_10k.log"
            decision_log = root / "bot_decisions_10k.jsonl"
            trade_log = root / "trades_10k.tsv"
            docs_data = root / "docs" / "data"

            bot_log.write_text(
                "\n".join(
                    [
                        "2026-04-20 13:30:01 [copytrade] startup",
                        "2026-04-20 13:30:02 [copytrade] first line",
                        "2026-04-20 13:30:03 [copytrade] second line",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            decision_log.write_text(
                "\n".join(
                    [
                        json.dumps({"timestamp_utc": "2026-04-20T13:30:01Z", "event_type": "order_submitted", "symbol": "AAPL"}),
                        json.dumps({"timestamp_utc": "2026-04-20T13:30:02Z", "event_type": "order_submitted", "symbol": "TSLA"}),
                        json.dumps({"timestamp_utc": "2026-04-20T13:30:03Z", "event_type": "order_submitted", "symbol": "AMZN"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            trade_log.write_text(
                "symbol\torder_id\tside\tnotional\tstatus\n"
                "AAPL\t1\tBUY\t100.00\tpending\n"
                "TSLA\t2\tBUY\t200.00\tpending\n"
                "AMZN\t3\tBUY\t300.00\tfilled\n",
                encoding="utf-8",
            )

            with patch.object(remote_snapshots, "DOCS_DATA_DIR", docs_data), patch.object(
                remote_snapshots, "PUBLIC_VERSION_PATH", docs_data / "version.json"
            ), patch.object(
                remote_snapshots, "RECENT_BOT_LOG_PATH", docs_data / "recent_bot.log"
            ), patch.object(
                remote_snapshots, "RECENT_DECISIONS_PATH", docs_data / "recent_decisions.json"
            ), patch.object(
                remote_snapshots, "RECENT_TRADES_PATH", docs_data / "recent_trades.tsv"
            ), patch.object(
                remote_snapshots, "RECENT_PORTFOLIO_PATH", docs_data / "recent_portfolio.json"
            ):
                changed = remote_snapshots.write_snapshot_files(
                    bot_log_path=bot_log,
                    decision_log_path=decision_log,
                    trade_log_path=trade_log,
                    portfolio_snapshot={
                        "as_of": "2026-04-21T14:00:00Z",
                        "equity": 10000.0,
                        "cash": 500.0,
                        "allocated": 9500.0,
                        "positions": [{"symbol": "AMZN", "current_value": 1500.0, "target_weight": 0.15}],
                    },
                    bot_log_limit=2,
                    decision_limit=2,
                    trade_limit=2,
                )

            self.assertEqual(len(changed), 5)
            version_payload = json.loads((docs_data / "version.json").read_text(encoding="utf-8"))
            self.assertEqual(version_payload["version"], "51.4")
            self.assertEqual(version_payload["display"], "v51.4")
            bot_log_snapshot = (docs_data / "recent_bot.log").read_text(encoding="utf-8")
            self.assertIn("first line", bot_log_snapshot)
            self.assertIn("second line", bot_log_snapshot)
            self.assertNotIn("startup", bot_log_snapshot)
            decision_rows = json.loads((docs_data / "recent_decisions.json").read_text(encoding="utf-8"))
            self.assertEqual([row["symbol"] for row in decision_rows], ["TSLA", "AMZN"])
            trade_snapshot = (docs_data / "recent_trades.tsv").read_text(encoding="utf-8")
            self.assertIn("TSLA\t2\tBUY\t200.00\tpending", trade_snapshot)
            self.assertIn("AMZN\t3\tBUY\t300.00\tfilled", trade_snapshot)
            self.assertNotIn("AAPL\t1\tBUY\t100.00\tpending", trade_snapshot)
            portfolio_snapshot = json.loads((docs_data / "recent_portfolio.json").read_text(encoding="utf-8"))
            self.assertEqual(portfolio_snapshot["cash"], 500.0)
            self.assertEqual(portfolio_snapshot["positions"][0]["symbol"], "AMZN")

    def test_khanna_live_publishes_remote_snapshots_on_startup(self):
        with install_alpaca_stubs():
            live = importlib.import_module("khanna_daily.live")
            importlib.reload(live)

            manager = live.CopyTradeLiveManager()
            with patch.object(live.signal_updater, "refresh_politician_signals", return_value={"added": 0, "pages_scanned": 1, "total_rows": 2}), patch.object(
                manager,
                "load_state",
            ), patch.object(manager.order_sync, "sync_trade_log_until_settled"), patch.object(manager, "evaluate"), patch.object(
                manager,
                "save_state",
            ), patch.object(
                manager.snapshot_publisher,
                "publish_if_due",
            ) as publish_mock:
                manager.startup_sync()

            publish_mock.assert_called_once_with(force=True)

    def test_remote_snapshot_publisher_syncs_before_writing(self):
        remote_snapshots = importlib.import_module("remote_snapshots")
        importlib.reload(remote_snapshots)

        publisher = remote_snapshots.RemoteSnapshotPublisher(
            bot_log_path=Path("bot_10k.log"),
            decision_log_path=Path("bot_decisions_10k.jsonl"),
            trade_log_path=Path("trades_10k.tsv"),
            enabled=True,
        )

        with patch.object(publisher, "_sync_branch") as sync_mock, patch.object(
            remote_snapshots,
            "write_snapshot_files",
            return_value=[],
        ) as write_mock:
            publisher.publish_once()

        sync_mock.assert_called_once_with()
        write_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
