"""命令列：precheck / doctor / backup / restore / selftest（不開視窗、不碰正式資料根）。"""
from __future__ import annotations

import zipfile

from strategist.hub import __main__ as cli
from strategist.hub.tests.conftest import HTML, web_manifest, write_ext


def test_precheck_exit_codes(tmp_path, capsys):
    good = write_ext(tmp_path / "good", web_manifest(), {"view/index.html": HTML})
    bad = write_ext(tmp_path / "bad", web_manifest(), {"view/index.html": HTML, "view/a.js": "xhub.fs.saveText({})"})
    assert cli.main(["precheck", str(good)]) == 0
    assert cli.main(["precheck", str(bad)]) == 1
    assert "fs" in capsys.readouterr().out


def test_doctor_without_running_hub(capsys):
    assert cli.main(["doctor"]) == 0
    assert "沒有在執行" in capsys.readouterr().out


def test_open_without_running_hub_fails_cleanly(capsys):
    assert cli.main(["open"]) == 1
    assert "沒有在執行" in capsys.readouterr().err


def test_backup_then_restore_offline(tmp_path, capsys):
    assert cli.main(["backup"]) == 0
    archive = capsys.readouterr().out.strip().splitlines()[-1]
    assert zipfile.is_zipfile(archive)
    assert cli.main(["restore", archive]) == 0
    assert "已還原" in capsys.readouterr().out


def test_selftest_passes_end_to_end():
    """MVP 驗收自測本身也是測試的一部分（用 repo 內的 strategist/ 當 Strategist 根）。"""
    assert cli.main(["selftest"]) == 0
