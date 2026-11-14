"""The container lock stays equal to the direct pins (deployment design Section 1.2; D-194, closes D-183).

Free; no Docker. `requirements.lock.txt` is the exact set the image installs with `--no-deps`, so every exact
pin in `requirements.txt` must carry the same version there, or the image would run a different version from
the one the suite tested."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._\-\[\]]*)==([^\s#;]+)\s*(?:#.*)?$")


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_pins(path: Path) -> dict[str, str]:
    """Exact pins in a requirements file; comments and blank lines are skipped. A line that is neither fails."""
    pins: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = PIN.match(line)
        assert m, f"{path.name}:{number}: not an exact pin: {line!r}"
        name = normalise(m.group(1).split("[")[0])
        assert name not in pins, f"{path.name}:{number}: {name} listed twice"
        pins[name] = m.group(2)
    return pins


def test_every_exact_pin_in_requirements_is_in_the_lock_with_the_same_version():
    direct = parse_pins(ROOT / "requirements.txt")
    lock = parse_pins(ROOT / "requirements.lock.txt")
    assert direct, "requirements.txt has no exact pins"
    missing = sorted(set(direct) - set(lock))
    assert not missing, f"in requirements.txt but not in the lock: {missing}"
    different = {n: (direct[n], lock[n]) for n in direct if direct[n] != lock[n]}
    assert not different, f"version differs (requirements.txt, lock): {different}"


def test_the_two_sign_in_pins_are_exact():
    direct = parse_pins(ROOT / "requirements.txt")
    assert direct["authlib"] == "1.9.1" and direct["httpx"] == "0.28.1"


def test_the_lock_is_linux_only_and_complete_for_sign_in():
    lock = parse_pins(ROOT / "requirements.lock.txt")
    assert "colorama" not in lock  # Windows only
    assert lock["hf-xet"] == "1.8.0"  # the Linux wheel
    for name in ("authlib", "httpx", "httpcore", "joserfc", "cryptography"):
        assert name in lock, name


def test_the_dockerfile_installs_the_lock_with_no_deps_then_checks():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "pip install --no-cache-dir --no-deps -r requirements.lock.txt" in dockerfile
    assert "pip check" in dockerfile
