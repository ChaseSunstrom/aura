"""No tracked file holds a private value: `scripts/verify/private_values.py`.

The repository is public, and it was written on one person's machine in one person's house: real
addresses, a real account, real device addresses and a real place had found their way into tests,
fixtures and docs. This pins that none is left, and that the scan can still see each kind.

Every private-looking value below is put together from pieces, so this file is clean under the
scan it tests.

Not covered: what has no shape. A person's name, a street, a private host written without a
scheme and a coordinate are found only through the machine's own untracked list, which the second
test uses where it exists and skips where it does not. Pictures are not read.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _tool():
    spec = importlib.util.spec_from_file_location("private_values_under_test", ROOT / "scripts" / "verify" / "private_values.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                         # a dataclass looks its module up by name
    spec.loader.exec_module(mod)
    return mod


pv = _tool()


def _said(findings) -> str:
    rows = [f"{f.path}:{f.line}: {f.kind}: {f.masked()}" for f in findings[:25]]
    more = f"\n… and {len(findings) - 25} more" if len(findings) > 25 else ""
    return f"{len(findings)} private value(s) in tracked files:\n" + "\n".join(rows) + more


def test_the_tracked_files_hold_no_private_value():
    findings = pv.scan_repo(ROOT, pv.LocalList())
    assert not findings, _said(findings)


def test_this_machines_own_list_finds_nothing_either():
    local = pv.read_local(ROOT / pv.LOCAL_LIST)
    if not local.present:
        pytest.skip(f"no {pv.LOCAL_LIST} here: names, places and bare hosts are checked only on a machine that has one")
    findings = [f for f in pv.scan_repo(ROOT, local) if f.kind == "local"]
    assert not findings, _said(findings)


# (class, a line holding a private value of that shape, the same line with a made-up value)
PLANTED = [
    ("address", "host: 203.0." + "114.9", "host: 203.0.113.9"),
    ("address", "wan 81.2." + "69.142", "lan 192.168.50.1"),
    ("host", "see https://nas." + "family-name.net/share", "see https://nas.example.com/share"),
    ("host", "ws://desk." + "walrus-pike" + ".ts.net:8643/ws", "ws://desk.example.ts.net:8643/ws"),
    ("host", "http://" + "martas-laptop" + ".local/", "http://jarvis.local/"),
    ("host", "clone git@" + "git.family-name.net" + ":me/aura.git", "clone git@git.example.com:me/aura.git"),
    ("email", "mail first.last@" + "gmail.com today", "mail first.last@example.com today"),
    ("phone", "call +1 999 " + "321 7654", "call +1 555 010 0199"),
    ("phone", "call (999) " + "321-7654", "call (555) 321-7654"),
    ("mac", "f0:9f:c2:" + "4d:1e:07", "00:00:5e:00:53:07"),
    ("path", "/home/" + "marta/notes.txt", "/home/user/notes.txt"),
    ("path", "C:\\Users\\" + "Marta\\notes.txt", "C:\\Users\\user\\notes.txt"),
    ("key", "token ghp_" + "a1B2c3D4" * 5, "token ghp_REDACTED"),
    ("key", "-----BEGIN OPENSSH " + "PRIVATE KEY-----\\n" + "b3BlbnNzaC1rZXktdjEAAAAA" + "BG5vbmUAAAAEbm9uZQAAAAAB",
     "-----BEGIN OPENSSH " + "PRIVATE KEY-----"),
]


@pytest.mark.parametrize("kind,private,made_up", PLANTED, ids=[f"{k}-{i}" for i, (k, _p, _m) in enumerate(PLANTED)])
def test_a_planted_private_value_is_reported_and_its_made_up_twin_is_not(kind, private, made_up):
    found = pv.scan_text("some/file.md", "first line\n" + private + "\n")
    assert [(f.kind, f.line) for f in found] == [(kind, 2)], found
    assert pv.scan_text("some/file.md", made_up + "\n") == []


def test_a_key_header_is_a_finding_only_with_a_key_under_it():
    """A note that names the PEM format holds no key; the same header over a body does, on the
    same line (an escaped newline in a string) or on the next one."""
    header = "-----BEGIN " + "PRIVATE KEY-----"
    body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" + "BKcwggSjAgEAAoIBAQC7"
    assert pv.scan_text("a.kt", f'error("expects ({header}); convert an RSA key first")') == []
    assert pv.scan_text("a.md", f"| key format | {header} (PKCS#8) | see AACS/AAClient/ssl/headunit.key |") == []
    assert [f.kind for f in pv.scan_text("a.pem", f"{header}\n{body}\n")] == ["key"]
    assert [f.kind for f in pv.scan_text("a.py", f'KEY = "{header}\\n{body}"')] == ["key"]


def test_a_role_mailbox_and_a_language_label_are_nobodys_address():
    assert pv.scan_text("a.kt", "this@AppPaneHost.projection = null") == []
    assert pv.scan_text("a.py", '"Co-Authored-By: A Model <noreply@' + 'anthropic.com>"') == []
    assert [f.kind for f in pv.scan_text("a.md", "write to pat.doe@" + "anthropic.com")] == ["email"]


def test_a_planted_value_is_masked_unless_asked_for():
    (finding,) = pv.scan_text("a.md", "host: 203.0." + "114.9")
    assert finding.masked() == "20" + "*" * 9
    assert "114" not in finding.masked()
    # A failing assert prints its operands, so the finding's own repr is masked too.
    assert "114" not in repr(finding) and "114" not in repr([finding])


def test_a_link_under_the_projects_account_must_be_the_public_repository_or_its_site():
    owners, links = ("someone",), ("github.com/someone/aura", "someone.github.io/aura")
    public = frozenset({"github.com", "github.io"})

    def kinds(line: str) -> list[str]:
        return [f.kind for f in pv.scan_text("README.md", line, public=public, owners=owners, links=links)]

    assert kinds("https://github.com/someone/aura") == []
    assert kinds("https://github.com/someone/aura/releases/latest") == []
    assert kinds("git clone https://github.com/someone/aura.git") == []
    assert kinds("https://someone.github.io/aura/") == []
    assert kinds("https://github.com/someone/house-notes") == ["link"]          # another repository of theirs
    assert kinds("https://github.com/someone/aura-private") == ["link"]         # a longer name is another repository
    assert kinds("https://someone.github.io/other/") == ["link"]
    assert kinds("https://github.com/another/project") == []                    # somebody else's project is not ours to judge


def test_the_account_is_named_only_inside_the_public_links(tmp_path):
    # The account is a person's handle, so a machine's own list holds it. The one place it may
    # stand is inside a public link: the install line could not be written otherwise. Anywhere
    # else, and in any other link under the account, it is still a finding.
    listed = tmp_path / pv.LOCAL_LIST
    listed.write_text("word someone\n", encoding="utf-8")
    local = pv.read_local(listed)
    links = ("github.com/someone/aura", "someone.github.io/aura", "raw.githubusercontent.com/someone/aura")
    owners = pv.owners_of(links)
    assert owners == ("someone",)
    public = frozenset({"github.com", "github.io", "githubusercontent.com"})

    def kinds(line: str) -> list[str]:
        return sorted(f.kind for f in pv.scan_text("README.md", line, local, public=public, owners=owners, links=links))

    assert kinds("curl -fsSL https://raw.githubusercontent.com/someone/aura/main/install.sh | sh") == []
    assert kinds('AURA_REPO_DEFAULT="https://github.com/someone/aura"') == []
    assert kinds("git clone https://github.com/someone/aura.git") == []
    assert kinds("https://Someone.github.io/aura/") == []
    assert kinds("written by someone") == ["local"]
    assert kinds("https://github.com/someone/aura, by someone") == ["local"]    # the link beside it excuses nothing
    assert kinds("https://raw.githubusercontent.com/someone/notes/main/a.txt") == ["link", "local"]
    assert kinds("https://github.com/someone/aura-private") == ["link", "local"]


def test_the_scans_own_account_list_is_read_out_of_its_links():
    # A second place to type the account would be a place to type it outside a link.
    assert pv.PROJECT_OWNERS == pv.owners_of(pv.PROJECT_LINKS)
    assert pv.owners_of(("github.com/Someone/aura", "someone.github.io/aura")) == ("someone",)


def test_the_machines_list_finds_text_words_places_and_file_names(tmp_path):
    listed = tmp_path / pv.LOCAL_LIST
    listed.write_text("# comment\ntext family-name.net\nword Marta\nname Grace\ncoord 10.5000,20.5000,50\n", encoding="utf-8")
    local = pv.read_local(listed)
    assert local.present

    def hits(path: str, body: str) -> list[int]:
        return [f.line for f in pv.scan_text(path, body, local) if f.kind == "local"]

    assert hits("a.md", "pull from registry.Family-Name.net/app") == [1]         # no scheme, any case
    assert hits("a.md", "said Marta.\nsmarta is a word\n") == [1]               # a word, not a part of one
    assert hits("a.md", "with grace\nasked Grace\n") == [2]                    # a name that is also a word: this case only
    assert hits("a.json", '{"lat": 10.4321,\n "lon": 20.6789}') == [1]          # a place within the radius, said once
    assert hits("a.json", '{"lat": 10.4321,\n "lon": 80.1234}') == []           # one number alone is not a place
    assert hits("a.json", '{"lat": 48.8584,\n "lon": 2.2945}') == []
    assert [f.kind for f in pv.scan_text("photos/marta.md", "photos/marta.md", local)] == ["local"]


def test_a_missing_list_is_an_empty_one_and_local_mode_says_so(tmp_path, capsys):
    assert pv.read_local(tmp_path / pv.LOCAL_LIST) == pv.LocalList()
    (tmp_path / "a.md").write_text("nothing here\n", encoding="utf-8")
    assert pv.main(["--repo", str(tmp_path), "--local"]) == 2
    assert "there is none" in capsys.readouterr().out
    assert pv.main(["--repo", str(tmp_path)]) == 0


def test_a_copy_without_git_is_walked_and_build_folders_are_left_out(tmp_path, capsys):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("host: 203.0." + "114.9\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "b.md").write_text("host: 203.0." + "114.9\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\0\1\2 203.0." + b"114.9")
    assert pv.main(["--repo", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "docs/a.md:1: address: 20*" in out and "node_modules" not in out and "blob.bin" not in out
    assert "1 finding(s)" in out and "114" not in out


def test_the_machines_list_can_never_be_committed():
    try:
        done = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", pv.LOCAL_LIST], capture_output=True)
    except OSError:
        pytest.skip("no git here")
    if done.returncode == 128:
        pytest.skip("not a git checkout")
    assert done.returncode == 0, f"{pv.LOCAL_LIST} is not in .gitignore: the list of real values could be committed"


def test_the_public_hosts_list_is_sorted_lowercase_and_has_no_made_up_or_private_looking_entry():
    rows = [r for r in (ROOT / "scripts" / "verify" / "public_hosts.txt").read_text(encoding="utf-8").splitlines()
            if r and not r.startswith("#")]
    assert rows == sorted(set(rows)), "keep one lowercase domain per line, sorted, no duplicates"
    assert all(r == r.lower().strip() and "." in r and "/" not in r for r in rows)
    assert not [r for r in rows if r.endswith(pv.LAN_SUFFIXES + pv.MADE_UP_HOST_SUFFIXES + (".ts.net",))]


def test_the_public_hosts_list_names_only_hosts_the_tree_still_uses():
    """An allow-list that only grows passes more every month: a host whose last mention left the
    tree leaves the list with it. Not covered: a host kept alive by one stale mention."""
    listed = "scripts/verify/public_hosts.txt"
    rows = [r for r in (ROOT / listed).read_text(encoding="utf-8").splitlines() if r and not r.startswith("#")]
    seen = bytearray()
    for name in pv.tracked(ROOT):
        if name == listed or name.startswith("graphify-out/"):
            continue
        try:
            raw = (ROOT / name).read_bytes()
        except OSError:
            continue
        if b"\0" not in raw[:4096]:
            seen += raw.lower() + b"\n"
    unused = [r for r in rows if r.encode() not in seen]
    assert not unused, f"no tracked file names these any more; drop them from {listed}: {unused}"
