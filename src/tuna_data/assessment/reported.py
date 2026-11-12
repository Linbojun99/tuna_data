"""Explicit readers for published summary databases and report tables."""
from __future__ import annotations

import io
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from functools import lru_cache

import pandas as pd

from .parsers import row,number


@lru_cache(maxsize=2)
def _pdf(content):
    from pypdf import PdfReader
    return PdfReader(io.BytesIO(content))


@lru_cache(maxsize=8)
def _pdf_text(content,pages,extractor):
    if extractor=='pypdf':
        pdf=_pdf(content)
        return '\n'.join(pdf.pages[i-1].extract_text(extraction_mode='layout') for i in pages)
    if extractor!='pdftotext':raise ValueError('Unsupported PDF text extractor')
    with TemporaryDirectory() as tmp:
        path=Path(tmp)/'report.pdf';path.write_bytes(content);result=[]
        for page in pages:
            try:
                process=subprocess.run(['pdftotext','-f',str(page),'-l',str(page),'-layout',str(path),'-'],
                                       capture_output=True,text=True,timeout=120)
            except FileNotFoundError as exc:
                raise RuntimeError('This report template requires Poppler pdftotext; see the assessment manual') from exc
            if process.returncode:raise ValueError('PDF text extraction failed: '+process.stderr[-500:])
            result.append(process.stdout)
        return '\n'.join(result)


def empty_result(version='source_reported'):
    return dict(version=version,series=[],parameters=[],reference_points=[],diagnostics=[],sections=[])


def _r_tables(content,suffix):
    # Read trusted public serialized DATA with base R. Never source an R script
    # obtained from a provider, run an assessment, or load an .Rprofile.
    with TemporaryDirectory() as tmp:
        path=Path(tmp)/('public'+suffix);path.write_bytes(content)
        script=Path(tmp)/'read.R'
        script.write_text('''args <- commandArgs(TRUE)
if (grepl("\\\\.(rda|rdata)$", args[1], ignore.case=TRUE)) {
  e <- new.env(parent=emptyenv()); load(args[1], envir=e)
  x <- mget(ls(e), envir=e)
} else x <- list(data=readRDS(args[1]))
for (n in names(x)) {
  if (is.data.frame(x[[n]])) write.csv(x[[n]], file.path(args[2],paste0(n,".csv")),row.names=FALSE,na="")
}
''')
        result=subprocess.run(['Rscript','--vanilla',str(script),str(path),tmp],capture_output=True,text=True)
        if result.returncode:raise RuntimeError('Reading the public R data object failed: '+result.stderr[-1000:])
        return {p.stem:pd.read_csv(p,keep_default_na=False) for p in Path(tmp).glob('*.csv')}


def read_reported(content,meta):
    reader=meta['reader']
    if reader=='iccat_rds':
        tables=_r_tables(content,'.rds');t=tables['data'];required={'mod_type'}
        if meta['summary_kind'] not in ('biology','parameters'):required.add('Value')
        if not required<=set(t):raise ValueError('Unexpected ICCAT public summary schema')
        schemas={
            'timeseries':{'Label','Value','year','var','mod_type'},
            'kobe':{'Label','Value','year','var','mod_type'},
            'derived_quantities':{'Label','Value','StdDev','mod_type'},
            'biology':{'Seas','Age_Beg','M','Len_Beg','mod_type'},
            'parameters':{'M','LAmin','LAmax','K','gamma','CV1','CVA','R0','h','sigmaR','mod_type'},
        }
        if meta['summary_kind'] not in schemas or set(t)-schemas[meta['summary_kind']]:
            raise ValueError('Unapproved column in ICCAT result summary')
        if 'var' in t and set(t['var'])-{'SSB','F'}:
            raise ValueError('Unapproved metric in ICCAT result summary')
        for name,part in t.groupby('mod_type',sort=True):
            m=dict(meta,model_name=str(name));p=empty_result()
            p['sections']=[dict(name=meta['summary_kind'],columns=list(part),rows=part.values.tolist(),metadata={'source_role':'public_assessment_summary'})]
            for _,d in part.iterrows():
                if meta['summary_kind'] in ('biology','parameters'):
                    for metric in part.columns:
                        if metric in ('mod_type','Seas','Age_Beg'):continue
                        p['parameters'].append(row(metric=str(metric),value=number(d[metric]),
                            age=str(d['Age_Beg']) if 'Age_Beg' in d else 'all',
                            season=int(d['Seas']) if 'Seas' in d else None,era='parameter',source_section=meta['summary_kind']))
                elif meta['summary_kind'] in ('timeseries','kobe'):
                    metric=str(d['var']);unit=meta.get('units',{}).get(metric,'source_native')
                    if meta['summary_kind']=='kobe':metric+='__relative_to_msy';unit='ratio'
                    p['series'].append(row(metric=metric,year=int(d['year']),value=number(d['Value']),unit=unit,source_section=meta['summary_kind']))
                else:p['reference_points'].append(row(metric=str(d['Label']),value=number(d['Value']),std_error=number(d.get('StdDev')),era='reference',source_section='derived_quantities'))
            yield m,p
    elif reader=='public_csv':
        t=pd.read_csv(io.BytesIO(content),keep_default_na=False)
        for key in meta.get('required_columns',[]):
            if key not in t:raise ValueError('Missing published table column '+key)
        model_col=meta.get('model_column');groups=t.groupby(model_col,sort=True) if model_col else [(meta['model_name'],t)]
        for name,part in groups:
            m=dict(meta,model_name=str(name));p=empty_result()
            selected=meta['columns'];part=part[selected]
            p['sections']=[dict(name=meta.get('section_name','published_table'),columns=list(part),rows=part.values.tolist(),metadata={})]
            for _,d in part.iterrows():
                for metric,field in meta.get('metrics',{}).items():
                    p['series'].append(row(metric=metric,year=int(d[meta['year_column']]) if meta.get('year_column') else None,
                        value=number(d[field]),unit=meta.get('units',{}).get(metric,'source_native'),source_section=meta.get('section_name','published_table')))
            yield m,p
    elif reader=='pdf_table':
        p=empty_result();m=dict(meta)
        for table in meta['tables']:
            pages=table['pages'];extractor=table.get('extractor',meta.get('extractor','pypdf'))
            text=_pdf_text(content,tuple(pages),extractor)
            if table['heading'] not in text:raise ValueError('Published table heading changed: '+table['heading'])
            if table.get('start_after'):
                if table['start_after'] not in text:raise ValueError('Published sub-table start changed')
                text=text.split(table['start_after'],1)[1]
            if table.get('end_before'):
                if table['end_before'] not in text:raise ValueError('Published sub-table end changed')
                text=text.split(table['end_before'],1)[0]
            rows=[]
            for line in text.splitlines():
                found=re.fullmatch(table['pattern'],line.strip())
                if not found:continue
                d=found.groupdict();rows.append(d)
                for metric,field in table['metrics'].items():
                    clean=lambda v:number(v.replace(',','').replace(' ','')) if v is not None else None
                    r=row(metric=metric,year=int(d['year']) if d.get('year') else table.get('year'),value=clean(d[field]),
                          unit=table.get('units',{}).get(metric,'source_native'),source_section=table['heading']+'; PDF pages '+','.join(map(str,pages)))
                    for key in ('lower','upper','std_error'):
                        if table.get(key,{}).get(metric):r[key]=clean(d[table[key][metric]])
                    r['interval_type']=table.get('interval_type')
                    r['statistic']=table.get('statistic','estimate')
                    r['era']=table.get('era','historical')
                    r.update(table.get('row_metadata',{}))
                    p[table.get('destination','series')].append(r)
            if len(rows)!=table['expected_rows']:raise ValueError(f'Published table row count changed: {table["heading"]}: {len(rows)}')
            p['sections'].append(dict(name=table['heading'],columns=list(rows[0]),rows=[list(d.values()) for d in rows],metadata={'pdf_pages':pages,'units':table.get('units',{}),'extractor':extractor}))
        yield m,p
    else:raise ValueError('Unsupported published result reader: '+reader)
