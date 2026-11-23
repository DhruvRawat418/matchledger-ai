"""Rebind copied case archives after moving a project; verify every source hash."""
import argparse
import hashlib
import sqlite3
from pathlib import Path
from src.case_store import CaseStore


def relocate(db_path):
    store = CaseStore(db_path)
    archive = db_path.parent.resolve() / 'document_archive'
    changes=[]
    for key in store.list_ids():
        version, record = store.load(key)
        changed=False
        for upload in record.get('uploads', {}).values():
            target=archive / Path(upload['file_path']).name
            if str(target) == upload['file_path']:
                continue
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=upload['sha256']:
                raise ValueError('Copied archive is missing or differs: '+target.name)
            upload['file_path']=str(target);changed=True
        if changed:changes.append((version,record))
    if changes:
        backup=db_path.with_suffix('.before-relocation.sqlite')
        if backup.exists():raise ValueError('Backup exists; choose a fresh backup before relocating again')
        with sqlite3.connect(db_path) as source, sqlite3.connect(backup) as dest:source.backup(dest)
        for version,record in changes:store.save(record,version)
    return len(changes)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=Path('outputs/document_cases.sqlite'))
    print('Cases relocated:',relocate(parser.parse_args().db))
