"""Derive the analyst's view of a component from the captured evidence.

The sign-off page used to repeat the agent's prose: a DAX definition, terse
screenshot captions and claim text written for an engineer. An analyst checks a
report differently - look at what the screen showed in each situation, compare a
figure with the same figure somewhere else, change one filter and see whether the
numbers move the way they should, put everything back and check it lands where it
started. These functions reconstruct exactly that from the observations
(`state.json`), the run journal (which action preceded which capture) and the
plan (which figures matter and which pairs must agree), so the renderer only has
to lay it out and the derivation stays unit-testable.

Nothing here may raise on an old sealed case. Fields the derivation needs may be
missing (no `plan.consistency`, no claim `question`, no component
`what_it_shows`); a check that cannot be derived is omitted, never guessed.

  component_observations(component, case_dir) -> one record per captured situation
  situation_labels(journal, plan)             -> capture label -> plain situation label
  automatic_checks(observations, plan, journal) -> computed pass/fail checks
  questions(component, claims, checks, catalog) -> the decisions asked of the analyst
  phrase_action(step)                         -> "Google Ads unchecked in Channel"
  format_number(value)                        -> "1,978"
  is_technical(text)                          -> the first engineer-only token, or None
"""
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent

MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
          'July', 'August', 'September', 'October', 'November', 'December']
DASH = '–'   # en dash, for ranges
MINUS = '−'  # true minus, so a negative delta does not read as a hyphen

# --- language filter -------------------------------------------------------

# DAX functions an analyst should never have to read on a sign-off page. The
# generic rule below catches the long ones; these are the short names ("SUM(",
# "ALL(") that a length rule would miss.
DAX_FUNCTIONS = (
    'COUNTROWS COUNT COUNTA COUNTAX COUNTBLANK CALCULATE CALCULATETABLE DIVIDE SUM SUMX AVERAGE AVERAGEX '
    'MIN MINX MAX MAXX MEDIAN MEDIANX PERCENTILE.INC PERCENTILE.EXC FILTER ALL ALLEXCEPT ALLSELECTED '
    'REMOVEFILTERS KEEPFILTERS VALUES DISTINCT DISTINCTCOUNT RELATED RELATEDTABLE USERELATIONSHIP EVALUATE '
    'ROW SUMMARIZE SUMMARIZECOLUMNS ADDCOLUMNS SELECTCOLUMNS TODAY NOW DATE DATEADD DATESMTD DATESYTD '
    'DATESQTD TOTALYTD TOTALMTD TOTALQTD SAMEPERIODLASTYEAR IF SWITCH BLANK ISBLANK VAR RETURN EARLIER '
    'RANKX TOPN UNION EXCEPT INTERSECT CONCATENATEX FORMAT LOOKUPVALUE HASONEVALUE SELECTEDVALUE '
    'ISFILTERED ISCROSSFILTERED'
).split()

TECHNICAL_PATTERNS = [
    # A named DAX function, with or without its opening parenthesis.
    re.compile(r'\b(' + '|'.join(re.escape(name) for name in DAX_FUNCTIONS) + r')\b(?=\s*\(|\s*$|[\s,.;:)])'),
    # Any other SCREAMING identifier used as a call: TMSCHEMA(, PERCENTILEX(...
    re.compile(r'\b([A-Z][A-Z0-9_.]{3,})\s*\('),
    # 'Table'[Column] and bare [Measure] references.
    re.compile(r"('[^']+'\s*\[[^\]]+\])"),
    re.compile(r'(\[[A-Za-z_][^\]]*\])'),
    # Warehouse and model identifiers.
    re.compile(r'\b((?:fact|dim|stg|int)_\w+)'),
    re.compile(r'\b([A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+)\b'),
    # Vocabulary that belongs to the tooling, not to the report.
    re.compile(r'\b(DAX|TMSCHEMA|DOM)\b'),
    re.compile(r'\b(oracle|oracles|receipt|receipts|pointer|pointers|JSON pointer|M query)\b', re.IGNORECASE),
    re.compile(r'(BLANK\(\)|role=|aria-[a-z]+)'),
    # File paths and file names.
    re.compile(r'([A-Za-z]:[\\/][^\s]*)'),
    re.compile(r'(\S+\.(?:json|py|html|png|csv|sql|pbix|pbip|bim|tmdl|yml))\b', re.IGNORECASE),
    re.compile(r'\b((?:evidence|scripts|plugin|cases|procedures)[\\/][^\s]*)'),
]


def is_technical(text):
    """The first token that makes this sentence unreadable for an analyst, or None.

    Applied only to the fields written FOR the analyst (`what_it_shows`, a claim's
    `question`). `definition`, `expected`, `observed` and `source` stay technical
    on purpose and are never passed through here.
    """
    if not text:
        return None
    found = None
    for pattern in TECHNICAL_PATTERNS:
        match = pattern.search(str(text))
        if match and (found is None or match.start() < found[0]):
            found = (match.start(), match.group(1) if match.groups() else match.group(0))
    return found[1] if found else None


# --- formatting ------------------------------------------------------------

def format_number(value):
    """Thousands separators, integers without decimals, text passed through."""
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            return f'{int(value):,}'
        return f'{round(float(value), 2):,}'.rstrip('0').rstrip('.')
    return str(value)


def pretty_key(key):
    """'leads_total' -> 'Leads total'; 'meta_table' -> 'Meta'."""
    text = str(key).strip()
    if text.lower().endswith('_table'):
        text = text[:-6]
    text = re.sub(r'[_\-]+', ' ', text).strip()
    return text[:1].upper() + text[1:] if text else str(key)


def figure_name(kind, key):
    if kind == 'card':
        return str(key)
    base = pretty_key(key)
    return base if 'total' in base.lower() else base + ' total'


def parse_date(text):
    """'7/13/2026' -> (2026, 7, 13); None when the shape is unknown."""
    match = re.match(r'^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$', str(text or ''))
    if not match:
        return None
    month, day, year = (int(g) for g in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return (year, month, day)


def pretty_date(text):
    parsed = parse_date(text)
    if not parsed:
        return str(text or '')
    year, month, day = parsed
    return f'{MONTHS[month - 1]} {day}, {year}'


def pretty_date_range(start, end):
    """'July 1-13' when one month, 'July 1 - August 3' across months, raw otherwise."""
    first, last = parse_date(start), parse_date(end)
    if not first or not last:
        both = [str(x) for x in (start, end) if x]
        return ' to '.join(both)
    if first == last:
        return f'{MONTHS[first[1] - 1]} {first[2]}'
    if first[0] == last[0] and first[1] == last[1]:
        return f'{MONTHS[first[1] - 1]} {first[2]}{DASH}{last[2]}'
    if first[0] == last[0]:
        return f'{MONTHS[first[1] - 1]} {first[2]} {DASH} {MONTHS[last[1] - 1]} {last[2]}'
    return f'{pretty_date(start)} {DASH} {pretty_date(end)}'


def range_relation(before, after):
    """'narrowed', 'widened', 'moved' or None for two (start, end) date pairs."""
    old = [parse_date(before[0]), parse_date(before[1])]
    new = [parse_date(after[0]), parse_date(after[1])]
    if not all(old) or not all(new):
        return None
    if old == new:
        return 'same'
    if new[0] >= old[0] and new[1] <= old[1]:
        return 'narrowed'
    if new[0] <= old[0] and new[1] >= old[1]:
        return 'widened'
    return 'moved'


# --- actions ---------------------------------------------------------------

ACTION_KEYS = ('toggle_member', 'set_date', 'click_mark', 'select_option', 'go_to_page')


def phrase_action(step):
    """One plain sentence fragment for a plan action step, or None when unknown."""
    if not isinstance(step, dict):
        return None
    if 'toggle_member' in step:
        detail = step['toggle_member'] or {}
        member, slicer = detail.get('label', 'a member'), detail.get('slicer', 'the slicer')
        verb = 're-checked' if detail.get('selected') else 'unchecked'
        return f'{member} {verb} in {slicer}'
    if 'set_date' in step:
        detail = step['set_date'] or {}
        start, end = detail.get('start'), detail.get('end')
        if start and end:
            return f'Dates changed to {pretty_date_range(start, end)}'
        if end:
            return f'End date changed to {pretty_date_range(end, end)}'
        if start:
            return f'Start date changed to {pretty_date_range(start, start)}'
        return 'Dates changed'
    if 'select_option' in step:
        detail = step['select_option'] or {}
        slicer, option = str(detail.get('slicer') or '').strip(), detail.get('label', 'another option')
        # Some toggle slicers render with a generic title ("All"); naming it reads worse than not.
        if not slicer or slicer.lower() in ('all', 'slicer'):
            return f'Switched to {option}'
        return f'{slicer} set to {option}'
    if 'click_mark' in step:
        detail = step['click_mark'] or {}
        return f"Clicked '{detail.get('category', 'a bar')}' in {detail.get('chart', 'the chart')}"
    if 'go_to_page' in step:
        return f"Moved to page {step['go_to_page']}"
    return None


def plan_of(journal, plan=None):
    """The plan that drove this run: the saved plan.json, else the journal's copy."""
    if isinstance(plan, dict) and plan:
        return plan
    if isinstance(journal, dict) and isinstance(journal.get('plan'), dict):
        return journal['plan']
    return {}


def capture_actions(journal, plan=None):
    """Capture label -> the action steps performed since the previous capture."""
    steps = (plan_of(journal, plan) or {}).get('steps') or []
    pending, result = [], {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        if 'capture' in step:
            result[str(step['capture'])] = {'actions': pending, 'description': step.get('description', '')}
            pending = []
        elif any(key in step for key in ACTION_KEYS):
            pending = pending + [step]
    return result


RESET_WORDS = ('restore', 'restored', 'reset', 'return', 'cleared', 'back to')


def join_phrases(phrases):
    phrases = [p for p in phrases if p]
    if len(phrases) < 3:
        return ' and '.join(phrases)
    return ', '.join(phrases[:-1]) + ' and ' + phrases[-1]


def restored_captures(journal, plan=None):
    """Capture label -> is every control touched since the baseline back where it started?

    Matching filter captions are not enough: a saved multi-selection still reads
    "Multiple selections" after one member is unchecked, and a cross-filtered
    chart leaves no slicer trace at all. So the plan's own steps are replayed: a
    member is back when its selection equals the value it had before the first
    toggle, a tile slicer and a clicked mark are back after an even number of
    steps on the same control. Returns None when the plan's step order is
    unavailable, so the caller can fall back to comparing the captured context.
    """
    steps = (plan_of(journal, plan) or {}).get('steps') or []
    if not steps:
        return None
    members, options, marks, started, result = {}, {}, {}, False, {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        if 'capture' in step:
            if not started:
                # Actions before the first capture set the baseline up; they are not changes from it.
                started, members, options, marks = True, {}, {}, {}
                result[str(step['capture'])] = True
                continue
            result[str(step['capture'])] = (all(initial == current for initial, current in members.values())
                                            and all(count % 2 == 0 for count in options.values())
                                            and all(count % 2 == 0 for count in marks.values()))
        elif not started:
            continue
        elif 'toggle_member' in step:
            detail = step['toggle_member'] or {}
            key, selected = (detail.get('slicer'), detail.get('label')), bool(detail.get('selected'))
            members[key] = [members[key][0], selected] if key in members else [not selected, selected]
        elif 'select_option' in step:
            key = (step['select_option'] or {}).get('slicer')
            options[key] = options.get(key, 0) + 1
        elif 'click_mark' in step:
            detail = step['click_mark'] or {}
            key = (detail.get('chart'), detail.get('category'))
            marks[key] = marks.get(key, 0) + 1
    return result


def situation_labels(journal, plan=None):
    """Capture label -> the plain situation label shown to the analyst.

    The first capture is the Baseline; every later one is named after the action
    that produced it ("Google Ads unchecked in Channel"). When the plan's step
    order is unavailable the labels fall back to the capture's own description.
    """
    states = [s.get('label') for s in (journal or {}).get('states') or [] if isinstance(s, dict)]
    actions = capture_actions(journal, plan)
    order = states or list(actions)
    labels, clicks = {}, {}
    for index, label in enumerate(order):
        if label is None:
            continue
        entry = actions.get(label, {})
        phrases = []
        for step in entry.get('actions') or []:
            phrase = phrase_action(step)
            if 'click_mark' in step:
                detail = step['click_mark'] or {}
                key = (detail.get('chart'), detail.get('category'))
                clicks[key] = clicks.get(key, 0) + 1
                if clicks[key] % 2 == 0:
                    phrase = (f"Cleared the '{detail.get('category', 'bar')}' selection in "
                              f"{detail.get('chart', 'the chart')}")
            if phrase:
                phrases.append(phrase)
        if index == 0:
            labels[label] = 'Baseline'
            continue
        if phrases:
            labels[label] = join_phrases(phrases)
            continue
        description = str(entry.get('description') or '').strip()
        if not description:
            record = next((s for s in (journal or {}).get('states') or [] if s.get('label') == label), {})
            description = str(record.get('description') or '').strip()
        if description:
            labels[label] = description.split('.')[0].strip() or f'Situation {index + 1}'
        elif any(word in str(label).lower() for word in RESET_WORDS):
            labels[label] = 'Back to the baseline filters'
        else:
            labels[label] = f'Situation {index + 1}'
    return labels


# --- observations ----------------------------------------------------------

def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return None


def component_journal(component, case_dir):
    """(journal, plan) for a component's run; empty dicts when the run is absent."""
    case_dir = Path(case_dir)
    reference = (component or {}).get('run')
    if not reference:
        return {}, {}
    run_dir = (case_dir / reference).parent
    journal = read_json(case_dir / reference) or {}
    plan = read_json(run_dir / 'plan.json')
    return journal, plan_of(journal, plan if isinstance(plan, dict) else None)


def observation_figures(state):
    """Every figure this capture recorded, cards first then table totals, in plan order."""
    figures = []
    texts = state.get('cards_text') or {}
    for title, value in (state.get('cards') or {}).items():
        text = texts.get(title)
        figures.append({'key': 'card:' + str(title), 'name': figure_name('card', title), 'kind': 'card',
                        'value': value, 'text': text,
                        'display': format_number(value) if value is not None else (text or '')})
    for key, value in (state.get('tables') or {}).items():
        figures.append({'key': 'table:' + str(key), 'name': figure_name('table', key), 'kind': 'table',
                        'value': value, 'text': None,
                        'display': format_number(value) if value is not None else ''})
    return figures


def component_observations(component, case_dir):
    """One record per captured situation, read from the sealed observations.

    Each record carries the dates, the slicer captions, every figure the capture
    recorded (with the text a card showed when its number could not be parsed),
    the declared consistency results when the run wrote them, and whether the
    capture's own receipt passed.
    """
    case_dir = Path(case_dir)
    journal, plan = component_journal(component, case_dir)
    labels = situation_labels(journal, plan)
    restored = restored_captures(journal, plan)
    records = []
    for scenario in (component or {}).get('scenarios') or []:
        evidence = [str(e) for e in scenario.get('evidence') or []]
        states = [e for e in evidence if e.endswith('state.json')]
        if not states:
            continue
        state = read_json(case_dir / states[0])
        if not isinstance(state, dict):
            continue
        receipts = [e for e in evidence if e.endswith('check.json')]
        receipt = read_json(case_dir / receipts[0]) if receipts else None
        label = state.get('label') or Path(states[0]).parent.name
        records.append({
            'id': scenario.get('id'), 'label': label,
            'situation': labels.get(label) or ('Baseline' if not records else str(label)),
            'restored': None if restored is None else bool(restored.get(label)),
            'description': str(scenario.get('description') or state.get('description') or ''),
            'plan_description': str(state.get('description') or ''),
            'date_start': state.get('date_start'), 'date_end': state.get('date_end'),
            'dates': pretty_date_range(state.get('date_start'), state.get('date_end')),
            'slicers': dict(state.get('slicers') or {}), 'active_page': state.get('active_page'),
            'figures': observation_figures(state),
            'consistency': list(state.get('consistency') or []),
            'receipt_status': (receipt or {}).get('status'),
            'receipt_expected': (receipt or {}).get('expected') or {},
            'screenshots': [e for e in evidence if e.lower().endswith('.png')],
        })
    return records


def figures_of(observation):
    return {figure['key']: figure for figure in observation.get('figures') or []}


def filter_phrase(observation, baseline=None):
    """'July 1-13, all values selected' / 'July 1-13, Channel: Multiple selections'."""
    parts = [observation.get('dates') or '']
    slicers = observation.get('slicers') or {}
    narrowed = [f'{title}: {caption}' for title, caption in slicers.items()
                if caption and str(caption).strip().lower() not in ('all', 'all values')]
    if slicers and not narrowed:
        parts.append('all values selected')
    parts += narrowed
    return ', '.join(p for p in parts if p)


def changed_filters(observation, baseline):
    """The slicer captions that differ from the baseline, or 'as baseline'."""
    if baseline is None or observation is baseline:
        # The table has its own Dates column; the baseline row lists only its slicer selections.
        slicers = observation.get('slicers') or {}
        narrowed = [f'{title}: {caption}' for title, caption in slicers.items()
                    if caption and str(caption).strip().lower() not in ('all', 'all values')]
        return ', '.join(narrowed) if narrowed else ('all values selected' if slicers else 'as captured')
    base = baseline.get('slicers') or {}
    slicers = observation.get('slicers') or {}
    differing = [f'{title}: {caption}' for title, caption in slicers.items() if base.get(title) != caption]
    return ', '.join(differing) if differing else 'as baseline'


def same_context(observation, baseline):
    """True when this capture put the report back on the baseline's dates and slicers."""
    return (observation.get('date_start') == baseline.get('date_start')
            and observation.get('date_end') == baseline.get('date_end')
            and (observation.get('slicers') or {}) == (baseline.get('slicers') or {}))


def delta(figure, base_figure):
    """{'text': '-882 (-20%)', 'value': -882} when both figures are numbers, else None."""
    if figure is None or base_figure is None:
        return None
    new, old = figure.get('value'), base_figure.get('value')
    if not isinstance(new, (int, float)) or not isinstance(old, (int, float)):
        return None
    if isinstance(new, bool) or isinstance(old, bool) or new == old:
        return None
    difference = new - old
    sign = MINUS if difference < 0 else '+'
    text = f'{sign}{format_number(abs(difference))}'
    if old:
        text += f' ({sign}{abs(round(difference / old * 100))}%)'
    return {'value': difference, 'text': text}


def baseline_status(observation, baseline):
    """The observed table's status cell for a capture that restored the baseline filters.

    Only a capture that actually put every control back (see `restored_captures`)
    and shows the baseline's dates and slicer captions is judged here; a capture
    that is still under a change is not expected to match the baseline.
    """
    if baseline is None or observation is baseline or not same_context(observation, baseline):
        return None
    if observation.get('restored') is False:
        return None
    base = figures_of(baseline)
    differing = [f['name'] for f in observation.get('figures') or []
                 if f['key'] in base and f['display'] != base[f['key']]['display']]
    if differing:
        return {'status': 'fail', 'text': 'Differs from baseline (' + ', '.join(differing) + ')', 'figures': differing}
    return {'status': 'pass', 'text': 'Back to baseline', 'figures': []}


# --- automatic checks ------------------------------------------------------

def _numbers(pair):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in pair)


def consistency_of(observation, pairs):
    """The declared pairs evaluated on this capture: the run's own record, or recomputed."""
    if observation.get('consistency'):
        return observation['consistency']
    if not pairs:
        return []
    figures = figures_of(observation)

    def resolve(reference):
        reference = str(reference or '')
        if reference.startswith('/cards/'):
            return figures.get('card:' + reference[len('/cards/'):], {}).get('value')
        if reference.startswith('/tables/'):
            return figures.get('table:' + reference[len('/tables/'):], {}).get('value')
        return None

    results = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        left, right = resolve(pair.get('a')), resolve(pair.get('b'))
        tolerance = abs(float(pair.get('tolerance') or 0))
        if not _numbers((left, right)):
            status = 'not_comparable'
        else:
            status = 'consistent' if abs(left - right) <= tolerance else 'inconsistent'
        results.append({'label': pair.get('label') or 'Declared pair', 'a': pair.get('a'), 'b': pair.get('b'),
                        'value_a': left, 'value_b': right, 'status': status})
    return results


def automatic_checks(observations, plan=None, journal=None):
    """Compute the checks an analyst would make by hand, in plain language.

    A check that cannot be derived from what was captured is left out rather than
    guessed: no declared pairs means no consistency check, an action whose effect
    has no expected direction produces no direction check.
    """
    observations = list(observations or [])
    checks = []
    if not observations:
        return checks
    baseline = observations[0]
    plan = plan_of(journal, plan)
    actions = capture_actions(journal, plan)

    # 1. Reset returns to baseline, figure by figure.
    for observation in observations[1:]:
        status = baseline_status(observation, baseline)
        if not status:
            continue
        situation = observation['situation']
        if status['status'] == 'pass':
            checks.append({'id': f"reset-{observation['label']}", 'kind': 'reset', 'status': 'pass',
                           'situation': situation,
                           'text': f'Putting the filters back gave the baseline figures again ({situation}).'})
        else:
            names = join_phrases(status['figures'])
            checks.append({'id': f"reset-{observation['label']}", 'kind': 'reset', 'status': 'fail',
                           'situation': situation,
                           'text': f'{situation}: the filters are back to the baseline but {names} '
                                   f'did not return to the baseline value.',
                           'question': f'{names} did not come back to the baseline figure after the filters '
                                       f'were put back the way they started.',
                           'prompt': 'Did the page keep a filter after resetting?'})

    # 2. Each change actually moved a figure.
    for index, observation in enumerate(observations):
        if index == 0:
            continue
        entry = actions.get(observation['label'])
        if not entry or not entry.get('actions'):
            continue
        previous = observations[index - 1]
        before = figures_of(previous)
        moved = []
        for figure in observation.get('figures') or []:
            other = before.get(figure['key'])
            if other is not None and figure['display'] != other['display']:
                moved.append(f"{figure['name']} ({other['display'] or 'blank'} → {figure['display'] or 'blank'})")
        situation = observation['situation']
        if moved:
            checks.append({'id': f"changed-{observation['label']}", 'kind': 'changed', 'status': 'pass',
                           'situation': situation, 'text': f'{situation} changed {join_phrases(moved)}.'})
        else:
            checks.append({'id': f"changed-{observation['label']}", 'kind': 'changed', 'status': 'fail',
                           'situation': situation,
                           'text': f'{situation} left every figure unchanged.',
                           'question': f'{situation} did not move any figure on this page.',
                           'prompt': 'Should this change have had no effect here?'})

        # 3. Direction plausibility, only where the expected direction is knowable.
        expectation = direction_expectation(entry['actions'], previous, observation)
        if not expectation:
            continue
        wrong = []
        for figure in observation.get('figures') or []:
            other = before.get(figure['key'])
            if other is None or not _numbers((figure.get('value'), other.get('value'))):
                continue
            if expectation['direction'] == 'not_up' and figure['value'] > other['value']:
                wrong.append(f"{figure['name']} ({other['display']} → {figure['display']})")
            if expectation['direction'] == 'not_down' and figure['value'] < other['value']:
                wrong.append(f"{figure['name']} ({other['display']} → {figure['display']})")
        if wrong:
            checks.append({'id': f"direction-{observation['label']}", 'kind': 'direction', 'status': 'fail',
                           'situation': situation,
                           'text': f"{situation}: {expectation['because']}, yet {join_phrases(wrong)} moved the other way.",
                           'question': f'{join_phrases(wrong)} moved the wrong way when {situation[:1].lower() + situation[1:]}.',
                           'prompt': expectation['prompt']})
        else:
            checks.append({'id': f"direction-{observation['label']}", 'kind': 'direction', 'status': 'pass',
                           'situation': situation,
                           'text': f"{situation}: {expectation['because']}, and no figure moved the other way."})

    # 4. Declared consistency pairs, per situation.
    pairs = plan.get('consistency') or []
    labels = []
    for observation in observations:
        for result in consistency_of(observation, pairs):
            if result.get('label') not in labels:
                labels.append(result.get('label'))
    for label in labels:
        failures, comparisons, comparable = [], [], 0
        for observation in observations:
            result = next((r for r in consistency_of(observation, pairs) if r.get('label') == label), None)
            if not result:
                continue
            if result.get('status') == 'inconsistent':
                failures.append(f"{observation['situation']} ({format_number(result.get('value_a'))} vs "
                                f"{format_number(result.get('value_b'))})")
            elif result.get('status') == 'consistent':
                comparable += 1
                comparisons.append(f"{format_number(result.get('value_a'))} = {format_number(result.get('value_b'))}")
        if failures:
            checks.append({'id': f'consistency-{label}', 'kind': 'consistency', 'status': 'fail',
                           'text': f"{label}: they do not agree in {', '.join(failures)}.",
                           'question': f"{label}, but they do not: {failures[0]}.",
                           'prompt': 'Is one of them meant to ignore a filter?'})
        elif comparable:
            checks.append({'id': f'consistency-{label}', 'kind': 'consistency', 'status': 'pass',
                           'text': f"{label}: they agree in every situation ({comparisons[0]})."})
        else:
            checks.append({'id': f'consistency-{label}', 'kind': 'consistency', 'status': 'unknown',
                           'text': f'{label}: one of the two figures was not readable, so they could not be compared.'})

    # 5. What our own independent calculation said.
    statuses = [o.get('receipt_status') for o in observations if o.get('receipt_status')]
    if statuses:
        rounded = any(isinstance(value, dict) and 'round' in value
                      for step in plan.get('steps') or [] if isinstance(step, dict)
                      for value in (step.get('expect') or {}).values())
        if all(status == 'passed' for status in statuses):
            text = 'Every figure we checked matched the figure we calculated ourselves from the data.'
            if rounded:
                text += ' One figure was compared at the rounded precision the card displays.'
            checks.append({'id': 'receipt', 'kind': 'receipt', 'status': 'pass', 'text': text})
        else:
            checks.append({'id': 'receipt', 'kind': 'receipt', 'status': 'fail',
                           'text': 'At least one figure did not match the figure we calculated ourselves.',
                           'question': 'A figure on this page did not match the value we calculated from the data.',
                           'prompt': None})
    return checks


def direction_expectation(steps, previous, observation):
    """What a single action must not do to a count or total, or None when unknowable."""
    steps = [s for s in steps if isinstance(s, dict)]
    if len(steps) != 1:
        return None
    step = steps[0]
    if 'toggle_member' in step:
        detail = step['toggle_member'] or {}
        member = detail.get('label', 'a member')
        if detail.get('selected'):
            return {'direction': 'not_down', 'because': f'putting {member} back can only add rows',
                    'prompt': f'Should putting {member} back be able to lower this figure?'}
        return {'direction': 'not_up', 'because': f'excluding {member} can only remove rows',
                'prompt': f'Should excluding {member} be able to increase this figure?'}
    if 'set_date' in step:
        relation = range_relation((previous.get('date_start'), previous.get('date_end')),
                                  (observation.get('date_start'), observation.get('date_end')))
        if relation == 'narrowed':
            return {'direction': 'not_up', 'because': 'a shorter date range covers fewer days',
                    'prompt': 'Should a shorter date range be able to increase this figure?'}
        if relation == 'widened':
            return {'direction': 'not_down', 'because': 'a longer date range covers more days',
                    'prompt': 'Should a longer date range be able to lower this figure?'}
    return None


# --- questions -------------------------------------------------------------

def load_catalog(path=None):
    """The detector catalog, so a method id can be shown as a plain label."""
    return read_json(Path(path) if path else HERE.parent / 'detectors/catalog.json') or {}


def detector_label(catalog, method):
    """A plain label for a detector method id; the id itself only as a last resort."""
    if not method:
        return None
    for entry in (catalog or {}).get('detectors') or []:
        if entry.get('id') == method:
            title = entry.get('title') or entry.get('description') or entry.get('claim')
            if title:
                sentence = re.split(r'[.;]', str(title))[0].strip()
                if len(sentence) > 90:
                    sentence = sentence[:87].rstrip() + '...'
                if sentence:
                    return sentence
            break
    return str(method).split('.')[-1].replace('_', ' ')


NOTHING_TO_ASK = ('Nothing looked inconsistent. Do these figures match what you expect for these dates and filters?')


def questions(component, claims, checks, catalog=None):
    """The decisions asked of the analyst: every computed failure, then every open claim."""
    items = []
    for check in checks or []:
        if check.get('status') != 'fail':
            continue
        items.append({'kind': 'check', 'id': check.get('id'), 'severity': 'issue',
                      'text': check.get('question') or check.get('text'),
                      'detail': check.get('prompt'), 'meta': None, 'technical_note': None})
    for claim in claims or []:
        status = claim.get('status')
        if status not in ('failed', 'inconclusive'):
            continue
        method = claim.get('detector')
        raw = str(claim.get('question') or claim.get('expected') or '').strip()
        token = is_technical(raw) if raw else 'missing'
        prefix = 'Defect: ' if status == 'failed' else ''
        entry = {'kind': 'claim', 'id': claim.get('id'), 'claim_id': claim.get('id'),
                 'severity': 'issue' if status == 'failed' else 'open', 'meta': method, 'detail': None,
                 'technical_note': None}
        if raw and not token:
            entry['text'] = prefix + raw
        else:
            # The agent wrote this for engineers (or wrote nothing); ask a neutral question and
            # keep the original wording behind the technical note.
            if method:
                label = detector_label(catalog, method) or method
                outcome = 'found a problem' if status == 'failed' else 'could not be settled automatically'
                entry['text'] = (f'{prefix}The "{label}" check {outcome} for this component; '
                                 'see the technical note. Does the figure look right to you?')
            else:
                outcome = 'failed' if status == 'failed' else 'could not be verified automatically'
                entry['text'] = (f'{prefix}One expectation ({claim.get("id")}) {outcome}; '
                                 'see the technical note. Does the figure look right to you?')
            note = raw if raw else str(claim.get('observed') or '')
            entry['technical_note'] = note or None
        items.append(entry)
    if not items:
        items.append({'kind': 'none', 'id': 'none', 'severity': 'ok', 'text': NOTHING_TO_ASK,
                      'detail': None, 'meta': None, 'technical_note': None})
    return items


def what_it_shows(component):
    """The plain sentence for the card header: the spec's own, else the definition's first sentence."""
    plain = str((component or {}).get('what_it_shows') or '').strip()
    if plain:
        return plain
    definition = str((component or {}).get('definition') or '').strip()
    first = re.split(r'(?<=[.!?])\s+', definition)[0].strip() if definition else ''
    if first and not is_technical(first):
        return first
    # An older case whose definition opens with DAX: name the component and its page rather
    # than show the analyst a formula.
    name = str((component or {}).get('name') or '').strip()
    page = str((component or {}).get('page') or '').strip()
    if name and page:
        return f"{name}, on the '{page}' page. The technical definition is below."
    return name or first or definition
