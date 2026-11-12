"""Validate and copy complete result snapshots, never the mixed source vault."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4
import zlib
import zipfile

from filelock import FileLock

from ..library import Library
from ..storage import atomic_json, checksum, contained
from .sources import FORBIDDEN, load_recipe
from .store import run_directory

TOP_FILES={'assessments.sqlite','models.csv','coverage.csv','sources.csv','files.csv',
           'exclusions.csv','recipe.lock.json','provenance.json'}


def _files(root):
    return [p for p in root.rglob('*') if p.is_file() and not any(x.startswith('._') for x in p.relative_to(root).parts)]


def write_file_manifest(root):
    root=Path(root)
    atomic_json(root/'file_manifest.json',{'files':[
        {'path':p.relative_to(root).as_posix(),'sha256':checksum(p),'bytes':p.stat().st_size}
        for p in sorted(_files(root)) if p.name!='file_manifest.json']})


def validate_assessments(*,run=None,library=None):
    """Full hash, relational, result-section and asset validation before sharing."""
    root=run_directory(run,library)
    spec=load_recipe(root/'recipe.lock.json')
    provenance=json.loads((root/'provenance.json').read_text())
    manifest=json.loads((root/'file_manifest.json').read_text())['files']
    indexed={x['path']:x for x in manifest}
    if len(indexed)!=len(manifest):raise ValueError('Duplicate manifest paths')
    actual={p.relative_to(root).as_posix() for p in _files(root)}-{'file_manifest.json'}
    if actual!=set(indexed):raise ValueError('Assessment snapshot contains missing or unapproved extra files')
    for name,record in indexed.items():
        p=contained(root,name)
        if p.is_symlink() or checksum(p)!=record['sha256'] or p.stat().st_size!=record['bytes']:
            raise ValueError('Assessment file failed validation: '+name)
    if indexed['assessments.sqlite']['sha256']!=provenance['database_sha256']:
        raise ValueError('Database does not match its provenance')
    conn=sqlite3.connect((root/'assessments.sqlite').as_uri()+'?mode=ro',uri=True)
    try:
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Invalid SQLite database')
        rows=conn.execute('SELECT source_id,url,sha256 FROM sources').fetchall()
        expected={s['id']:(s['url'],s['sha256']) for s in spec['sources']}
        if {r[0]:tuple(r[1:]) for r in rows}!=expected:raise ValueError('Unverified source in assessment database')
        allowed=TOP_FILES.copy()
        for path,digest,role in conn.execute('SELECT relative_path,sha256,asset_role FROM files'):
            if role not in ('parameter','model_code') or not path.startswith('model_assets/'):
                raise ValueError('Unapproved assessment asset')
            if path not in indexed or indexed[path]['sha256']!=digest:raise ValueError('Asset is absent from file manifest')
            if FORBIDDEN.search(path) or FORBIDDEN.search(contained(root,path).read_text()):
                raise ValueError('Excluded content in assessment asset')
            allowed.add(path)
        if actual!=allowed:raise ValueError('Only approved outputs and parameter/code assets can be copied')
        for sid,payload in conn.execute('SELECT section_id,payload FROM section_payloads'):
            text=zlib.decompress(payload).decode()
            if FORBIDDEN.search(text):raise ValueError('Excluded content in result section '+sid)
        for table in ['series','parameters','reference_points','diagnostics','sections']:
            orphan=conn.execute('SELECT COUNT(*) FROM '+table+' v LEFT JOIN models m USING(model_id) WHERE m.model_id IS NULL').fetchone()[0]
            if orphan:raise ValueError('Unlinked assessment result rows')
        counts={table:conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in provenance['counts']}
        if counts!=provenance['counts']:raise ValueError('Assessment counts differ from provenance')
        flags=conn.execute('SELECT COUNT(*) FROM series WHERE quality_flag IS NOT NULL').fetchone()[0]
    finally:conn.close()
    return {'run_dir':str(root),'database_sha256':provenance['database_sha256'],
            'counts':counts,'files_verified':len(indexed),'flagged_series_rows':flags}


def copy_assessments(destination,*,run=None,library=None):
    """Copy a validated immutable run to another library root (Drive/NAS/local)."""
    root=run_directory(run,library);checked=validate_assessments(run=root)
    meta=json.loads((root/'provenance.json').read_text())
    parent=Library(destination).root/'processed'/'assessments';parent.mkdir(parents=True,exist_ok=True)
    target=contained(parent,meta['run_id'])
    with FileLock(str(parent/'.copy.lock'),timeout=300):
        if target.exists():
            existing=validate_assessments(run=target)
            if existing['database_sha256']!=checked['database_sha256']:raise ValueError('Destination run already differs')
        else:
            stage=parent/('.'+meta['run_id']+'.copying-'+uuid4().hex[:8]);stage.mkdir()
            try:
                for src in _files(root):
                    dst=stage/src.relative_to(root);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
                validate_assessments(run=stage)
                os.replace(stage,target)
            except BaseException:
                shutil.rmtree(stage)
                raise
        atomic_json(parent/'latest.json',{'run_id':meta['run_id'],'database_sha256':checked['database_sha256']})
    return {'run_dir':str(target),'database_sha256':checked['database_sha256'],'files_verified':checked['files_verified']}


def bundle_assessments(destination,*,run=None,library=None):
    """Portable ZIP of validated results only; the source vault is never included."""
    root=run_directory(run,library);checked=validate_assessments(run=root)
    target=Path(destination).expanduser().resolve()
    if target==root or root in target.parents:raise ValueError('Write bundles outside their immutable run')
    target.parent.mkdir(parents=True,exist_ok=True)
    stage=target.with_name('.'+target.name+'.'+uuid4().hex[:8]+'.partial')
    try:
        with zipfile.ZipFile(stage,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as archive:
            for p in sorted(_files(root)):archive.write(p,root.name+'/'+p.relative_to(root).as_posix())
        with zipfile.ZipFile(stage) as archive:
            if archive.testzip() is not None:raise ValueError('Result bundle verification failed')
        os.replace(stage,target)
    finally:stage.unlink(missing_ok=True)
    return dict(file=str(target),sha256=checksum(target),bytes=target.stat().st_size,
                database_sha256=checked['database_sha256'])
