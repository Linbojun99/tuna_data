"""Portable read-only SQLite queries; no shared DB writes during analysis."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import zlib

import pandas as pd

from ..library import Library
from ..storage import checksum, contained

TABLES = {'models','stocks','sources','series','parameters','reference_points','coverage',
          'diagnostics','sections','files','exclusions'}
FILTERS={'species':'species_code','stock':'stock_id','assessment_year':'assessment_year',
         'model':'model_id','model_role':'model_role','year':'year','metric':'metric',
         'region':'region','season':'season','source':'source_id','unit':'unit',
         'section':'source_section','model_name':'model_name','rfmo':'rfmo','era':'era',
         'fleet':'fleet','age':'age','sex':'sex','quality_flag':'quality_flag'}


def run_directory(run=None, library=None):
    if run is not None:
        result=Path(run).expanduser().resolve()
    else:
        root=(library or Library()).root/'processed'/'assessments'
        pointer=json.loads((root/'latest.json').read_text())
        result=contained(root,pointer['run_id'])
    return result


def _connection(run=None,library=None,verify=True):
    root=run_directory(run,library)
    meta=json.loads((root/'provenance.json').read_text())
    path=root/'assessments.sqlite'
    if verify and checksum(path)!=meta['database_sha256']:
        raise ValueError('Assessment database checksum mismatch; finish sync or restore this run')
    conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    conn.execute('PRAGMA query_only=ON')
    return root,conn


def read_assessment(table='series',*,run=None,library=None,columns=None,
                    include_forecast=False,verify=True,**filters):
    if table not in TABLES:
        raise ValueError('Unknown assessment table: '+table)
    root,conn=_connection(run,library,verify)
    try:
        known=[x[1] for x in conn.execute(f'PRAGMA table_info("{table}")')]
        selected=list(columns) if columns is not None else known
        if set(selected)-set(known) or not selected:
            raise ValueError('Unknown or empty assessment column selection')
        clauses,params=[],[]
        for key, values in filters.items():
            if values is None:continue
            if key not in FILTERS or FILTERS[key] not in known:
                raise ValueError(f'Filter {key!r} is not available in {table}')
            col=FILTERS[key]
            if isinstance(values,(str,int,float)):
                values=[values]
            else:values=list(values)
            if not values:
                clauses.append('0');continue
            if key=='species':values=[str(v).upper() for v in values]
            clauses.append('"'+col+'" IN ('+','.join('?' for _ in values)+')')
            params.extend(values)
        if not include_forecast and 'era' in known:
            clauses.append("era NOT IN ('forecast','equilibrium','initialization')")
        sql='SELECT '+','.join('"'+s+'"' for s in selected)+' FROM "'+table+'"'
        if clauses:sql+=' WHERE '+' AND '.join(clauses)
        return pd.read_sql_query(sql,conn,params=params)
    finally:conn.close()


def assessment_catalog(*,run=None,library=None,**filters):
    return read_assessment('models',run=run,library=library,**filters)


def read_native(model,section,*,run=None,library=None,verify=True):
    """Return an approved model-result section with its native row/column labels.

    Mixed observation/input sections are never in this database. Numeric values
    can remain strings when the source uses nonnumeric flags; no implicit zero.
    """
    _,conn=_connection(run,library,verify)
    try:
        rows=conn.execute('SELECT payload FROM section_payloads WHERE model_id=? AND section_id=?',
                          (model,section)).fetchall()
        if len(rows)!=1:
            raise KeyError('Select an exact section_id from the sections table')
        data=json.loads(zlib.decompress(rows[0][0]))
        frame=pd.DataFrame(data['rows'],columns=data['columns'])
        frame.attrs.update(data.get('metadata',{}))
        return frame
    finally:conn.close()


def assessment_file(file_id,*,run=None,library=None,verify=True):
    root,conn=_connection(run,library,verify)
    try:
        row=conn.execute('SELECT relative_path,sha256 FROM files WHERE file_id=?',(file_id,)).fetchone()
        if row is None:raise KeyError(file_id)
        path=contained(root,row[0])
        if verify and checksum(path)!=row[1]:raise ValueError('Assessment asset checksum mismatch')
        return path
    finally:conn.close()
