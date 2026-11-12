"""Build an immutable public-results SQLite snapshot from a local source vault."""
from __future__ import annotations

from datetime import datetime,timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path,PurePosixPath
import re
import sqlite3
import tarfile
from uuid import uuid4
import zipfile
import zlib

import pandas as pd

from ..library import Library
from ..storage import atomic_json,checksum,utc_now
from .parsers import ss3_results,mfcl_results
from .sources import load_recipe,local_vault,cached_source,FORBIDDEN

MODEL_COLS=['model_id','stock_id','species_code','rfmo','assessment_year','model_family','model_name',
            'model_role','source_id','source_member','model_version','calendar','spawning_definition',
            'qualification','year_min','year_max']
VALUE_COLS=['metric','year','season','region','fleet','age','sex','era','statistic','value','std_error','lower','upper',
            'unit','source_section','source_row','time_native','interval_type','quality_flag']
META_COLS=['model_id','stock_id','species_code','rfmo','assessment_year','model_role','source_id']


def _archive_members(path,kind):
    if kind=='zip':
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if not member.is_dir():yield member.filename,lambda m=member:archive.read(m)
    elif kind in ('tar','tar.gz'):
        with tarfile.open(path,'r:*') as archive:
            for member in archive:
                if member.isfile():
                    yield member.name,lambda m=member:archive.extractfile(m).read()
    else:
        yield '',lambda:path.read_bytes()


def _safe_member(name):
    p=PurePosixPath(name)
    return not p.is_absolute() and '..' not in p.parts and not any(x.startswith(('._','__MACOSX')) for x in p.parts)


def _init_db(conn):
    conn.executescript('''
    PRAGMA journal_mode=DELETE;
    PRAGMA foreign_keys=ON;
    CREATE TABLE models(model_id TEXT PRIMARY KEY,stock_id TEXT,species_code TEXT,rfmo TEXT,assessment_year INTEGER,
      model_family TEXT,model_name TEXT,model_role TEXT,source_id TEXT,source_member TEXT,model_version TEXT,
      calendar TEXT,spawning_definition TEXT,qualification TEXT,year_min INTEGER,year_max INTEGER);
    CREATE TABLE stocks(stock_id TEXT PRIMARY KEY,species_code TEXT,scientific_name TEXT,stock_name TEXT,rfmo TEXT);
    CREATE TABLE sources(source_id TEXT PRIMARY KEY,url TEXT,sha256 TEXT,bytes INTEGER,retrieved_at TEXT,access TEXT,source_type TEXT);
    CREATE TABLE sections(section_id TEXT PRIMARY KEY,model_id TEXT,stock_id TEXT,species_code TEXT,rfmo TEXT,
      assessment_year INTEGER,model_role TEXT,source_id TEXT,section_name TEXT,n_rows INTEGER,n_columns INTEGER,metadata_json TEXT);
    CREATE TABLE section_payloads(section_id TEXT PRIMARY KEY,model_id TEXT,payload BLOB);
    CREATE TABLE files(file_id TEXT PRIMARY KEY,model_id TEXT,stock_id TEXT,species_code TEXT,rfmo TEXT,
      assessment_year INTEGER,model_role TEXT,source_id TEXT,filename TEXT,relative_path TEXT,sha256 TEXT,asset_role TEXT);
    CREATE TABLE exclusions(source_id TEXT,member TEXT,reason TEXT);
    ''')
    for table in ['series','parameters','reference_points','diagnostics']:
        cols=[]
        for col in META_COLS+VALUE_COLS:
            kind='INTEGER' if col in ('assessment_year','year','season','source_row') else 'REAL' if col in ('value','std_error','lower','upper') else 'TEXT'
            cols.append('"'+col+'" '+kind)
        conn.execute('CREATE TABLE '+table+'('+','.join(cols)+')')


def _append(conn,table,rows,columns):
    if not rows:return
    conn.executemany('INSERT INTO '+table+'('+','.join('"'+s+'"' for s in columns)+') VALUES ('+','.join('?' for _ in columns)+')',
                     [[r.get(c) for c in columns] for r in rows])


def _save_model(conn,parsed,meta,source,member):
    signature=json.dumps([meta['stock_id'],meta['assessment_year'],meta['model_name'],source['sha256'],member],ensure_ascii=True)
    if meta.get('model_id_prefix'):
        signature=json.dumps([meta['model_id_prefix'],meta['model_name']])
    mid=meta.get('model_id') or meta['stock_id']+'__'+str(meta['assessment_year'])+'__'+hashlib.sha256(signature.encode()).hexdigest()[:16]
    years=[r['year'] for r in parsed['series'] if r.get('year') is not None and r.get('era')=='historical']
    model=dict(model_id=mid,stock_id=meta['stock_id'],species_code=meta['species_code'],rfmo=meta['rfmo'],
        assessment_year=meta['assessment_year'],model_family=meta['model_family'],model_name=meta['model_name'],
        model_role=meta.get('model_role','source_reported'),source_id=source['id'],source_member=member,
        model_version=parsed.get('version','source_reported'),calendar='calendar_year' if parsed.get('dimensions') else meta.get('calendar','source_reported'),
        spawning_definition=meta.get('spawning_definition','source_native_not_harmonized'),
        qualification=meta.get('qualification',''),year_min=min(years) if years else None,year_max=max(years) if years else None)
    existing=conn.execute('SELECT stock_id,assessment_year,model_name,year_min,year_max FROM models WHERE model_id=?',(mid,)).fetchone()
    if existing:
        if tuple(existing[:3])!=(meta['stock_id'],meta['assessment_year'],meta['model_name']):
            raise ValueError('Conflicting assessment model identity')
        years += [x for x in existing[3:] if x is not None]
        conn.execute('UPDATE models SET year_min=?,year_max=? WHERE model_id=?',
                     (min(years) if years else None,max(years) if years else None,mid))
    else:
        _append(conn,'models',[model],MODEL_COLS)
    for table in ['series','parameters','reference_points','diagnostics']:
        for r in parsed[table]:
            if r.get('lower') is not None and r.get('upper') is not None and r['lower']>r['upper']:
                r['quality_flag']='source_interval_order_invalid'
        values=[dict(**{k:model[k] for k in META_COLS},**r) for r in parsed[table]
                if not FORBIDDEN.search(json.dumps(r,ensure_ascii=False))]
        _append(conn,table,values,META_COLS+VALUE_COLS)
    existing_ids=conn.execute('SELECT section_id FROM sections WHERE model_id=?',(mid,)).fetchall()
    offset=max((int(r[0].rsplit('__section_',1)[1]) for r in existing_ids),default=0)
    for i,s in enumerate(parsed['sections']):
        if FORBIDDEN.search(json.dumps(s,ensure_ascii=False)):
            conn.execute('INSERT INTO exclusions VALUES (?,?,?)',(source['id'],member+'#'+s['name'],'restricted_keyword_in_result_section; local_vault_only'))
            continue
        sid=mid+'__section_'+str(offset+i+1)
        payload=dict(columns=s['columns'],rows=s['rows'],metadata=s.get('metadata',{}))
        if s.get('line_numbers'):payload['metadata']['source_line_numbers']=s['line_numbers']
        packed=zlib.compress(json.dumps(payload,ensure_ascii=False,allow_nan=False).encode(),6)
        conn.execute('INSERT INTO section_payloads VALUES (?,?,?)',(sid,mid,packed))
        _append(conn,'sections',[dict(**{k:model[k] for k in META_COLS},section_id=sid,section_name=s['name'],
                     n_rows=len(s['rows']),n_columns=len(s['columns']),metadata_json=json.dumps(s.get('metadata',{})))],
                     ['section_id',*META_COLS,'section_name','n_rows','n_columns','metadata_json'])
    return model


def _save_asset(conn,root,content,source,member,group,model=None):
    """Only public parameter/code assets; never an input/data/archive/report file."""
    role=group.get('asset_role')
    if role not in ('parameter','model_code'):raise ValueError('Unsupported shareable assessment asset role')
    if FORBIDDEN.search(member):raise ValueError('Excluded asset path')
    text=content.decode('utf-8',errors='strict')
    if FORBIDDEN.search(text):
        conn.execute('INSERT INTO exclusions VALUES (?,?,?)',(source['id'],member,'restricted_keyword_in_public_asset; local_vault_only'))
        return
    suffix=Path(member or source['url']).suffix.lower()
    if role=='parameter' and suffix not in ('.par','.ctl','.ss_new','.cfg','.ss'):
        raise ValueError('Only explicit parameter file formats can be shared')
    if role=='model_code' and suffix not in ('.r','.py','.cpp','.hpp','.h','.c','.sh','.tpl'):
        raise ValueError('Only source code, never binaries, can be shared')
    name=Path(member or source['url']).name
    if re.search(r'(^data|\.dat$|\.frq$|\.tag$|\.csv$|\.zip$)',name,re.I):
        raise ValueError('Observation/input files cannot be shared as parameter assets')
    digest=hashlib.sha256(content).hexdigest()
    file_id=source['id']+'__'+hashlib.sha256(member.encode()).hexdigest()[:12]
    relative='model_assets/'+digest[:16]+'/'+name
    target=root/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
    if model is None:
        matches=conn.execute('SELECT model_id FROM models WHERE stock_id=? AND assessment_year=? AND model_name=?',
            (group.get('stock_id'),group.get('assessment_year'),group.get('model_name'))).fetchall()
        if len(matches)==1:model={'model_id':matches[0][0]}
    record=dict(file_id=file_id,model_id=(model or {}).get('model_id'),stock_id=group.get('stock_id'),
       species_code=group.get('species_code'),rfmo=group.get('rfmo'),assessment_year=group.get('assessment_year'),
       model_role=group.get('model_role'),source_id=source['id'],filename=name,relative_path=relative,sha256=digest,asset_role=role)
    _append(conn,'files',[record],['file_id',*META_COLS,'filename','relative_path','sha256','asset_role'])


def build_assessments(config,*,cache=None,library=None,output=None,progress=print):
    spec=load_recipe(config);lib=library or Library();vault=local_vault(cache,lib)
    parent=Path(output).expanduser() if output else lib.root/'processed'/'assessments'
    parent.mkdir(parents=True,exist_ok=True)
    run_id='Assessment_public_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid4().hex[:8]
    stage=parent/('.'+run_id+'.building');stage.mkdir()
    conn=sqlite3.connect(stage/'assessments.sqlite')
    sources={s['id']:s for s in spec['sources']};records={};models=[]
    try:
        _init_db(conn)
        _append(conn,'stocks',spec['stocks'],['stock_id','species_code','scientific_name','stock_name','rfmo'])
        for source in spec['sources']:
            path,receipt=cached_source(vault,source);records[source['id']]=(path,receipt)
            _append(conn,'sources',[dict(source_id=source['id'],source_type=source.get('kind'),**receipt)],
                     ['source_id','url','sha256','bytes','retrieved_at','access','source_type'])
        for i,group in enumerate(spec.get('groups',[]),1):
            path,receipt=records[group['source_id']];source=sources[group['source_id']]
            kind=source.get('kind','file');matched=0
            pattern=re.compile(group.get('member_pattern','^$'))
            for member,read in _archive_members(path,kind):
                if any(x.startswith(('._','__MACOSX')) for x in PurePosixPath(member).parts):continue
                if not _safe_member(member):raise ValueError('Unsafe archive member')
                match=pattern.fullmatch(member)
                if not match:continue
                matched+=1
                content=read()
                if member.endswith('.gz') or source.get('compression')=='gzip':content=gzip.decompress(content)
                meta=dict(group)
                if group.get('model_name_group'):
                    meta['model_name']=match.group(group['model_name_group'])
                if group['reader'] in ('ss3','mfcl'):
                    parsed=(ss3_results if group['reader']=='ss3' else mfcl_results)(content.decode('utf-8-sig',errors='strict'),meta)
                    model=_save_model(conn,parsed,meta,receipt,member)
                    models.append(model)
                elif group['reader']=='asset':
                    _save_asset(conn,stage,content,receipt,member,meta)
                else:
                    from .reported import read_reported
                    for m,p in read_reported(content,meta):
                        model=_save_model(conn,p,m,receipt,member);models.append(model)
            if matched==0 and group.get('optional'):
                conn.execute('INSERT INTO exclusions VALUES (?,?,?)',(group['source_id'],group.get('member_pattern'), 'optional_output_not_published'))
            elif matched==0:raise ValueError('No matching public model output: '+group['source_id'])
            conn.commit()
            if i%10==0 or i==len(spec['groups']):progress(f'Assessment groups built: {i}/{len(spec["groups"])}; model outputs: {len(models)}')
        conn.executescript('''
        CREATE INDEX series_model_year ON series(model_id,year,metric,region);
        CREATE INDEX series_stock_year ON series(species_code,stock_id,assessment_year,year,metric);
        CREATE INDEX parameters_model ON parameters(model_id,metric);
        CREATE INDEX references_model ON reference_points(model_id,metric);
        CREATE INDEX sections_model ON sections(model_id,section_name);
        CREATE TABLE coverage AS SELECT s.*, COUNT(DISTINCT m.model_id) model_count,
          SUM(CASE WHEN m.model_role='published_report_table' THEN 1 ELSE 0 END) report_model_count,
          SUM(CASE WHEN m.model_id IS NOT NULL AND m.model_role<>'published_report_table' THEN 1 ELSE 0 END) output_model_count,
          MIN(m.year_min) year_min,MAX(m.year_max) year_max,
          MIN(m.assessment_year) assessment_year_min,MAX(m.assessment_year) assessment_year_max,
          CASE WHEN COUNT(m.model_id)=0 THEN 'no_numeric_output_in_this_snapshot' ELSE 'numeric_results_available' END coverage_status
          FROM stocks s LEFT JOIN models m USING(stock_id) GROUP BY s.stock_id;
        ''')
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        stats={table:conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
               for table in ['models','stocks','sources','series','parameters','reference_points','diagnostics','sections','files','exclusions']}
        # Source-member rows and reference estimates may differ in precision and
        # meaning; they remain explicitly identified rather than collapsed.
        for table in ['models','coverage','sources','files','exclusions']:
            pd.read_sql_query('SELECT * FROM '+table,conn).to_csv(stage/(table+'.csv'),index=False)
        conn.commit();conn.close()
        digest=checksum(stage/'assessments.sqlite')
        lock=json.loads(json.dumps(spec))
        for s in lock['sources']:s['sha256']=records[s['id']][1]['sha256']
        atomic_json(stage/'recipe.lock.json',lock)
        policy=dict(public_sources_only=True,personal_cpue_used=False,logbook_uploaded=False,
                    mixed_source_archives='local_vault_only',observational_sections='excluded',
                    allowed_asset_roles=['parameter','model_code'],result_files=['assessments.sqlite','models.csv','coverage.csv','sources.csv','files.csv','exclusions.csv','recipe.lock.json','provenance.json'])
        provenance=dict(schema_version=1,run_id=run_id,created_at=utc_now(),database_sha256=digest,
                        counts=stats,publication_policy=policy,notes=spec.get('notes',[]))
        atomic_json(stage/'provenance.json',provenance)
        from .validation import write_file_manifest,validate_assessments
        write_file_manifest(stage)
        validate_assessments(run=stage)
        target=parent/run_id;os.replace(stage,target)
        atomic_json(parent/'latest.json',dict(run_id=run_id,database_sha256=digest))
        return dict(run_dir=str(target),counts=stats,database_sha256=digest)
    except BaseException:
        conn.close()
        # Keep the failed local stage for diagnosis; never advance latest.
        raise
