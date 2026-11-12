"""Download only declared public assessment sources to a local-only vault.

A model archive can contain observations. It is never copied into the shared
library. Only the result/parameter allowlist in build.py can leave this vault.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

from filelock import FileLock
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..storage import atomic_json, checksum, utc_now

PUBLIC_HOSTS = {'github.com', 'raw.githubusercontent.com', 'media.githubusercontent.com',
                'codeload.github.com', 'api.github.com', 'www.iattc.org', 'iattc.org',
                'iotc.org', 'www.iotc.org', 'iccat.int', 'www.iccat.int', 'isc.fra.go.jp',
                'ccsbt.org', 'www.ccsbt.org', 'meetings.wcpfc.int', 'www.wcpfc.int',
                'nmfs-ost.github.io', 'oceanfish.spc.int'}
FORBIDDEN = re.compile(r'log[ _-]?books?|personal[ _-]?cpue|confidential|vessel[ _-]?(?:id|name)|mmsi', re.I)
SAFE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,180}')


def load_recipe(config):
    path = Path(config).expanduser()
    spec = json.loads(path.read_text(encoding='utf-8'))
    if spec.get('schema_version') != 1:
        raise ValueError('Unsupported assessment recipe schema')
    ids = set()
    for source in spec.get('sources', []):
        if not SAFE_ID.fullmatch(source['id']) or source['id'] in ids:
            raise ValueError('Invalid or duplicate public source ID')
        ids.add(source['id'])
        validate_public_url(source['url'])
        if source.get('access') != 'public_download':
            raise ValueError('Only independently downloadable public sources are allowed')
        for field, pattern in [('sha256', r'[a-f0-9]{64}'), ('git_blob_sha', r'[a-f0-9]{40}')]:
            if source.get(field) and not re.fullmatch(pattern, source[field]):
                raise ValueError(f'Invalid {field} for {source["id"]}')
    for group in spec.get('groups', []):
        if group['source_id'] not in ids:
            raise ValueError('Model group refers to an unknown public source')
    return spec


def validate_public_url(url):
    p = urlparse(url)
    if p.scheme != 'https' or p.hostname not in PUBLIC_HOSTS or p.username or p.password or p.port:
        raise ValueError('Use an allowlisted public HTTPS assessment source without credentials')
    if p.query or p.fragment or FORBIDDEN.search(p.path):
        raise ValueError('Private/logbook paths and credential/query URLs are not assessment sources')


def local_vault(cache=None, library=None):
    root = Path(cache or '~/.cache/tuna-data/public-assessments').expanduser().resolve()
    if 'CloudStorage' in root.parts or any('googledrive' in x.lower() or 'onedrive' in x.lower() for x in root.parts):
        raise ValueError('Mixed source archives must stay in a local-only vault, outside cloud sync')
    if library and (root == library.root or library.root in root.parents):
        raise ValueError('Keep the local source vault outside the shared data library')
    root.mkdir(parents=True, exist_ok=True)
    return root


def git_blob_hash(path):
    p = Path(path)
    h = hashlib.sha1(f'blob {p.stat().st_size}\0'.encode())
    with p.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def check_expected(path, spec):
    if spec.get('sha256') and checksum(path) != spec['sha256']:
        raise ValueError(f'Public source SHA-256 mismatch: {spec["id"]}')
    if spec.get('git_blob_sha') and git_blob_hash(path) != spec['git_blob_sha']:
        raise ValueError(f'Public Git object mismatch: {spec["id"]}')


def cached_source(vault, source):
    receipt = vault / 'receipts' / (source['id'] + '.json')
    if not receipt.exists():
        raise FileNotFoundError(f'Download the public source first: {source["id"]}')
    record = json.loads(receipt.read_text())
    if not re.fullmatch(r'[a-f0-9]{64}',record.get('sha256','')):
        raise ValueError('Invalid cached source digest')
    if record['url'] != source['url']:
        raise ValueError('Recipe URL changed; sync this source before building')
    path = vault / 'objects' / record['sha256'] / 'source'
    if checksum(path) != record['sha256']:
        raise ValueError(f'Corrupt assessment source: {source["id"]}')
    check_expected(path, source)
    return path, record


def sync_assessments(config, *, cache=None, library=None, workers=4, refresh=False,
                     candidates=None, progress=print):
    """Fetch public URLs; a local candidate is reused only against a public hash.

    candidates is an optional private mapping of source ID -> already downloaded
    file. Candidate paths are never written into shared/public metadata.
    """
    spec = load_recipe(config)
    vault = local_vault(cache, library)
    (vault/'receipts').mkdir(exist_ok=True)
    records, errors = [], []
    def fetch(source):
        with FileLock(str(vault/'receipts'/(source['id']+'.lock')), timeout=300):
            if not refresh:
                try:
                    return cached_source(vault, source)[1]
                except (FileNotFoundError, ValueError):
                    pass
            with NamedTemporaryFile(dir=vault, delete=False) as stream:
                tmp = Path(stream.name)
            try:
                candidate = (candidates or {}).get(source['id'])
                reused = False
                if candidate and FORBIDDEN.search(str(candidate)):
                    raise ValueError('Personal CPUE/logbook candidates are excluded')
                if candidate and (source.get('sha256') or source.get('git_blob_sha')):
                    try:
                        check_expected(Path(candidate), source)
                        shutil.copyfile(candidate, tmp)
                        reused = True
                    except (OSError, ValueError):
                        reused = False
                if not reused:
                    # No netrc, bearer token, cookies or personal account credentials.
                    session = requests.Session()
                    session.trust_env = False
                    session.mount('https://',HTTPAdapter(max_retries=Retry(total=3,backoff_factor=1,
                        status_forcelist=[429,500,502,503,504],allowed_methods=['GET'])))
                    with session.get(source['url'], stream=True, timeout=(30,180),
                                     headers={'User-Agent':'tuna-data-public-assessment/1'}) as response:
                        response.raise_for_status()
                        with tmp.open('wb') as stream:
                            for chunk in response.iter_content(1024*1024):
                                stream.write(chunk)
                    session.close()
                check_expected(tmp, source)
                if tmp.stat().st_size == 0:
                    raise ValueError('Empty public source')
                digest = checksum(tmp)
                target = vault/'objects'/digest/'source'
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, target)
                record = dict(id=source['id'],url=source['url'],sha256=digest,
                              bytes=target.stat().st_size, retrieved_at=utc_now(),
                              access='public_download', reused_verified_local_copy=reused)
                atomic_json(vault/'receipts'/(source['id']+'.json'),record)
                return record
            finally:
                tmp.unlink(missing_ok=True)
    with ThreadPoolExecutor(max_workers=max(1,min(int(workers),8))) as pool:
        futures={pool.submit(fetch,s):s for s in spec['sources']}
        for i, future in enumerate(as_completed(futures),1):
            source=futures[future]
            try:
                records.append(future.result())
            except Exception as exc:
                errors.append(dict(source_id=source['id'],error=str(exc)))
                progress(f'Assessment source failed: {source["id"]}: {exc}')
            if i%10==0 or i==len(futures):
                progress(f'Public sources checked: {i}/{len(futures)}; errors: {len(errors)}')
    result=dict(checked_at=utc_now(),sources=sorted(records,key=lambda x:x['id']),errors=errors)
    atomic_json(vault/'last_sync.json',result)
    if errors:
        raise RuntimeError(f'{len(errors)} public sources failed; see local vault last_sync.json')
    return result


def source_catalog(config):
    return pd.DataFrame(load_recipe(config)['sources'])
