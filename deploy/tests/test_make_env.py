"""Tests for deploy/make_env.py (random secret generation for .env)."""
import re

import pytest

from make_env import main, make_env


def _write(path, text):
    # newline="" so the bytes written are exactly the bytes given (no CRLF translation on Windows).
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def test_replaces_every_change_me_value(tmp_path):
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    _write(example, "GSN_VERSION=dev\nA=change-me-a\nB=change-me-b\n")
    make_env(example, target, token=iter(["tok1", "tok2"]).__next__)
    assert target.read_text() == "GSN_VERSION=dev\nA=tok1\nB=tok2\n"


def test_keeps_comments_blank_lines_and_other_values_verbatim(tmp_path):
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    _write(
        example,
        "# a comment with change-me inside\n"
        "\n"
        "KEEP=value=with=equals\n"
        "SECRET=change-me-x\n"
        "ADDR=:80\n",
    )
    make_env(example, target, token=lambda: "tok")
    assert target.read_bytes() == (
        b"# a comment with change-me inside\n"
        b"\n"
        b"KEEP=value=with=equals\n"
        b"SECRET=tok\n"
        b"ADDR=:80\n"
    )


def test_writes_lf_even_when_example_has_crlf(tmp_path):
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    _write(example, "GSN_VERSION=dev\r\nA=change-me-a\r\n")
    make_env(example, target, token=lambda: "tok")
    assert b"\r" not in target.read_bytes()
    assert target.read_bytes() == b"GSN_VERSION=dev\nA=tok\n"


def test_refuses_to_overwrite_existing_env(tmp_path):
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    _write(example, "A=change-me-a\n")
    _write(target, "A=already-set\n")
    with pytest.raises(FileExistsError):
        make_env(example, target, token=lambda: "tok")
    assert target.read_text() == "A=already-set\n"


def test_generated_tokens_are_url_safe(tmp_path):
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    _write(example, "A=change-me-a\nB=change-me-b\n")
    make_env(example, target)  # default token generator
    values = [line.split("=", 1)[1] for line in target.read_text().splitlines()]
    assert len(values) == 2
    for value in values:
        assert re.fullmatch(r"[A-Za-z0-9_-]{32}", value)
    assert values[0] != values[1]


def test_cli_writes_target_and_never_prints_secrets(tmp_path, capsys):
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    _write(example, "A=change-me-a\n")
    assert main(["--example", str(example), "--target", str(target)]) == 0
    secret = target.read_text().strip().split("=", 1)[1]
    assert secret and secret not in capsys.readouterr().out
    # second run must refuse and keep the secret
    assert main(["--example", str(example), "--target", str(target)]) == 1
    assert target.read_text().strip().split("=", 1)[1] == secret


def test_bom_in_example_is_stripped(tmp_path):
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    example.write_bytes(b"\xef\xbb\xbfGSN_VERSION=dev\nA=change-me-a\n")
    make_env(example, target, token=lambda: "tok")
    assert target.read_bytes() == b"GSN_VERSION=dev\nA=tok\n"


def test_bom_before_a_placeholder_first_line_is_still_replaced(tmp_path):
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    example.write_bytes(b"\xef\xbb\xbfA=change-me-a\n")
    make_env(example, target, token=lambda: "tok")
    assert target.read_bytes() == b"A=tok\n"


def test_quoted_placeholders_are_replaced_and_written_unquoted(tmp_path):
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    _write(example, 'A="change-me-a"\nB=\'change-me-b\'\nC=" change-me-c "\nKEEP="plain value"\n')
    make_env(example, target, token=iter(["tok1", "tok2", "tok3"]).__next__)
    assert target.read_text() == 'A=tok1\nB=tok2\nC=tok3\nKEEP="plain value"\n'


def test_refuses_and_creates_no_file_if_a_placeholder_would_survive(tmp_path):
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    # "change-me" inside a URL is not at the start of the value, so it is not replaced
    _write(example, "A=change-me-a\nURL=postgres://user:change-me-b@db/app\n")
    with pytest.raises(ValueError, match="line"):
        make_env(example, target, token=lambda: "tok")
    assert not target.exists()


def test_refuses_for_placeholder_text_without_a_key(tmp_path):
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    _write(example, "A=ok\nchange-me\n")
    with pytest.raises(ValueError):
        make_env(example, target, token=lambda: "tok")
    assert not target.exists()


def test_cli_reports_surviving_placeholder_without_creating_file(tmp_path, capsys):
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    _write(example, "URL=x://change-me@host\n")
    assert main(["--example", str(example), "--target", str(target)]) == 1
    assert not target.exists()
    assert "placeholder" in capsys.readouterr().err
