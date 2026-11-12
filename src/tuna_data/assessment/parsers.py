"""Conservative readers for public model RESULTS, never fishery observations.

Native indices and model units are retained. Calendar conversion is explicit in
source metadata; a numeric SS3 Yr is not assumed to be a calendar year.
"""
from __future__ import annotations

import math
import re

NA = {'','NA','NaN','nan','-nan','_','NULL','Inf','-Inf','inf','-inf'}


def number(value):
    if value is None or str(value) in NA:return None
    try:
        result=float(str(value).replace('D','E'))
        return result if math.isfinite(result) else None
    except (ValueError,TypeError):return None


def row(**values):
    result=dict(metric=None,year=None,season=None,region='all',fleet='all',age='all',sex='all',
                era='historical',statistic='estimate',value=None,std_error=None,lower=None,upper=None,
                unit='source_native',source_section='',source_row=None,time_native=None,interval_type=None)
    result.update(values)
    return result


def _calendar(native,meta):
    if number(native) is None:return None,None
    n=int(float(native))
    mode=meta.get('calendar','native')
    if mode=='quarter_index':
        base=int(meta['calendar_origin'])
        return base+(n-1)//4,(n-1)%4+1
    if mode in ('calendar_year','July_June_start_year'):
        return n,None
    return None,None


def _ss_table(lines,section,header):
    starts=[i for i,l in enumerate(lines) if re.match(r'^'+re.escape(section)+r'\s+report:',l)]
    if not starts:return None
    if len(starts)!=1:raise ValueError('Ambiguous SS3 section '+section)
    start=starts[0]
    end=next((i for i in range(start+1,len(lines)) if re.search(r'\breport:\d+',lines[i])),len(lines))
    h=next((i for i in range(start+1,min(end,start+30)) if lines[i].split()[:len(header)]==header),None)
    if h is None:return None
    cols=lines[h].split()
    if '//' in cols:cols=cols[:cols.index('//')]
    rows=[];linenos=[]
    for i in range(h+1,end):
        parts=lines[i].split()
        if not parts:
            if rows:break
            continue
        if parts[0].startswith('#'):continue
        if len(parts)<2:break
        # Table rows may omit trailing statistics for fixed parameters.
        if len(parts)>len(cols):
            raise ValueError(f'SS3 {section}: row {i+1} exceeds its header')
        rows.append(parts+[None]*(len(cols)-len(parts)));linenos.append(i+1)
    return cols,rows,linenos


def ss3_results(text,meta):
    lines=text.splitlines();sections=[];series=[];parameters=[];refs=[];diagnostics=[]
    version=lines[0].split(';')[0].lstrip('#') if lines else ''
    for label in ['Convergence_Level','Hessian','Final_phase','N_iterations','total_LogL']:
        match=next((re.match(re.escape(label)+r':\s*(\S+)',l) for l in lines if l.startswith(label+':')),None)
        if match:diagnostics.append(row(metric=label,value=number(match[1]),source_section='report_header'))
    eras={'TIME':'historical','FORE':'forecast','VIRG':'equilibrium','INIT':'initialization'}
    table=_ss_table(lines,'TIME_SERIES',['Area','Yr','Era','Seas'])
    temporal={}
    if table:
        cols,rows,linenos=table
        allowed=[i for i,c in enumerate(cols) if c in {'Area','Yr','Era','Seas','Bio_all','Bio_smry','SpawnBio','Recruit_0','mature_bio','mature_num'}
                 or c.startswith(('SpawnBio_GP:','SmryBio_SX:','SmryNum_SX:','F:'))]
        sections.append(dict(name='TIME_SERIES',columns=[cols[i] for i in allowed],
                             rows=[[r[i] for i in allowed] for r in rows],line_numbers=linenos,
                             metadata={'calendar':meta.get('calendar','native'),'observations_excluded':True}))
        metrics={'Bio_all':('total_biomass',meta.get('biomass_unit','source_biomass_units')),
                 'Bio_smry':('summary_biomass',meta.get('biomass_unit','source_biomass_units')),
                 'SpawnBio':('spawning_quantity',meta.get('spawning_unit','source_spawning_units')),
                 'Recruit_0':('recruitment',meta.get('recruitment_unit','source_recruitment_units'))}
        for values,line in zip(rows,linenos):
            d=dict(zip(cols,values));y,s=_calendar(d['Yr'],meta)
            if s is None:s=int(float(d['Seas']))
            era=eras.get(d['Era'],'source_reported')
            temporal[str(d['Yr'])]=(y,s,era)
            for raw,(metric,unit) in metrics.items():
                if raw in d:
                    series.append(row(metric=metric,value=number(d[raw]),year=y,season=s,region=d['Area'],
                                      era=era,unit=unit,time_native=d['Yr'],source_section='TIME_SERIES:'+raw,source_row=line))
            for name in cols:
                if name.startswith('F:'):
                    series.append(row(metric='fishing_mortality_fleet',value=number(d[name]),year=y,season=s,
                                      region=d['Area'],fleet=name.split(':',1)[1].strip('_'),era=era,
                                      unit='source_F_definition',time_native=d['Yr'],source_section='TIME_SERIES:'+name,source_row=line))
    for section,header in [('DERIVED_QUANTITIES',['Label','Value']),('PARAMETERS',['Num','Label','Value'])]:
        table=_ss_table(lines,section,header)
        if not table:continue
        cols,rows,linenos=table
        # Derived quantities / fitted parameters contain no input observations.
        sections.append(dict(name=section,columns=cols,rows=rows,line_numbers=linenos,metadata={}))
        for values,line in zip(rows,linenos):
            d=dict(zip(cols,values));label=d['Label'];value=number(d.get('Value'))
            se=number(d.get('StdDev') if section=='DERIVED_QUANTITIES' else d.get('Parm_StDev'))
            if section=='PARAMETERS':
                parameters.append(row(metric=label,value=value,std_error=se,source_section=section,source_row=line,era='parameter'))
                continue
            match=re.fullmatch(r'(SSB|Recr|Bratio|SPRratio|F|TotBio|SmryBio)_(-?\d+)',label)
            if match:
                raw,native=match.groups();y,s,era=temporal.get(native,(*_calendar(native,meta),'source_reported'))
                metric,unit={'SSB':('spawning_quantity',meta.get('spawning_unit','source_spawning_units')),
                             'Recr':('recruitment',meta.get('recruitment_unit','source_recruitment_units')),
                             'Bratio':('biomass_ratio','ratio_source_denominator'),
                             'SPRratio':('SPR_report','ratio_source_SPR_definition'),
                             'F':('F_report','source_F_report_definition'),
                             'TotBio':('total_biomass',meta.get('biomass_unit','source_biomass_units')),
                             'SmryBio':('summary_biomass',meta.get('biomass_unit','source_biomass_units'))}[raw]
                series.append(row(metric=metric,value=value,std_error=se,year=y,season=s,era=era,unit=unit,
                                  time_native=native,source_section=section+':'+raw,source_row=line))
            else:
                refs.append(row(metric=label,value=value,std_error=se,source_section=section,source_row=line,era='reference'))
    # Biology/age/size/selectivity result sections contain multiple sub-tables.
    # Preserve their result text in rows without inventing a dimension mapping.
    safe={'BIOLOGY','BIOLOGY_AT_AGE','BIOLOGY_AGE','AGE_SELEX','LEN_SELEX','NUMBERS_AT_AGE',
          'NUMBERS_AT_LENGTH','FISHING_MORTALITY','F_AT_AGE','Z_AT_AGE','MEAN_BODY_WT',
          'SPR_YPR_PROFILE','SPR/YPR_Profile','GLOBAL_MSY','DYNAMIC_BZERO','Dynamic_Bzero'}
    starts=[(i,re.match(r'^(\S+)\s+report:',l)[1]) for i,l in enumerate(lines) if re.match(r'^(\S+)\s+report:',l)]
    for k,(i,name) in enumerate(starts):
        if name not in safe:continue
        end=starts[k+1][0] if k+1<len(starts) else len(lines)
        block=lines[i+1:end]
        if re.search(r'log[ _-]?book|observed[ _-]?cpue|vessel[ _-]?id|mmsi','\n'.join(block),re.I):continue
        sections.append(dict(name=name,columns=['source_line','text'],rows=[[j+1,lines[j]] for j in range(i+1,end)],metadata={'format':'native_result_text'}))
    if not series:raise ValueError('No supported model time-series results in this SS3 report')
    return dict(version=version,series=series,parameters=parameters,reference_points=refs,diagnostics=diagnostics,sections=sections)


def _mfcl_blocks(text):
    blocks=[];name=None;rows=[];line_numbers=[];parent=None
    for i,line in enumerate(text.splitlines(),1):
        if line.lstrip().startswith('#'):
            new=line.lstrip()[1:].strip()
            if not new:continue
            if name is not None:blocks.append((name,rows,line_numbers))
            if re.match(r'Region\b|Projected years\b',new,re.I) and parent:
                name=parent+' / '+new
            else:
                name=new;parent=new
            rows=[];line_numbers=[]
        elif line.strip():
            rows.append(line.split());line_numbers.append(i)
    if name is not None:blocks.append((name,rows,line_numbers))
    return blocks


def mfcl_results(text,meta):
    blocks=_mfcl_blocks(text)
    lookup={name:(values,lines) for name,values,lines in blocks}
    def integer(name):
        v=lookup[name][0]
        if len(v)!=1 or len(v[0])!=1 or number(v[0][0]) is None:raise ValueError('Invalid MFCL dimension '+name)
        return int(float(v[0][0]))
    nt=integer('Number of time periods');nr=integer('Number of regions')
    frequency=integer('Number of recruitments per year');start=integer('Year 1')
    if frequency not in (1,2,4,12) or nt%frequency:raise ValueError('Unsupported/incomplete MFCL calendar')
    ns=integer('Number of age classes');series=[];refs=[];sections=[];parameters=[]
    states={'Total biomass':('total_biomass',meta.get('biomass_unit','source_biomass_units')),
            'Total biomass in absence of fishing':('total_biomass_unfished',meta.get('biomass_unit','source_biomass_units')),
            'Adult biomass':('spawning_quantity',meta.get('spawning_unit','source_spawning_potential_units')),
            'Adult biomass in absence of fishing':('spawning_quantity_unfished',meta.get('spawning_unit','source_spawning_potential_units')),
            'Recruitment':('recruitment',meta.get('recruitment_unit','number'))}
    ratios={'Aggregate F over F at MSY':('F_over_Fmsy','ratio'),
            'Adult biomass over adult biomass at MSY':('spawning_over_spawning_msy','ratio'),
            'Aggregate F':('aggregate_F','source_F_definition')}
    safe=re.compile(r'^(?:Number of |Year 1$|Mean lengths at age|SD of length at age|Mean weights at age|Natural mortality at age|Total biomass(?: in absence of fishing)?$|Adult biomass(?: in absence of fishing)?$|Recruitment$|Selectivity by age class|Fishing mortality by age class|Population Number by age|MSY$|F at MSY$|Adult biomass at MSY$|Total biomass at MSY$|current Adult Biomass|current Total Biomass|F multiplier at MSY|Aggregate F(?: over F at MSY)?$|Adult biomass over adult biomass at MSY$|Equilibrium |Beverton-Holt|Spawning biomass ratio|Spawning potential|Average age|Mean age)',re.I)
    for name,values,linenos in blocks:
        base=name.split(' / ')[0]
        if not safe.search(base):continue
        width=max((len(r) for r in values),default=0)
        if width:
            sections.append(dict(name=name,columns=['row_index',*[f'column_{i+1}' for i in range(width)]],
                rows=[[i+1,*v,*([None]*(width-len(v)))] for i,v in enumerate(values)],
                metadata={'native_label':name,'start_year':start,'periods_per_year':frequency,'regions':nr,'age_classes':ns},line_numbers=linenos))
        if name in states:
            if len(values)!=nt or any(len(r)!=nr for r in values):raise ValueError('MFCL state shape mismatch: '+name)
            metric,unit=states[name]
            for t,(v,line) in enumerate(zip(values,linenos)):
                for region,cell in enumerate(v,1):
                    value=number(cell)
                    if value is None:raise ValueError('Nonnumeric MFCL state '+name)
                    series.append(row(metric=metric,value=value,year=start+t//frequency,season=t%frequency+1,
                        region=str(region),unit=unit,time_native=t+1,source_section=name,source_row=line))
        elif name in ratios:
            flat=[(cell,line) for v,line in zip(values,linenos) for cell in v]
            if len(flat)!=nt:raise ValueError('MFCL ratio shape mismatch: '+name)
            metric,unit=ratios[name]
            for t,(cell,line) in enumerate(flat):
                series.append(row(metric=metric,value=number(cell),year=start+t//frequency,season=t%frequency+1,
                    unit=unit,time_native=t+1,source_section=name,source_row=line))
        elif len(values)==1 and len(values[0])==1 and not name.startswith(('Number of','Year 1')):
            refs.append(row(metric=name,value=number(values[0][0]),source_section=name,source_row=linenos[0],era='reference'))
        elif base in ('Mean lengths at age','SD of length at age','Mean weights at age','Natural mortality at age'):
            for age,cell in enumerate((c for v in values for c in v),1):
                parameters.append(row(metric=base,value=number(cell),age=str(age),source_section=name,era='parameter'))
    if not series:raise ValueError('No supported MFCL result sections')
    return dict(version=meta.get('model_version','MULTIFAN-CL; see source parameters'),series=series,parameters=parameters,
                reference_points=refs,diagnostics=[],sections=sections,
                dimensions={'time_periods':nt,'start_year':start,'periods_per_year':frequency,'regions':nr,'age_classes':ns})
