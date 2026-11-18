"""Recover the sealed October server inside a disposable November 1 research restore."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


REPO = Path(__file__).resolve().parents[1]
COMMIT = '6fc079be2dc6604f284647563c621237b6f6fac0'
FREEZE = '.codex-build/eat-chainplanwake-20261028/screen-source-freeze.json'
FILES = ('db.py', 'inquiry_observations.py', 'inquiry_policies.py', 'inquiry_workflow.py', 'service.py')


def recover(original, archived, expected):
    if hashlib.sha256(original).hexdigest() == expected:
        return original
    # Git normalized two mixed-ending blobs. Unchanged lines retain the exact
    # endings in the later backup. The seal verifies every reconstructed byte.
    old = original.splitlines(keepends=True)
    later = archived.splitlines(keepends=True)
    normalized = [line.replace(b'\r\n', b'\n') for line in later]
    matcher = difflib.SequenceMatcher(None, old, normalized, autojunk=False)
    pieces = []
    for tag, start, end, a, b in matcher.get_opcodes():
        if tag == 'equal':
            pieces.extend(later[a:b])
        else:
            pieces.extend(line.replace(b'\n', b'\r\n') for line in old[start:end])
    candidate = b''.join(pieces)
    if hashlib.sha256(candidate).hexdigest() != expected:
        raise ValueError('Reconstructed bytes differ from the archived SHA-256')
    return candidate


def restore(root):
    root = root.resolve()
    if (root / '.git').exists() or not (root / 'test_h1_online_engine.py').is_file():
        raise ValueError('Select a separate unpacked research backup with its original root tests')
    freeze = json.loads((root / FREEZE).read_bytes())['source_sha256']
    pending = {}
    for name in FILES:
        path = root / 'server' / name
        if path.resolve().parent != root / 'server':
            raise ValueError(f'Expected an ordinary file in the restored server: {path}')
        expected = freeze['server/' + name]
        archived = path.read_bytes()
        if hashlib.sha256(archived).hexdigest() != expected:
            original = subprocess.check_output(['git', 'show', f'{COMMIT}:server/{name}'], cwd=REPO)
            pending[path] = recover(original, archived, expected)
    for path, content in pending.items():
        with tempfile.TemporaryDirectory(dir=path.parent) as staging:
            temp = Path(staging) / path.name
            temp.write_bytes(content)
            temp.replace(path)
    print(f'Verified {len(FILES)} sealed server files, restored {len(pending)}.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('research_root', type=Path)
    restore(parser.parse_args().research_root)
