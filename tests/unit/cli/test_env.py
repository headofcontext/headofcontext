from pathlib import Path

from headofcontext.cli.env import load_dotenv, write_env


def test_load_dotenv_parses_and_does_not_override(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        '# comment\nHOC_A=1\nHOC_B="two words"\nexport HOC_C=3\nHOC_D=\n\n'
    )
    monkeypatch.setenv("HOC_A", "already")
    loaded = load_dotenv(tmp_path / ".env")
    assert loaded == {"HOC_B": "two words", "HOC_C": "3", "HOC_D": ""}
    import os

    assert os.environ["HOC_A"] == "already" and os.environ["HOC_B"] == "two words"


def test_write_env_replaces_and_appends(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("KEEP=1\nHOC_OPENFGA_STORE_ID=old\n")
    write_env(path, {"HOC_OPENFGA_STORE_ID": "new", "HOC_OPENFGA_MODEL_ID": "m1"})
    assert path.read_text() == "KEEP=1\nHOC_OPENFGA_STORE_ID=new\nHOC_OPENFGA_MODEL_ID=m1\n"


def test_write_env_creates_file(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    write_env(path, {"A": "b"})
    assert path.read_text() == "A=b\n"
