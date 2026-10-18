import csv
import functools
import hashlib
import http.server
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from ingest.fetch_raw import main

SCRIPT = Path(__file__).resolve().parents[1] / "python" / "ingest" / "fetch_raw.py"
PAYLOAD = b"synthetic statute bytes " * 50
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
NAME = "raw/big_file.pdf"
FIELDS = ["file", "source_url", "sha256", "bytes", "committed"]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def server(tmp_path):
    """Serve tmp_path/www over http on a free local port; yields (base_url, www_dir)."""
    www = tmp_path / "www"
    www.mkdir()
    handler = functools.partial(QuietHandler, directory=str(www))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}", www
    httpd.shutdown()
    httpd.server_close()


def make_manifest(tmp_path, url, sha=PAYLOAD_SHA, size=len(PAYLOAD)):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    manifest = data / "manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow({"file": NAME, "source_url": url, "sha256": sha,
                         "bytes": size, "committed": "no (large; git-ignored)"})
        writer.writerow({"file": "raw/small.pdf", "source_url": "http://invalid.invalid/x",
                         "sha256": "0" * 64, "bytes": 1, "committed": "yes"})
    return manifest


def run(manifest, *extra):
    return main(["--manifest", str(manifest), *extra])


def test_download_success(tmp_path, server, capsys):
    base, www = server
    (www / "f.pdf").write_bytes(PAYLOAD)
    manifest = make_manifest(tmp_path, f"{base}/f.pdf")
    assert run(manifest) == 0
    target = manifest.parent / NAME
    assert target.read_bytes() == PAYLOAD
    assert not target.with_name(target.name + ".part").exists()
    assert "downloaded" in capsys.readouterr().out


def test_hash_mismatch_removes_part_and_fails(tmp_path, server, capsys):
    base, www = server
    (www / "f.pdf").write_bytes(PAYLOAD[:-1] + b"X")
    manifest = make_manifest(tmp_path, f"{base}/f.pdf")
    assert run(manifest) == 1
    target = manifest.parent / NAME
    assert not target.exists()
    assert not target.with_name(target.name + ".part").exists()
    out = capsys.readouterr().out
    assert "expected" in out and PAYLOAD_SHA in out


def test_size_cap_stops_oversized_download(tmp_path, server, capsys):
    base, www = server
    (www / "f.pdf").write_bytes(PAYLOAD * 3)  # 3x the manifest size, cap is 1.5x
    manifest = make_manifest(tmp_path, f"{base}/f.pdf")
    assert run(manifest) == 1
    target = manifest.parent / NAME
    assert not target.exists()
    assert not target.with_name(target.name + ".part").exists()
    assert "size cap" in capsys.readouterr().out


def test_existing_correct_file_is_present(tmp_path, capsys):
    manifest = make_manifest(tmp_path, "http://127.0.0.1:9/never-called")
    target = manifest.parent / NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(PAYLOAD)
    assert run(manifest) == 0
    assert "present" in capsys.readouterr().out


def test_existing_wrong_file_never_overwritten(tmp_path, server, capsys):
    base, www = server
    (www / "f.pdf").write_bytes(PAYLOAD)
    manifest = make_manifest(tmp_path, f"{base}/f.pdf")
    target = manifest.parent / NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(b"locally edited")
    assert run(manifest) == 1
    assert target.read_bytes() == b"locally edited"
    assert "not overwritten" in capsys.readouterr().out


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_unreachable_url_fails(tmp_path, capsys):
    manifest = make_manifest(tmp_path, f"http://127.0.0.1:{free_port()}/f.pdf")
    assert run(manifest) == 1
    assert not (manifest.parent / NAME).exists()
    assert "FAIL" in capsys.readouterr().out


def test_mirror_used_when_source_fails(tmp_path, server, capsys):
    base, www = server
    (www / "mirror.pdf").write_bytes(PAYLOAD)
    manifest = make_manifest(tmp_path, f"http://127.0.0.1:{free_port()}/f.pdf")
    assert run(manifest, "--mirror", f"{base}/mirror.pdf") == 0
    assert (manifest.parent / NAME).read_bytes() == PAYLOAD
    assert "mirror.pdf" in capsys.readouterr().out


def test_mirror_not_tried_when_source_works(tmp_path, server):
    base, www = server
    (www / "f.pdf").write_bytes(PAYLOAD)
    manifest = make_manifest(tmp_path, f"{base}/f.pdf")
    assert run(manifest, "--mirror", f"http://127.0.0.1:{free_port()}/mirror.pdf") == 0


def test_dest_downloads_into_other_folder(tmp_path, server):
    base, www = server
    (www / "f.pdf").write_bytes(PAYLOAD)
    manifest = make_manifest(tmp_path, f"{base}/f.pdf")
    dest = tmp_path / "elsewhere"
    assert run(manifest, "--dest", str(dest)) == 0
    assert (dest / NAME).read_bytes() == PAYLOAD
    assert not (manifest.parent / NAME).exists()


def test_script_exit_code_via_subprocess(tmp_path):
    manifest = make_manifest(tmp_path, f"http://127.0.0.1:{free_port()}/f.pdf")
    result = subprocess.run([sys.executable, str(SCRIPT), "--manifest", str(manifest)],
                            capture_output=True, text=True)
    assert result.returncode == 1
