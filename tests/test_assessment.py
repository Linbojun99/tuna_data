"""Assessment privacy, provenance, scientific calendar and portable-query tests."""
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from tuna_data import Library
from tuna_data.assessment import (build_assessments,read_assessment,read_native,
    assessment_catalog,copy_assessments,validate_assessments,bundle_assessments)
from tuna_data.assessment.parsers import ss3_results,mfcl_results
from tuna_data.assessment.sources import (validate_public_url,local_vault,git_blob_hash,
    sync_assessments,cached_source)

# Synthetic model output; no real fishery observations.
SS3='''#_3.30_test
TIME_SERIES report:16
Area Yr Era Seas Bio_all Bio_smry SpawnBio Recruit_0 obs_cat:_1 F:_1
1 -1 VIRG 1 150 130 100 40 987654321 0.01
1 1 TIME 1 100 80 60 20 987654321 0.2
1 2 TIME 1 90 70 50 25 987654321 0.3
1 5 FORE 1 80 60 40 18 987654321 0.4

PARAMETERS report:5
Num Label Value Parm_StDev
1 growth_K 0.2 0.01
2 personal_CPUE_q 0.3 0.01

DERIVED_QUANTITIES report:6
Label Value StdDev
SSB_1 60 4
SSB_5 40 5
MSY 10 2

CPUE report:20
Index Value
personal_cpue 987654321

'''
META=dict(stock_id='EPO-BET',species_code='BET',rfmo='IATTC',assessment_year=2024,
          model_family='SS3',model_name='test_model',model_role='reference_model',calendar='quarter_index',
          calendar_origin=1975,biomass_unit='tonnes',spawning_unit='tonnes',recruitment_unit='thousand_fish')


def setup_recipe(tmp_path,content=SS3):
    archive=tmp_path/'public.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('model/Report.sso',content)
        z.writestr('model/ss3.par','growth_K 0.2\n')
        z.writestr('model/data.ss','DO_NOT_COPY_OBSERVATIONS')
        z.writestr('model/logbook.csv','DO_NOT_COPY_LOGBOOK')
        z.writestr('__MACOSX/._Report.sso','resource fork')
    source=dict(id='public_test',url='https://www.iattc.org/public-test.zip',kind='zip',
                access='public_download',sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    recipe=dict(schema_version=1,stocks=[dict(stock_id='EPO-BET',species_code='BET',scientific_name='Thunnus obesus',stock_name='Eastern Pacific bigeye',rfmo='IATTC')],sources=[source],groups=[
        dict(META,source_id='public_test',reader='ss3',member_pattern=r'model/Report\.sso'),
        dict(META,source_id='public_test',reader='asset',asset_role='parameter',member_pattern=r'model/ss3\.par')])
    config=tmp_path/'recipe.json';config.write_text(json.dumps(recipe))
    vault=tmp_path/'vault';lib=Library(tmp_path/'library')
    sync_assessments(config,cache=vault,library=lib,candidates={'public_test':str(archive)},progress=lambda _:None)
    return config,vault,lib,source


def test_calendar_and_observation_exclusion():
    p=ss3_results(SS3,META)
    s=[r for r in p['series'] if r['source_section']=='TIME_SERIES:SpawnBio']
    assert [(r['year'],r['season'],r['era']) for r in s]==[(1974,3,'equilibrium'),(1975,1,'historical'),(1975,2,'historical'),(1976,1,'forecast')]
    assert all(r['unit']=='tonnes' for r in s)
    assert '987654321' not in json.dumps(p)
    assert not any(x['name']=='CPUE' for x in p['sections'])


def test_snapshot_queries_copy_and_hashes(tmp_path):
    config,vault,lib,source=setup_recipe(tmp_path)
    result=build_assessments(config,cache=vault,library=lib,progress=lambda _:None)
    root=Path(result['run_dir'])
    checked=validate_assessments(library=lib)
    assert checked['counts']['models']==1
    assert checked['counts']['parameters']==1  # private CPUE-labelled parameter removed
    files=read_assessment('files',library=lib)
    assert len(files)==1 and files.iloc[0].model_id==assessment_catalog(library=lib).iloc[0].model_id
    assert not list(root.rglob('*.zip')) and not list(root.rglob('*.sso'))
    selected=read_assessment(library=lib,species='bet',year=1975,metric='spawning_quantity',section='TIME_SERIES:SpawnBio')
    assert list(selected.value)==[60,50]
    assert read_assessment(library=lib,year=1976).empty
    assert not read_assessment(library=lib,year=1976,include_forecast=True).empty
    assert read_assessment(library=lib,species=[]).empty
    with pytest.raises(ValueError):read_assessment('series; DROP TABLE series',library=lib)
    with pytest.raises(ValueError):read_assessment(library=lib,metric="'; DROP TABLE series --",bad_filter=1)
    sections=read_assessment('sections',library=lib)
    for _,row in sections.iterrows():
        native=read_native(row.model_id,row.section_id,library=lib)
        assert '987654321' not in native.to_string() and 'personal_CPUE' not in native.to_string()
    destination=tmp_path/'NAS'
    copy_assessments(destination,library=lib)
    other=Library(destination)
    assert assessment_catalog(library=other).equals(assessment_catalog(library=lib))
    packed=bundle_assessments(tmp_path/'results.zip',library=lib)
    with zipfile.ZipFile(packed['file']) as archive:
        assert archive.testzip() is None
        assert any(n.endswith('/assessments.sqlite') for n in archive.namelist())
        assert not any(n.endswith(('.sso','.ss','.rep')) for n in archive.namelist())
        assert not any('logbook' in n.lower() for n in archive.namelist())
    (root/'unexpected.csv').write_text('private data')
    with pytest.raises(ValueError,match='extra files'):copy_assessments(tmp_path/'other',library=lib)
    (root/'unexpected.csv').unlink()
    with (root/'assessments.sqlite').open('ab') as f:f.write(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):read_assessment(library=lib)


def test_source_changes_and_failed_build_do_not_advance_latest(tmp_path):
    config,vault,lib,source=setup_recipe(tmp_path)
    build_assessments(config,cache=vault,library=lib,progress=lambda _:None)
    pointer=(lib.root/'processed/assessments/latest.json').read_bytes()
    spec=json.loads(config.read_text());spec['groups'][0]['member_pattern']='missing-report.sso';config.write_text(json.dumps(spec))
    with pytest.raises(ValueError,match='No matching'):build_assessments(config,cache=vault,library=lib,progress=lambda _:None)
    assert (lib.root/'processed/assessments/latest.json').read_bytes()==pointer
    path,_=cached_source(vault,source);path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='Corrupt'):cached_source(vault,source)


@pytest.mark.parametrize('url',[
    'http://www.iattc.org/results.zip','https://example.org/results.zip',
    'https://user:secret@www.iattc.org/results.zip','https://www.iattc.org/x?token=secret',
    'https://www.iattc.org/logbook.csv','https://www.iattc.org/personal-cpue.csv'])
def test_only_public_declared_urls(url):
    with pytest.raises(ValueError):validate_public_url(url)


def test_source_vault_cannot_be_shared_or_symlinked(tmp_path):
    cloud=tmp_path/'CloudStorage'/'Drive';cloud.mkdir(parents=True)
    link=tmp_path/'alias';link.symlink_to(cloud,target_is_directory=True)
    with pytest.raises(ValueError):local_vault(link)
    lib=Library(tmp_path/'library')
    with pytest.raises(ValueError):local_vault(lib.root/'sources',lib)


def test_mfcl_dimensions_missing_values_and_unpooled_regions():
    text='''# Number of time periods
4
# Year 1
2000
# Number of regions
2
# Number of recruitments per year
4
# Number of age classes
3
# Total biomass
10 20
11 21
12 22
13 23
# Adult biomass
2 3
3 4
4 5
5 6
# Catch observed
987654321
'''
    p=mfcl_results(text,{})
    assert len(p['series'])==16
    assert {r['region'] for r in p['series']}=={'1','2'}
    assert {r['season'] for r in p['series']}=={1,2,3,4}
    assert '987654321' not in json.dumps(p)
    assert all(r['unit']=='source_spawning_potential_units' for r in p['series'] if r['metric']=='spawning_quantity')
    with pytest.raises(ValueError,match='shape'):mfcl_results(text.replace('13 23','13'),{})


def test_iccat_summary_rejects_observation_columns(monkeypatch):
    import pandas as pd
    from tuna_data.assessment import reported
    public=pd.DataFrame({'mod_type':['model1'],'Value':[1.2],'year':[2020],'var':['SSB'],'Label':['SSB_2020']})
    monkeypatch.setattr(reported,'_r_tables',lambda *args:{'data':public})
    out=list(reported.read_reported(b'',dict(reader='iccat_rds',summary_kind='timeseries')))
    assert out[0][1]['series'][0]['value']==1.2
    public['observed_cpue']=[999]
    with pytest.raises(ValueError,match='Unapproved column'):
        list(reported.read_reported(b'',dict(reader='iccat_rds',summary_kind='timeseries')))
