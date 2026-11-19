"""Identify an exact release at startup without trusting an unverified label."""
import importlib.metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_REQUIREMENTS = 'requirements/requirements-server.txt'


def check_dependencies(mode='full', root=None):
    """Check all exact server pins; voice-only intentionally needs no solver."""
    if mode == 'voice-only':
        return dict(checked=False, reason='voice-only', packages={})
    if mode != 'full':
        raise ValueError('Unknown runtime mode.')
    packages = {}
    for line in (Path(root or ROOT)/SERVER_REQUIREMENTS).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, expected = line.split('==')
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        packages[name] = dict(expected=expected, actual=actual, matches=actual == expected)
    mismatches = [name for name, value in packages.items() if not value['matches']]
    if mismatches:
        raise ValueError('Pinned server dependency mismatch: ' + ', '.join(mismatches))
    return dict(checked=True, packages=packages)


def release_info(root=None):
    root = Path(root or ROOT).resolve()
    release = root.parent
    manifest = release / 'release-manifest.json'
    if not manifest.exists():
        return dict(status='unpackaged', verified=False,
                    note='Development source; no common release manifest.')
    raise ValueError('Legacy release packages are not supported by this source tree.')
