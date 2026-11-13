"""CLI: ispravni izlazni kodovi i fajlovi. Testira se i kroz pravi proces (python -m fabagent)."""

import json
import os
import subprocess
import sys

import pytest

from fabagent.cli import main
from fabagent.core import config as C
from fabagent.core.errors import (InvalidGeometry, InvalidParam, MissingParam, OutOfRange,
                                  UnknownTemplate, ValidationFailed)


def _params(example: dict) -> list[str]:
    return [f"{k}={v}" for k, v in example.items()]


def test_list_and_info(capsys):
    assert main(["laser", "list"]) == 0
    assert "privesak" in capsys.readouterr().out
    assert main(["print", "list"]) == 0
    assert "nosac_l" in capsys.readouterr().out
    assert main(["laser", "info", "privesak"]) == 0
    out = capsys.readouterr().out
    assert "OBAVEZNI" in out and "rupa_promjer" in out
    assert main(["--json", "print", "info", "kutija"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["params"]["tolerancija"]["required"] is True


def test_laser_build_ok(tmp_path, capsys):
    out = tmp_path / "p.svg"
    code = main(["laser", "build", "privesak", "--params",
                 *_params(C.LASER_TEMPLATES["privesak"].example), "--out", str(out), "--preview"])
    assert code == 0
    assert out.exists() and (tmp_path / "p.json").exists() and (tmp_path / "p.png").exists()
    assert main(["validate", str(out)]) == 0


def test_print_build_ok(tmp_path):
    out = tmp_path / "n.stl"
    code = main(["print", "build", "nosac_l", "--params",
                 *_params(C.PRINT_TEMPLATES["nosac_l"].example), "--out", str(out)])
    assert code == 0
    for ext in ("stl", "3mf", "json"):
        assert (tmp_path / f"n.{ext}").exists()
    assert main(["validate", str(out)]) == 0
    assert main(["validate", str(tmp_path / "n.3mf")]) == 0


@pytest.mark.parametrize("argv,exc", [
    (["laser", "build", "privesak", "--params", "oblik=krug", "--out", "x.svg"], MissingParam),
    (["laser", "build", "nepostoji", "--params", "a=1", "--out", "x.svg"], UnknownTemplate),
])
def test_error_exit_codes(argv, exc, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(argv) == exc.exit_code


def test_out_of_range_and_invalid_codes(tmp_path):
    ex = dict(C.LASER_TEMPLATES["privesak"].example, sirina=500)
    assert main(["laser", "build", "privesak", "--params", *_params(ex),
                 "--out", str(tmp_path / "a.svg")]) == OutOfRange.exit_code
    ex = dict(C.LASER_TEMPLATES["privesak"].example, sirina="40,5")
    assert main(["laser", "build", "privesak", "--params", *_params(ex),
                 "--out", str(tmp_path / "a.svg")]) == InvalidParam.exit_code
    ex = dict(C.LASER_TEMPLATES["privesak"].example, tekst="DOBOJDOBOJDOBOJ")
    assert main(["laser", "build", "privesak", "--params", *_params(ex),
                 "--out", str(tmp_path / "a.svg")]) == InvalidGeometry.exit_code
    assert not (tmp_path / "a.svg").exists()        # nista se ne upisuje kod greske


def test_use_defaults_flag(tmp_path):
    ex = {k: v for k, v in C.PRINT_TEMPLATES["poklopac"].example.items()
          if k not in ("ispuna_posto", "visina_sloja")}
    argv = ["print", "build", "poklopac", "--params", *_params(ex), "--out", str(tmp_path / "c.stl")]
    assert main(argv) == MissingParam.exit_code
    assert main(argv + ["--use-defaults"]) == 0
    meta = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert meta["params_defaulted"] == ["ispuna_posto", "visina_sloja"]


def test_validate_rejects_bad_files(tmp_path):
    bad_svg = tmp_path / "bad.svg"
    bad_svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="10px" height="10px"/>')
    assert main(["validate", str(bad_svg)]) == ValidationFailed.exit_code
    bad_stl = tmp_path / "bad.stl"
    bad_stl.write_bytes(b"\x01" * 100)
    assert main(["validate", str(bad_stl)]) == ValidationFailed.exit_code
    assert main(["validate", str(tmp_path / "nema.svg")]) != 0
    other = tmp_path / "x.dxf"
    other.write_text("0")
    assert main(["validate", str(other)]) == InvalidParam.exit_code


def test_subprocess_exit_codes(tmp_path):
    env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    ok = subprocess.run([sys.executable, "-m", "fabagent", "laser", "list"], capture_output=True, env=env)
    assert ok.returncode == 0
    bad = subprocess.run([sys.executable, "-m", "fabagent", "print", "build", "kutija",
                          "--params", "duzina=80", "--out", str(tmp_path / "k.stl")],
                         capture_output=True, env=env)
    assert bad.returncode == MissingParam.exit_code
    assert b"MissingParam" in bad.stderr
    usage = subprocess.run([sys.executable, "-m", "fabagent", "laser"], capture_output=True, env=env)
    assert usage.returncode == 2
