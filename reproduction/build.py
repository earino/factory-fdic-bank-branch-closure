import json
import math
import pathlib
import urllib.parse

import pandas as pd

from source_io import fetch

SOD_ENDPOINT = 'https://api.fdic.gov/banks/sod'
HISTORY_ENDPOINT = 'https://api.fdic.gov/banks/history'
SOD_FIELDS = ['YEAR', 'UNINUMBR', 'CERT', 'BKMO', 'BRNUM', 'DEPSUMBR', 'SIMS_ESTABLISHED_DATE', 'BRSERTYP', 'BKCLASS', 'ASSET', 'CB', 'METROBR', 'MICROBR', 'STALPBR', 'BRCENM', 'USA']
SOD_REQUIRED = ['YEAR', 'UNINUMBR', 'CERT', 'BKMO', 'BRNUM', 'DEPSUMBR', 'SIMS_ESTABLISHED_DATE', 'BRSERTYP', 'BKCLASS', 'ASSET', 'METROBR', 'MICROBR', 'STALPBR', 'BRCENM', 'USA']
HISTORY_FIELDS = ['UNINUM', 'EFFDATE', 'CHANGECODE', 'CHANGECODE_DESC', 'ORG_ROLE_CDE']
HISTORY_FILTER = 'CHANGECODE:721 AND EFFDATE:[1900-01-01 TO 2023-06-30]'
YEARS = list(range(2012, 2023))
FEATURES = ['DEPSUMBR', 'SIMS_ESTABLISHED_DATE', 'BRSERTYP', 'BKCLASS', 'ASSET', 'METROBR', 'MICROBR', 'STALPBR', 'BRCENM', 'branch_count_bank', 'branch_deposit_share']
CARRY = ['branch_id', 'cert', 'survey_year']
ID_COLUMN = 'branch_year_id'
TIME_COLUMN = 'snapshot_date'
TARGET = 'closure_next_12m'
COLUMNS = [ID_COLUMN, TIME_COLUMN] + CARRY + FEATURES + [TARGET]
VALID_STATES = set(['AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'DC', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'PR', 'VI', 'GU', 'AS', 'MP', 'FM', 'MH', 'PW'])


def _is_missing(value):
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def _norm_id(value):
    if _is_missing(value):
        return None
    text = str(value).strip()
    if text == '' or text.lower() in ('nan', 'none', 'na', 'n/a', '.'):
        return None
    if text.endswith('.0'):
        try:
            return str(int(float(text)))
        except Exception:
            return text
    return text


def _text_upper(series):
    return series.map(lambda value: None if _is_missing(value) else str(value).strip().upper())


def _number(series):
    def parse(value):
        if _is_missing(value):
            return None
        if isinstance(value, (int, float)):
            return value
        text = str(value).strip()
        if text == '' or text.lower() in ('nan', 'none', 'na', 'n/a', '.'):
            return None
        text = text.replace(',', '').replace('$', '')
        if text.startswith('(') and text.endswith(')'):
            text = '-' + text[1:-1]
        return text
    return pd.to_numeric(series.map(parse), errors='coerce')


def _flag(series):
    text = series.map(lambda value: None if _is_missing(value) else str(value).strip().upper())
    num = _number(text)
    out = pd.Series(pd.NA, index=series.index, dtype='Int64')
    yes = num.eq(1)
    no = num.eq(0)
    truthy = text.isin(['Y', 'YES', 'T', 'TRUE'])
    falsy = text.isin(['N', 'NO', 'F', 'FALSE'])
    out.loc[yes | truthy] = 1
    out.loc[no | falsy] = 0
    return out


def _numeric_or_flag(series):
    num = _number(series)
    if num.notna().any():
        return num
    return _flag(series)


def _date_string(series):
    parsed = pd.to_datetime(series, errors='coerce', utc=True)
    if getattr(parsed.dt, 'tz', None) is not None:
        parsed = parsed.dt.tz_convert(None)
    return parsed.dt.strftime('%Y-%m-%d')


def _fetch_local(url, cache, name):
    result = fetch(url, cache, name)
    if isinstance(result, (str, pathlib.Path)):
        return pathlib.Path(result)
    if isinstance(result, dict):
        for key in ('path', 'local_path', 'file', 'filename'):
            if key in result:
                return pathlib.Path(result[key])
    for attr in ('path', 'local_path', 'file', 'filename'):
        if hasattr(result, attr):
            return pathlib.Path(getattr(result, attr))
    if isinstance(result, (list, tuple)) and result:
        return pathlib.Path(result[0])
    raise TypeError('fetch did not return a local path for ' + url)


def _record(item):
    if isinstance(item, dict):
        inner = item.get('data')
        if isinstance(inner, dict):
            return inner
        return item
    return {}


def _source_error(context, member, headers, missing, extra=None):
    message = f'{context}; selected archive member {member}; observed headers {headers}; missing names {missing}'
    if extra:
        message += f'; {extra}'
    return message[:4000]


def _api_pages_once(base_url, filters, cache, prefix, fields, keep_fields, page_size=10000, max_pages=1000):
    offset = 0
    total = None
    records = []
    headers = []
    members = []
    pages = 0
    member = prefix
    while True:
        params = {'limit': page_size, 'offset': offset}
        if filters is not None:
            params['filters'] = filters
        if fields is not None:
            params['fields'] = ','.join(fields)
        url = base_url + '?' + urllib.parse.urlencode(params)
        name = prefix + '_offset_' + str(offset) + '.json'
        path = _fetch_local(url, cache, name)
        member = path.name
        members.append(member)
        with open(path, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            data = payload
            meta = {}
        elif isinstance(payload, dict):
            data = payload.get('data')
            meta = payload.get('meta') or {}
        else:
            raise AssertionError(_source_error(prefix + ' returned an unexpected JSON document', member, headers, keep_fields))
        if data is None:
            keys = list(payload.keys()) if isinstance(payload, dict) else []
            raise AssertionError(_source_error(prefix + ' response has no data list', member, keys, keep_fields))
        if not isinstance(data, list):
            raise AssertionError(_source_error(prefix + ' response data is not a list', member, headers, keep_fields))
        if not headers and data:
            headers = sorted(_record(data[0]).keys())
        page_total = meta.get('total') if isinstance(meta, dict) else None
        if total is None and page_total is not None:
            try:
                total = int(page_total)
            except Exception:
                total = None
        for item in data:
            record = _record(item)
            records.append({field: record.get(field) for field in keep_fields})
        pages += 1
        offset += len(data)
        if total is not None and offset >= total:
            break
        if not data:
            break
        if total is None and len(data) < page_size:
            break
        if pages >= max_pages:
            raise AssertionError(_source_error(prefix + ' pagination exceeded the page guard', member, headers, keep_fields, 'pages ' + str(pages) + '; fetched ' + str(len(records)) + '; total ' + str(total)))
    if total is None:
        total = len(records)
    if len(records) != total:
        raise AssertionError(_source_error(prefix + ' pagination did not reach the API total', member, headers, keep_fields, 'fetched ' + str(len(records)) + ' of ' + str(total)))
    frame = pd.DataFrame(records, columns=keep_fields)
    return frame, {'total': int(total), 'headers': headers, 'pages': pages, 'members': members, 'member': member}


def _api_pages(base_url, filters, cache, prefix, fields, keep_fields, page_size=10000, max_pages=1000):
    attempts = []
    if fields is not None:
        attempts.append((fields, prefix))
        attempts.append((None, prefix + '_allfields'))
    else:
        attempts.append((None, prefix))
    errors = []
    for attempt_index, (field_list, attempt_prefix) in enumerate(attempts):
        try:
            frame, meta = _api_pages_once(base_url, filters, cache, attempt_prefix, field_list, keep_fields, page_size, max_pages)
            meta['field_fallback'] = bool(fields is not None and field_list is None)
            return frame, meta
        except Exception as exc:
            errors.append('attempt ' + str(attempt_index) + ' with fields ' + str(field_list) + ': ' + str(exc)[:1500])
    raise AssertionError(_source_error(prefix + ' could not be downloaded', prefix, [], keep_fields, ' | '.join(errors)))


def _write_csv(frame, path):
    ordered = frame[COLUMNS].copy()
    ordered = ordered.sort_values([TIME_COLUMN, ID_COLUMN], kind='mergesort').reset_index(drop=True)
    ordered.to_csv(path, index=False, lineterminator=chr(10))


def build(output: pathlib.Path, cache: pathlib.Path):
    output = pathlib.Path(output)
    cache = pathlib.Path(cache)
    (output / 'public').mkdir(parents=True, exist_ok=True)
    (output / 'private').mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    checks = []

    def add_check(name, observed, expected, passed, kind=None):
        item = {'check': name, 'observed': observed, 'expected': expected, 'passed': bool(passed)}
        if kind is not None:
            item['kind'] = kind
        checks.append(item)
        if not passed:
            raise AssertionError(f'{name}; observed {observed!r}; expected {expected}')

    history_raw, history_meta = _api_pages(HISTORY_ENDPOINT, HISTORY_FILTER, cache, 'history_721', HISTORY_FIELDS, HISTORY_FIELDS, page_size=10000, max_pages=300)
    history_headers = history_meta['headers']
    history_member = history_meta['member']
    history_missing = [field for field in HISTORY_FIELDS if field not in history_headers]
    if history_missing:
        fallback_flag = history_meta.get('field_fallback')
        raise AssertionError(_source_error('History required field names are missing', history_member, history_headers, history_missing, 'field fallback ' + str(fallback_flag)))
    if len(history_raw) == 0:
        raise AssertionError(_source_error('History query returned no records', history_member, history_headers, HISTORY_FIELDS))
    add_check('History pagination reached the API total', {'rows_fetched': len(history_raw), 'meta_total': history_meta['total'], 'field_fallback': history_meta['field_fallback']}, 'fetched rows equal meta.total', len(history_raw) == history_meta['total'])

    history = history_raw.copy()
    history['UNINUM'] = history['UNINUM'].map(_norm_id)
    missing_uninum = int(history['UNINUM'].isna().sum())
    if missing_uninum:
        raise AssertionError(_source_error('History branch identifier has missing values', history_member, history_headers, ['UNINUM'], 'missing value count ' + str(missing_uninum)))
    history['CHANGECODE'] = history['CHANGECODE'].map(_norm_id)
    other_code_rows = int((history['CHANGECODE'] != '721').sum())
    history = history.loc[history['CHANGECODE'] == '721'].copy()
    if history.empty:
        raise AssertionError(_source_error('History query contains no branch closing code records', history_member, history_headers, ['CHANGECODE'], 'other code rows ' + str(other_code_rows)))
    add_check('History query contains branch closing code records', {'returned_rows': len(history_raw), 'code_721_rows': len(history), 'other_code_rows_excluded': other_code_rows}, 'at least one 721 record after excluding other returned codes', len(history) > 0)

    descriptions = history['CHANGECODE_DESC'].map(lambda value: '' if _is_missing(value) else str(value).strip().lower())
    bad_description_count = int((descriptions != 'branch closing').sum())
    if bad_description_count:
        observed_descriptions = sorted(set(descriptions.tolist()))[:20]
        raise AssertionError(_source_error('History branch closing description is unexpected', history_member, history_headers, ['CHANGECODE_DESC'], 'unexpected rows ' + str(bad_description_count) + '; values ' + str(observed_descriptions)))
    add_check('History 721 records carry the Branch Closing description', {'records': len(history), 'unexpected_description_count': bad_description_count}, 'all selected 721 records are labelled Branch Closing', bad_description_count == 0)

    roles = history['ORG_ROLE_CDE'].map(lambda value: None if _is_missing(value) else str(value).strip().upper())
    missing_role_count = int(roles.isna().sum())
    if missing_role_count:
        raise AssertionError(_source_error('History organization role has missing values', history_member, history_headers, ['ORG_ROLE_CDE'], 'missing value count ' + str(missing_role_count)))
    branch_role = roles == 'BR'
    branch_events = history.loc[branch_role].copy()
    non_branch_721 = int((~branch_role).sum())
    if len(branch_events) == 0:
        observed_roles = sorted(set(roles.dropna().tolist()))[:20]
        raise AssertionError(_source_error('History has no branch role 721 events', history_member, history_headers, ['ORG_ROLE_CDE'], 'non branch role events ' + str(non_branch_721) + '; observed roles ' + str(observed_roles)))
    add_check('History 721 events include branch role records', {'branch_role_events': len(branch_events), 'non_branch_721': non_branch_721}, 'at least one branch role event and no missing role values', len(branch_events) > 0)

    effective = pd.to_datetime(branch_events['EFFDATE'], errors='coerce', utc=True)
    bad_effective = int(effective.isna().sum())
    if bad_effective:
        raise AssertionError(_source_error('History effective dates have unparseable values', history_member, history_headers, ['EFFDATE'], 'unparseable count ' + str(bad_effective)))
    branch_events['effdate'] = effective.dt.tz_convert(None)
    events = branch_events[['UNINUM', 'effdate']].drop_duplicates().copy()
    add_check('History effective dates parse for every branch role event', {'events': len(events), 'min_effective': events['effdate'].min().date().isoformat(), 'max_effective': events['effdate'].max().date().isoformat()}, 'all effective dates parse and at least one event exists', len(events) > 0)

    sod_frames = []
    sod_totals = {}
    sod_fetched = {}
    sod_headers = {}
    sod_fallbacks = {}
    sod_members = []
    cb_coverage = {}
    selection_counts = {}
    for year in YEARS:
        raw, meta = _api_pages(SOD_ENDPOINT, 'YEAR:' + str(year), cache, 'sod_' + str(year), SOD_FIELDS, SOD_FIELDS, page_size=10000, max_pages=100)
        sod_headers[str(year)] = meta['headers']
        sod_totals[str(year)] = int(meta['total'])
        sod_fetched[str(year)] = int(len(raw))
        sod_fallbacks[str(year)] = bool(meta.get('field_fallback'))
        sod_members.append(meta['member'])
        missing = [field for field in SOD_REQUIRED if field not in meta['headers']]
        if missing:
            fallback_flag = meta.get('field_fallback')
            raise AssertionError(_source_error('SOD required field names are missing for a survey year', meta['member'], meta['headers'], missing, 'survey year ' + str(year) + '; field fallback ' + str(fallback_flag)))
        frame = raw.copy()
        for field in SOD_FIELDS:
            if field not in frame.columns:
                frame[field] = None
        year_values = pd.to_numeric(frame['YEAR'], errors='coerce')
        if not (year_values == year).all():
            unexpected_years = sorted(set(int(value) for value in year_values.dropna().unique()))[:20]
            raise AssertionError(_source_error('SOD response contains rows outside the requested survey year', meta['member'], meta['headers'], ['YEAR'], 'requested year ' + str(year) + '; observed years ' + str(unexpected_years)))
        frame['CERT'] = frame['CERT'].map(_norm_id)
        frame['UNINUMBR'] = frame['UNINUMBR'].map(_norm_id)
        cb_coverage[str(year)] = int(_flag(frame['CB']).notna().sum())
        frame['_bkmo'] = _flag(frame['BKMO'])
        frame['_brnum'] = _number(frame['BRNUM'])
        frame['_usa'] = _flag(frame['USA'])
        frame['_uninum_num'] = pd.to_numeric(frame['UNINUMBR'], errors='coerce')
        frame['_deposit'] = _number(frame['DEPSUMBR'])
        frame['ASSET'] = _number(frame['ASSET'])
        frame['METROBR'] = _numeric_or_flag(frame['METROBR'])
        frame['MICROBR'] = _numeric_or_flag(frame['MICROBR'])
        frame['BRSERTYP'] = frame['BRSERTYP'].map(_norm_id)
        frame['BKCLASS'] = frame['BKCLASS'].map(_norm_id)
        frame['BRCENM'] = frame['BRCENM'].map(_norm_id)
        frame['STALPBR'] = _text_upper(frame['STALPBR'])
        frame['SIMS_ESTABLISHED_DATE'] = _date_string(frame['SIMS_ESTABLISHED_DATE'])
        valid_cert = frame['CERT'].notna()
        grouped = frame.loc[valid_cert].groupby('CERT', dropna=True)
        bank_counts = grouped.size()
        bank_deposits = grouped['_deposit'].sum(min_count=1)
        frame['branch_count_bank'] = frame['CERT'].map(bank_counts).astype('Int64')
        frame['_bank_dep_sum'] = frame['CERT'].map(bank_deposits)
        frame['branch_deposit_share'] = frame['_deposit'] / frame['_bank_dep_sum'].where(frame['_bank_dep_sum'] > 0)
        frame['DEPSUMBR'] = frame['_deposit']
        raw_rows = len(frame)
        main_office_count = int((frame['_bkmo'] == 1).sum())
        bad_brnum_count = int((~(frame['_brnum'] > 0)).sum())
        bad_uninum_count = int((~(frame['_uninum_num'] > 0)).sum())
        not_usa_count = int((frame['_usa'] != 1).sum())
        bad_state_count = int((~frame['STALPBR'].isin(VALID_STATES)).sum())
        mask = (frame['_bkmo'] == 0) & (frame['_brnum'] > 0) & (frame['_uninum_num'] > 0) & (frame['_usa'] == 1) & frame['STALPBR'].isin(VALID_STATES)
        frame = frame.loc[mask].copy()
        selected_before_dedupe = len(frame)
        if selected_before_dedupe == 0:
            raise AssertionError(_source_error('SOD survey year has no rows after the frozen population filters', meta['member'], meta['headers'], ['BKMO', 'BRNUM', 'UNINUMBR', 'USA', 'STALPBR'], 'survey year ' + str(year)))
        before_dedupe = len(frame)
        frame = frame.sort_values(['UNINUMBR', 'CERT'], kind='mergesort', na_position='last').drop_duplicates('UNINUMBR', keep='first')
        duplicate_count = before_dedupe - len(frame)
        snapshot = pd.Timestamp(year=year, month=6, day=30)
        prior_closed = set(events.loc[events['effdate'] <= snapshot, 'UNINUM'].dropna())
        future_end = snapshot + pd.DateOffset(years=1)
        future_closed = set(events.loc[(events['effdate'] > snapshot) & (events['effdate'] <= future_end), 'UNINUM'].dropna())
        prior_mask = frame['UNINUMBR'].isin(prior_closed)
        prior_excluded = int(prior_mask.sum())
        frame = frame.loc[~prior_mask].copy()
        if len(frame) == 0:
            raise AssertionError(_source_error('SOD survey year retained no eligible branches', meta['member'], meta['headers'], ['UNINUMBR'], 'survey year ' + str(year) + '; prior closures excluded ' + str(prior_excluded)))
        frame[TARGET] = frame['UNINUMBR'].isin(future_closed).astype(int)
        frame['branch_id'] = frame['UNINUMBR']
        frame['cert'] = frame['CERT']
        frame['survey_year'] = int(year)
        frame['snapshot_date'] = snapshot.strftime('%Y-%m-%d')
        frame['branch_year_id'] = frame['UNINUMBR'].astype(str) + '_' + str(year)
        selection_counts[str(year)] = {
            'raw_rows': int(raw_rows),
            'main_office_rows': main_office_count,
            'bad_brnum_rows': bad_brnum_count,
            'bad_uninum_rows': bad_uninum_count,
            'not_usa_rows': not_usa_count,
            'invalid_state_rows': bad_state_count,
            'after_population_filters': int(selected_before_dedupe),
            'duplicate_uninumbr_removed': int(duplicate_count),
            'prior_721_excluded': prior_excluded,
            'future_721_positive_rows': int(frame[TARGET].sum()),
            'final_rows': int(len(frame)),
        }
        sod_frames.append(frame[COLUMNS])

    headers_union = sorted(set().union(*[set(values) for values in sod_headers.values()])) if sod_headers else []
    cb_total = int(sum(cb_coverage.values()))
    add_check('Community bank flag has no nonmissing publisher values in the selected SOD vintages and is therefore documented rather than shipped as an empty declared column', {'requested_field': 'CB', 'nonmissing_by_year': cb_coverage, 'sod_headers_2022': sod_headers.get('2022')}, 'the SOD branch record carries no nonmissing community bank flag for survey years 2012 through 2022, so the feature is omitted with recorded evidence', cb_total == 0)

    add_check('SOD required field names are present for every survey year', sod_headers and {key: [field for field in SOD_REQUIRED if field not in value] for key, value in sod_headers.items()}, 'every declared SOD feature field name appears in the returned record headers for all selected years', all(not [field for field in SOD_REQUIRED if field not in value] for value in sod_headers.values()))
    add_check('SOD annual row counts reached API meta totals', {'meta_total': sod_totals, 'rows_fetched': sod_fetched, 'field_fallback_by_year': sod_fallbacks}, 'fetched rows equal meta.total and totals are positive', all(sod_totals[key] > 0 and sod_totals[key] == sod_fetched[key] for key in sod_totals))

    all_df = pd.concat(sod_frames, ignore_index=True)
    if list(all_df.columns) != COLUMNS:
        missing_columns = [column for column in COLUMNS if column not in all_df.columns]
        raise AssertionError(_source_error('Constructed columns do not match the declared schema', sod_members[-1] if sod_members else 'SOD pages', list(all_df.columns), missing_columns))

    coverage = {feature: int(all_df[feature].notna().sum()) for feature in FEATURES}
    missing_feature_names = [feature for feature in FEATURES if coverage[feature] == 0]
    if missing_feature_names:
        raise AssertionError(_source_error('Declared feature columns are entirely missing after construction', sod_members[-1] if sod_members else 'SOD pages', headers_union, missing_feature_names, 'coverage ' + str(coverage)))
    add_check('Every declared feature has at least one nonmissing value', coverage, 'each shipped feature has positive nonmissing coverage after parsing', not missing_feature_names)

    branch_ids = set(all_df['branch_id'].dropna())
    event_ids = set(events['UNINUM'].dropna())
    matched_branch_events = len(branch_ids.intersection(event_ids))
    if matched_branch_events == 0:
        raise AssertionError(_source_error('History to SOD branch join found no matches', history_member, history_headers, ['UNINUM', 'UNINUMBR'], 'distinct SOD branches ' + str(len(branch_ids)) + '; distinct history branches ' + str(len(event_ids))))
    add_check('History to SOD branch join matches branch identifiers', {'distinct_sod_branches': len(branch_ids), 'distinct_history_branches': len(event_ids), 'matched': matched_branch_events}, 'at least one branch identifier matches an SOD branch', matched_branch_events > 0)

    add_check('Population filters were applied and rows were retained', selection_counts, 'main offices, invalid identifiers, non USA rows, invalid states, and prior closures are counted and excluded without aborting only because they exist in the source', sum(value['final_rows'] for value in selection_counts.values()) == len(all_df))

    prior_violations = 0
    for year in YEARS:
        snapshot = pd.Timestamp(year=year, month=6, day=30)
        prior_closed = set(events.loc[events['effdate'] <= snapshot, 'UNINUM'].dropna())
        subset = all_df.loc[all_df['survey_year'] == year]
        prior_violations += int(subset['branch_id'].isin(prior_closed).sum())
    add_check('Constructed population contains no branch with a prior 721 closure', {'violations': prior_violations, 'prior_excluded': {key: value['prior_721_excluded'] for key, value in selection_counts.items()}}, 'zero retained rows with a 721 event effective on or before the survey date', prior_violations == 0)

    invalid_state_output = int((~all_df['STALPBR'].isin(VALID_STATES)).sum())
    add_check('Branch state filter retains only recognized state or territory codes', {'invalid_rows_in_output': invalid_state_output, 'excluded_invalid_source_rows': sum(value['invalid_state_rows'] for value in selection_counts.values())}, 'zero invalid state codes in output while invalid source rows are counted and excluded', invalid_state_output == 0, kind='unit')

    duplicate_ids = int(all_df[ID_COLUMN].duplicated().sum())
    add_check('Branch year identifiers are unique', {'duplicate_count': duplicate_ids, 'rows': len(all_df)}, 'zero duplicate branch year identifiers', duplicate_ids == 0)
    required_carry_missing = {column: int(all_df[column].isna().sum()) for column in [ID_COLUMN, TIME_COLUMN, 'branch_id', 'survey_year']}
    add_check('Required identifiers and the event date are complete', required_carry_missing, 'zero missing values for the identifier, time, branch identifier, and survey year columns', all(value == 0 for value in required_carry_missing.values()))

    target_values = set(int(value) for value in all_df[TARGET].dropna().unique())
    target_missing = int(all_df[TARGET].isna().sum())
    add_check('Target is binary and complete', {'values': sorted(target_values), 'missing': target_missing, 'positives': int(all_df[TARGET].sum())}, 'values are a subset of 0 and 1 with no missing labels', target_missing == 0 and target_values.issubset({0, 1}))

    snapshot_dates = pd.to_datetime(all_df[TIME_COLUMN], errors='coerce')
    bad_snapshot_dates = int(snapshot_dates.isna().sum() + ((snapshot_dates.dt.month != 6) | (snapshot_dates.dt.day != 30)).sum())
    add_check('Snapshot dates are annual June 30 survey dates', {'unique_dates': sorted(all_df[TIME_COLUMN].dropna().unique().tolist()), 'bad_date_count': bad_snapshot_dates}, 'every snapshot date is June 30 of its survey year', bad_snapshot_dates == 0 and len(all_df[TIME_COLUMN].dropna().unique()) == len(YEARS), kind='unit')

    train_mask = snapshot_dates < pd.Timestamp('2019-01-01')
    eval_mask = (snapshot_dates >= pd.Timestamp('2019-01-01')) & (snapshot_dates < pd.Timestamp('2021-01-01'))
    holdout_mask = snapshot_dates >= pd.Timestamp('2021-01-01')
    split_coverage = train_mask | eval_mask | holdout_mask
    split_counts = {'train': int(train_mask.sum()), 'eval': int(eval_mask.sum()), 'holdout': int(holdout_mask.sum())}
    add_check('Half open chronological split windows contain every row', split_counts, 'all rows fall in fixed train 2012 through 2018, eval 2019 through 2020, and holdout 2021 through 2022 windows', split_coverage.all() and all(value > 0 for value in split_counts.values()))

    latest_snapshot = snapshot_dates.max()
    if pd.isna(latest_snapshot):
        raise AssertionError(_source_error('Snapshot dates could not be parsed for the history coverage check', sod_members[-1] if sod_members else 'SOD pages', headers_union, [TIME_COLUMN]))
    latest_label_end = latest_snapshot + pd.DateOffset(years=1)
    add_check('History coverage reaches the end of every label window', {'max_snapshot_date': latest_snapshot.strftime('%Y-%m-%d'), 'history_query_end': '2023-06-30'}, 'latest label window ends on or before the history query end', latest_label_end <= pd.Timestamp('2023-06-30'))

    positive_counts = {'train': int(all_df.loc[train_mask, TARGET].sum()), 'eval': int(all_df.loc[eval_mask, TARGET].sum()), 'holdout': int(all_df.loc[holdout_mask, TARGET].sum())}
    add_check('Each chronological split contains positive closure events', positive_counts, 'positive event counts greater than zero in train, eval, and holdout', all(value > 0 for value in positive_counts.values()))

    deposits = pd.to_numeric(all_df['DEPSUMBR'], errors='coerce')
    add_check('Branch deposit unit and frame check', {'negative': int((deposits < 0).sum()), 'missing': int(deposits.isna().sum()), 'min': None if deposits.dropna().empty else float(deposits.min()), 'max': None if deposits.dropna().empty else float(deposits.max())}, 'nonnegative thousands of USD or missing', int((deposits < 0).sum()) == 0, kind='unit')
    assets = pd.to_numeric(all_df['ASSET'], errors='coerce')
    add_check('Institution asset unit and frame check', {'negative': int((assets < 0).sum()), 'missing': int(assets.isna().sum()), 'max': None if assets.dropna().empty else float(assets.max())}, 'nonnegative thousands of USD or missing', int((assets < 0).sum()) == 0, kind='unit')
    shares = pd.to_numeric(all_df['branch_deposit_share'], errors='coerce')
    add_check('Branch deposit share is bounded', {'outside_unit_interval': int(((shares < 0) | (shares > 1)).sum()), 'missing': int(shares.isna().sum())}, 'nonmissing shares fall within zero and one inclusive', int(((shares < 0) | (shares > 1)).sum()) == 0, kind='unit')
    counts = pd.to_numeric(all_df['branch_count_bank'], errors='coerce')
    add_check('Bank branch count is at least one', {'below_one': int((counts < 1).sum()), 'missing': int(counts.isna().sum())}, 'nonmissing branch counts are at least one', int((counts < 1).sum()) == 0, kind='unit')

    missing_deposit_share = int(all_df.loc[all_df['DEPSUMBR'].isna(), 'branch_deposit_share'].notna().sum())
    add_check('Missing deposits stay missing rather than becoming zero', {'rows_with_missing_deposit_but_nonmissing_share': missing_deposit_share, 'missing_deposit_rows': int(all_df['DEPSUMBR'].isna().sum())}, 'rows with missing branch deposits also have a missing deposit share', missing_deposit_share == 0)

    _write_csv(all_df.loc[train_mask], output / 'public' / 'train.csv')
    _write_csv(all_df.loc[eval_mask], output / 'public' / 'eval.csv')
    _write_csv(all_df.loc[holdout_mask], output / 'private' / 'holdout.csv')
    header = (output / 'public' / 'train.csv').read_text(encoding='utf-8').splitlines()[0].split(',')
    add_check('Written CSV header matches the declared schema', {'header': header}, 'exact declared ID, time, carry, feature, and target names', header == COLUMNS)
    return checks
