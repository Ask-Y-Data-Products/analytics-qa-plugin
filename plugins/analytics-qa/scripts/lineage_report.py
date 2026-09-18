"""Explain how a report actually works, from the evidence the case already holds.

The sign-off page answers "do you accept these numbers". This page answers the
question the analyst asks first, and the one a new engineer asks when a report is
handed to them: what is on each page, which measure is behind each thing on the
screen, which tables that measure reads, where those tables come from, and which
filters reach which visual.

Everything here is read from the case:

  evidence/inventory.json   `visual_bindings` - pages, visuals, projections,
                            filters and interactions as the report defines them
  evidence/source/**        the copied report definition (page order, visibility)
  evidence/model/TMSCHEMA_* the live model capture - measure expressions, their
                            home tables, and each table's partition query
  case.json                 the target, the trace the investigation recorded and
                            the components already reviewed

Nothing is queried, and no relationship is asserted that cannot be read off those
files. A measure whose expression was not captured is named and marked "not
captured" rather than guessed; a table nobody's expression mentions is not linked
to a visual; the tables a measure reads are the ones its own DAX names (followed
through the measures it calls), and the page says so.

  pages_from_evidence(case_dir)        -> one record per page, with its visuals
  measures_used(pages, model)          -> measures the visuals use, then the rest
  tables_for_measures(measures, model) -> table -> source -> the measures reading it
  plain_visual_type(raw)               -> 'line chart' for 'lineChart'
  plain_sentence(expression)           -> 'counts rows of fact_contract', by pattern only
  correctness_chips(expression)        -> the steps worth a second look, as observations
  follow_one_number(pages, measures, model) -> one chain per headline measure
  render(case_dir, out, title=None)    -> writes the page, returns the summary

The plain sentence is pattern matching, never paraphrase: an expression whose shape
is not one of the handful of patterns below reads "see the definition" and keeps its
DAX in the disclosure, because a wrong sentence about a number is worse than none.
"""
import argparse
import html
import json
import re
from pathlib import Path

from jinja2 import Template
from markupsafe import Markup

from analyst_view import anchor_slug, read_json

HERE = Path(__file__).resolve().parent
NOT_CAPTURED = 'not captured'

STYLE = '''*{box-sizing:border-box}body{margin:0;color:#1f2528;background:#f3f5f6;font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1400px;margin:auto;padding:20px 24px 60px}h1{font-size:26px;margin:6px 0 4px}h2{font-size:22px;margin:0}
.lead{color:#586066;margin:0 0 18px}.notice{border-left:4px solid #c27c11;padding:10px 14px;background:#fff6df;border-radius:4px;margin:0 0 18px}
.summary{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}.pill{padding:6px 12px;border-radius:999px;background:#fff;border:1px solid #d3d9dc;font-size:14px}
.pill b{font-size:16px}
.toc{position:sticky;top:0;z-index:6;background:#fff;border:1px solid #d9dfe2;border-radius:10px;padding:12px 16px;margin:0 0 20px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.toc b{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:#586066}
.toc ol{list-style:none;display:flex;flex-wrap:wrap;gap:8px 18px;margin:8px 0 0;padding:0;max-height:24vh;overflow:auto}
.toc li{display:flex;align-items:baseline;gap:6px;font-size:14px}.toc a{color:#0b5c8e;text-decoration:none;font-weight:600}
.toc a:hover{text-decoration:underline}.tag{font-size:12px;color:#586066;background:#f3f5f6;border-radius:999px;padding:2px 8px}
.tag.warn{color:#8a5a00;background:#fff6df}
.find{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:10px 0 0;padding-top:10px;border-top:1px solid #eef1f2}
.find input{flex:1 1 280px;max-width:460px;padding:7px 11px;border:1px solid #c6ced2;border-radius:8px;font:14px inherit;color:inherit}
.find .count{font-size:13px;color:#586066}
.find button{border:1px solid #c6ced2;background:#f7f9f9;border-radius:8px;padding:6px 11px;font:13px inherit;color:#333b40;cursor:pointer}
.empty{color:#8a5a00;font-size:14px;margin:0 0 18px;padding:10px 14px;background:#fff6df;border-left:4px solid #c27c11;border-radius:4px}
.chains{list-style:none;margin:0;padding:0}
.chain{border:1px solid #e3e7e9;border-radius:9px;padding:12px 14px;margin:0 0 12px;background:#fafbfb;scroll-margin-top:var(--sticky,240px)}
.chain h4{font-size:16px;margin:0 0 2px}.chain h4 a{color:#0b5c8e;text-decoration:none}.chain h4 a:hover{text-decoration:underline}
.chain .seen{color:#586066;font-size:13px;margin:0 0 9px}
.steps{display:flex;align-items:stretch;gap:8px;flex-wrap:wrap}
.step{flex:1 1 190px;min-width:180px;background:#fff;border:1px solid #d9dfe2;border-radius:8px;padding:9px 11px}
.step.wide{flex:1.6 1 250px}
.step b{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#586066;margin:0 0 5px;font-weight:700}
.step p{margin:0;font-size:13.5px;overflow-wrap:anywhere}
.step ul{margin:0;padding-left:16px;font-size:13px}.step li{margin:2px 0;overflow-wrap:anywhere}
.step .src{color:#586066;font-size:12.5px;display:block}
.step .unknown{color:#8a5a00}
.arrow{align-self:center;flex:0 0 auto;color:#5f7f96;font-size:22px;font-weight:700;line-height:1}
.chips{display:flex;flex-wrap:wrap;gap:5px;margin:8px 0 0}
.chip{font-size:11.5px;border:1px solid #e0c48a;background:#fff6df;color:#8a5a00;border-radius:999px;padding:2px 8px}
@media(max-width:900px){.steps{flex-direction:column}.arrow{transform:rotate(90deg);align-self:flex-start;margin-left:14px}}
.totop{position:fixed;right:18px;bottom:18px;z-index:9;background:#fff;border:1px solid #c6ced2;border-radius:999px;padding:8px 14px;font-size:13px;color:#0b5c8e;text-decoration:none;box-shadow:0 2px 8px rgba(0,0,0,.14)}
.up{font-size:13px}.up a{color:#586066;text-decoration:none}.up a:hover{text-decoration:underline}
.card{background:#fff;border:1px solid #d9dfe2;border-radius:10px;padding:18px 20px;margin-bottom:22px;box-shadow:0 1px 2px rgba(0,0,0,.04);scroll-margin-top:var(--sticky,240px)}
.head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap}.page{color:#586066;font-size:14px}
h3{font-size:17.5px;margin:20px 0 8px;padding-bottom:5px;border-bottom:1px solid #e9eded}
h4{font-size:15.5px;margin:0 0 4px}
.hint{color:#586066;font-size:13.5px;margin:4px 0 8px}
.vis{display:grid;grid-template-columns:1fr;gap:11px;margin:10px 0 4px}
@media(min-width:1100px){.vis{grid-template-columns:repeat(2,minmax(0,1fr))}}
.v{margin:0;padding:10px 12px 12px;border:1px solid #d9dfe2;border-radius:8px;background:#fafbfb;scroll-margin-top:var(--sticky,240px)}
.v .kind{color:#586066;font-size:13px}.v.mute{opacity:.72}
.scroll{overflow-x:auto;border:1px solid #e3e7e9;border-radius:8px;margin-bottom:8px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
table th{background:#f7f9f9;font-size:12px;text-transform:uppercase;letter-spacing:.03em;color:#586066;white-space:nowrap;text-align:left}
table td,table th{padding:8px 10px;border-bottom:1px solid #e3e7e9;vertical-align:top;text-align:left;overflow-wrap:anywhere}
table tr:last-child td{border-bottom:0}table tr:hover{background:#f2f7fa}
table.fields td:first-child{font-weight:600;width:34%}
#measures table td:first-child,#measures table th:first-child{min-width:150px}
#measures table td:nth-child(2),#measures table th:nth-child(2){width:32%}
td a,th a{color:#0b5c8e;text-decoration:none}td a:hover{text-decoration:underline}
details{margin:6px 0 0}details summary{cursor:pointer;color:#586066;font-size:13px}
details pre{margin:6px 0 0;padding:10px 12px;background:#f7f9f9;border:1px solid #e3e7e9;border-radius:6px;font:12.5px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}
details p{margin:6px 0 0;color:#333b40;font-size:13.5px;overflow-wrap:anywhere}
ul.plain{margin:0;padding:0;list-style:none}ul.plain li{padding:6px 0;border-bottom:1px solid #f0f3f4;font-size:14px}
ul.plain li:last-child{border-bottom:0}
ul.gaps{margin:0;padding-left:20px}ul.gaps li{margin:6px 0;font-size:14px}
code{font:12.5px ui-monospace,SFMono-Regular,Consolas,monospace;background:#f3f5f6;border-radius:4px;padding:1px 5px;overflow-wrap:anywhere}
.none{color:#586066;font-size:14px;margin:6px 0}
.manifest{overflow-wrap:anywhere;font:12px ui-monospace,monospace;color:#586066;margin-top:22px}
@media(max-width:760px){main{padding:12px}}
@media print{body{background:#fff}main{max-width:none;padding:0}
.toc,.totop,.find,.up{display:none!important}
.card,.chain,.v{break-inside:avoid;box-shadow:none}
details{display:block}details summary{font-weight:600;color:#1f2528;list-style:none}
details>*{display:revert!important}
details::details-content{content-visibility:visible!important;block-size:auto!important;display:block!important}
[data-find][hidden]{display:revert!important}}'''

# What a visualType is called in a sentence an analyst would say out loud.
VISUAL_TYPES = {
    'card': 'card', 'cardVisual': 'card', 'multiRowCard': 'multi-row card', 'kpi': 'KPI',
    'tableEx': 'table', 'table': 'table', 'pivotTable': 'matrix', 'matrix': 'matrix',
    'slicer': 'slicer', 'advancedSlicerVisual': 'slicer', 'textbox': 'text box', 'image': 'image',
    'shape': 'shape', 'basicShape': 'shape', 'actionButton': 'button', 'group': 'group of visuals',
    'lineChart': 'line chart', 'areaChart': 'area chart', 'stackedAreaChart': 'stacked area chart',
    'columnChart': 'column chart', 'clusteredColumnChart': 'column chart',
    'stackedColumnChart': 'stacked column chart',
    'hundredPercentStackedColumnChart': '100% stacked column chart',
    'barChart': 'bar chart', 'clusteredBarChart': 'bar chart', 'stackedBarChart': 'stacked bar chart',
    'hundredPercentStackedBarChart': '100% stacked bar chart',
    'lineClusteredColumnComboChart': 'line and column combo chart',
    'lineStackedColumnComboChart': 'line and stacked column combo chart',
    'pieChart': 'pie chart', 'donutChart': 'donut chart', 'treemap': 'treemap', 'funnel': 'funnel',
    'gauge': 'gauge', 'waterfallChart': 'waterfall chart', 'scatterChart': 'scatter chart',
    'map': 'map', 'filledMap': 'filled map', 'shapeMap': 'shape map', 'azureMap': 'map',
    'ribbonChart': 'ribbon chart', 'decompositionTreeVisual': 'decomposition tree',
    'keyDriversVisual': 'key influencers', 'qnaVisual': 'Q&A visual', 'esriVisual': 'map',
    'scriptVisual': 'script visual', 'pythonVisual': 'Python visual',
}

# The field wells, said plainly. The raw role name is kept when it is not one of these.
ROLE_WORDS = {
    'Values': 'value', 'Value': 'value', 'Data': 'value', 'Y': 'value (Y axis)', 'Y2': 'value (second axis)',
    'Category': 'grouped by', 'Axis': 'axis', 'X': 'X axis', 'Rows': 'rows', 'Columns': 'columns',
    'Legend': 'legend', 'Series': 'series', 'Size': 'size', 'Details': 'details', 'Tooltips': 'tooltip',
    'Gradient': 'colour', 'Play': 'play axis', 'Breakdown': 'breakdown', 'Goal': 'target',
    'TrendLine': 'trend', 'Indicator': 'indicator', 'Location': 'location', 'Latitude': 'latitude',
    'Longitude': 'longitude',
}

AGGREGATIONS = {0: 'Sum', 1: 'Average', 2: 'Minimum', 3: 'Maximum', 4: 'Count (distinct)',
                5: 'Count', 6: 'Median', 7: 'Standard deviation', 8: 'Variance', 9: 'Count (non-blank)'}

INTERACTION_WORDS = {'NoFilter': 'does not filter', 'DataFilter': 'filters',
                     'HighlightFilter': 'highlights inside'}

CONNECTORS = {'GoogleBigQuery': 'Google BigQuery', 'Sql': 'SQL Server', 'AmazonRedshift': 'Amazon Redshift',
              'Snowflake': 'Snowflake', 'PostgreSQL': 'PostgreSQL', 'MySQL': 'MySQL', 'Oracle': 'Oracle',
              'Excel': 'an Excel workbook', 'Csv': 'a CSV file', 'Databricks': 'Databricks',
              'AzureStorage': 'Azure Storage', 'Web': 'a web source', 'Odbc': 'an ODBC source'}

MAX_VALUES = 8
MAX_INTERACTIONS = 40


def plugin_version():
    """Version of the plugin this page was generated by; 'unknown' outside a plugin checkout."""
    try:
        return str(json.loads((HERE.parent / '.claude-plugin/plugin.json').read_text(encoding='utf-8-sig'))['version'])
    except (OSError, ValueError, KeyError, TypeError):
        return 'unknown'


# --- reading the evidence ---------------------------------------------------

def rows_of(document):
    """The rows of a captured DMV rowset, whether it was saved wrapped or bare."""
    if isinstance(document, dict):
        rows = document.get('rows')
        return rows if isinstance(rows, list) else []
    return document if isinstance(document, list) else []


def load_model(case_dir):
    """The model capture as this page needs it; `captured` is False when there is none.

    Measures are keyed by lower-case name because that is how a visual references
    them (`Measure.Property`); the raw rows stay in the case.
    """
    folder = Path(case_dir) / 'evidence/model'
    measures = rows_of(read_json(folder / 'TMSCHEMA_MEASURES.json'))
    tables = rows_of(read_json(folder / 'TMSCHEMA_TABLES.json'))
    partitions = rows_of(read_json(folder / 'TMSCHEMA_PARTITIONS.json'))
    if not measures and not tables:
        return {'captured': False, 'measures': {}, 'tables': {}, 'table_names': [], 'partitions': {}}
    by_id = {str(row.get('ID')): str(row.get('Name') or '') for row in tables if isinstance(row, dict)}
    records = {}
    for row in measures:
        if not isinstance(row, dict) or not row.get('Name'):
            continue
        records[str(row['Name']).strip().lower()] = {
            'name': str(row['Name']).strip(), 'expression': row.get('Expression'),
            'home_table': by_id.get(str(row.get('TableID'))), 'format': row.get('FormatString'),
            'hidden': bool(row.get('IsHidden')), 'folder': row.get('DisplayFolder'),
            'description': row.get('Description'), 'error': row.get('ErrorMessage')}
    grouped = {}
    for row in partitions:
        if isinstance(row, dict):
            grouped.setdefault(by_id.get(str(row.get('TableID')), ''), []).append(row)
    return {'captured': True, 'measures': records, 'tables': {str(v): v for v in by_id.values() if v},
            'table_names': sorted({v for v in by_id.values() if v}),
            'hidden_tables': {str(r.get('Name')) for r in tables if isinstance(r, dict) and r.get('IsHidden')},
            'partitions': grouped}


def definition_dir(case_dir):
    """The copied report definition folder, when `qa.py inspect` preserved one."""
    source = Path(case_dir) / 'evidence/source'
    if not source.is_dir():
        return None
    for path in sorted(source.rglob('pages/pages.json')):
        return path.parent.parent
    for path in sorted(source.rglob('definition/report.json')):
        return path.parent
    return None


def page_metadata(case_dir):
    """(page order, {page id: {'visibility': ...}}) from the copied definition, or ([], {})."""
    folder = definition_dir(case_dir)
    if not folder:
        return [], {}
    order = (read_json(folder / 'pages/pages.json') or {}).get('pageOrder') or []
    detail = {}
    for path in sorted(folder.glob('pages/*/page.json')):
        page = read_json(path)
        if isinstance(page, dict) and page.get('name'):
            detail[str(page['name'])] = {'visibility': page.get('visibility'),
                                         'display_name': page.get('displayName'),
                                         'filters': page.get('filterConfig')}
    return [str(name) for name in order], detail


# --- reading one field binding ---------------------------------------------

def source_entity(expression):
    """The table a projection's expression points at, following SourceRef/Hierarchy wrappers."""
    seen = 0
    while isinstance(expression, dict) and seen < 8:
        seen += 1
        if 'SourceRef' in expression:
            reference = expression['SourceRef'] or {}
            return reference.get('Entity') or reference.get('Source')
        expression = next((v for v in expression.values() if isinstance(v, dict)), None)
    return None


def field_reference(projection):
    """What one field well entry actually is: a measure, a column, or something unnamed.

    Returns {'kind', 'entity', 'property', 'aggregation'}; `kind` is 'measure',
    'column', 'hierarchy level' or 'expression' when the shape is not one the
    definition names.
    """
    field = (projection or {}).get('field')
    result = {'kind': 'expression', 'entity': None, 'property': None, 'aggregation': None}
    if not isinstance(field, dict):
        return result
    if 'Aggregation' in field:
        inner = field['Aggregation'] or {}
        result['aggregation'] = AGGREGATIONS.get(inner.get('Function'), inner.get('Function'))
        nested = field_reference({'field': inner.get('Expression')})
        nested['aggregation'] = result['aggregation']
        return nested
    for key, kind in (('Measure', 'measure'), ('Column', 'column'), ('HierarchyLevel', 'hierarchy level')):
        if key in field:
            inner = field[key] or {}
            result['kind'] = kind
            result['property'] = inner.get('Property') or inner.get('Level')
            result['entity'] = source_entity(inner.get('Expression'))
            return result
    return result


def field_record(role, projection):
    """One row of a visual's field table: what the reader sees, and what is behind it."""
    reference = field_reference(projection)
    label = (projection or {}).get('displayName') or (projection or {}).get('nativeQueryRef') \
        or reference['property'] or (projection or {}).get('queryRef') or 'unnamed field'
    behind = reference['property'] or str((projection or {}).get('queryRef') or '')
    if reference['entity'] and reference['property'] and reference['kind'] != 'measure':
        behind = f"{reference['entity']}[{reference['property']}]"
    if reference['aggregation'] and reference['kind'] == 'column':
        behind = f"{reference['aggregation']} of {behind}"
    return {'role': str(role), 'role_word': ROLE_WORDS.get(str(role), str(role)),
            'label': str(label), 'kind': reference['kind'], 'measure': reference['property'],
            'entity': reference['entity'], 'behind': behind,
            'query_ref': (projection or {}).get('queryRef')}


def visual_title(binding):
    """The title the report puts on the visual, when the definition sets a literal one."""
    for entry in binding.get('title_properties') or []:
        text = ((entry or {}).get('properties') or {}).get('text') or {}
        literal = ((text.get('expr') or {}).get('Literal') or {}).get('Value')
        if literal:
            return str(literal).strip().strip("'")
    return ''


CAMEL = re.compile(r'(?<=[a-z0-9])(?=[A-Z])')
LONG_HEX = re.compile(r'[0-9A-Fa-f]{16,}')


def plain_visual_type(raw):
    """'lineClusteredColumnComboChart' -> 'line and column combo chart'."""
    name = str(raw or '').strip()
    if not name:
        return 'visual'
    if name in VISUAL_TYPES:
        return VISUAL_TYPES[name]
    if LONG_HEX.search(name):
        return 'custom visual'
    words = CAMEL.sub(' ', name).replace('_', ' ').strip().lower()
    return words or 'visual'


# --- pages ------------------------------------------------------------------

def literal_values(condition):
    """The literal values a categorical filter condition lists, in order."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            literal = node.get('Literal')
            if isinstance(literal, dict) and literal.get('Value') is not None:
                found.append(str(literal['Value']).strip().strip("'"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(condition)
    return found


def describe_filter(entry):
    """One recorded filter, as a sentence plus the raw definition for the details block."""
    if not isinstance(entry, dict):
        return None
    reference = field_reference(entry)
    field = entry.get('field')
    name = reference['property'] or (entry.get('displayName') if isinstance(entry.get('displayName'), str) else None)
    where = ((entry.get('filter') or {}).get('Where') or []) if isinstance(entry.get('filter'), dict) else []
    values = literal_values(where)
    target = f"{reference['entity']}[{name}]" if reference['entity'] and name else (name or 'a field')
    if values:
        shown = ', '.join(values[:MAX_VALUES])
        more = f' and {len(values) - MAX_VALUES} more' if len(values) > MAX_VALUES else ''
        sentence = f'{target} is restricted to {len(values)} value(s): {shown}{more}.'
    elif where:
        sentence = f'{target} carries a condition the definition records but does not list as values.'
    else:
        sentence = f'{target} is offered as a filter with nothing selected in the definition.'
    return {'field': target, 'kind': str(entry.get('type') or 'filter'), 'text': sentence,
            'values': values, 'raw': json.dumps(field, indent=1, default=str)[:1200] if field else ''}


def filters_of(config):
    """Every filter a filterConfig records, described; [] for an absent or empty config."""
    if not isinstance(config, dict):
        return []
    return [f for f in (describe_filter(e) for e in config.get('filters') or []) if f]


def pages_from_evidence(case_dir):
    """One record per page of the captured report definition, with its visuals.

    Built from `evidence/inventory.json`'s `visual_bindings`, in the page order
    the copied definition records when it is present. Returns [] when the case
    holds no inventory, which the page reports as a gap rather than an error.
    """
    case_dir = Path(case_dir)
    inventory = read_json(case_dir / 'evidence/inventory.json') or {}
    bindings = inventory.get('visual_bindings') or []
    order, detail = page_metadata(case_dir)
    pages = {}
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        page_id = str(binding.get('page_id') or binding.get('page') or 'page')
        page = pages.setdefault(page_id, {
            'id': page_id, 'name': str(binding.get('page') or detail.get(page_id, {}).get('display_name') or page_id),
            'anchor': 'page-' + anchor_slug(page_id), 'visuals': [], 'interactions': [],
            'filters': filters_of((binding.get('filters') or {}).get('page')) or filters_of(detail.get(page_id, {}).get('filters')),
            'hidden': str(detail.get(page_id, {}).get('visibility') or '') == 'HiddenInViewMode'})
        if not page['interactions']:
            page['interactions'] = [i for i in binding.get('interactions') or [] if isinstance(i, dict)]
        fields = []
        for role, projections in (binding.get('projections') or {}).items():
            for projection in projections or []:
                fields.append(field_record(role, projection))
        page['visuals'].append({
            'id': str(binding.get('visual_id') or ''), 'title': visual_title(binding),
            'type_raw': str(binding.get('type') or ''), 'type': plain_visual_type(binding.get('type')),
            'hidden': bool(binding.get('hidden')), 'fields': fields,
            'filters': filters_of((binding.get('filters') or {}).get('visual')),
            'anchor': 'visual-' + anchor_slug(str(binding.get('visual_id') or ''))})
    result = list(pages.values())
    if order:
        position = {name: index for index, name in enumerate(order)}
        result.sort(key=lambda page: (position.get(page['id'], len(order)), page['name']))
    report_config = next((b['filters']['report'] for b in bindings
                          if isinstance(b, dict) and (b.get('filters') or {}).get('report')), None)
    report_filters = filters_of(report_config)
    for page in result:
        page['report_filters'] = report_filters
    return result


def visual_label(visual):
    """How a visual is named on this page: its own title, or its type and first field."""
    if visual.get('title'):
        return visual['title']
    first = next((f['label'] for f in visual.get('fields') or []), '')
    kind = visual.get('type') or 'visual'
    return f'{kind[:1].upper()}{kind[1:]}' + (f' showing {first}' if first else ' (untitled)')


# --- measures ---------------------------------------------------------------

QUOTED_TABLE = re.compile(r"'([^']+)'\s*\[")
BARE_TABLE = re.compile(r'\b([A-Za-z_][A-Za-z0-9_ ]*?)\s*\[')
BRACKETED = re.compile(r'(?<!\])\[([^\]\[]+)\]')


def expression_references(expression, model):
    """(tables, measures) whose names appear in this DAX expression.

    Only names the captured model actually defines are returned, so a bracketed
    word that is not a measure and a table-looking token that is not a table are
    both dropped instead of inventing an object. A table counts whether the
    expression reads a column of it (`fact_x[amount]`) or the table itself
    (`COUNTROWS ( fact_x )`).
    """
    text = str(expression or '')
    if not text:
        return [], []
    tables = []
    for name in (model or {}).get('table_names') or []:
        pattern = r"(?<![A-Za-z0-9_])'?" + re.escape(name) + r"'?(?![A-Za-z0-9_])"
        if re.search(pattern, text):
            tables.append(name)
    qualified = {m.end() for m in list(QUOTED_TABLE.finditer(text)) + list(BARE_TABLE.finditer(text))}
    measures = []
    for match in BRACKETED.finditer(text):
        if match.start() + 1 in qualified:
            continue  # 'Table'[Column] - a column, not a measure reference
        record = ((model or {}).get('measures') or {}).get(match.group(1).strip().lower())
        if record and record['name'] not in measures:
            measures.append(record['name'])
    return sorted(tables), measures


def measure_closure(name, model, depth=4):
    """Tables this measure reads, following the measures it calls, bounded and cycle-safe."""
    measures = (model or {}).get('measures') or {}
    tables, through, seen, frontier = [], [], set(), [(str(name), 0)]
    while frontier:
        current, level = frontier.pop(0)
        key = current.strip().lower()
        if key in seen or level > depth:
            continue
        seen.add(key)
        record = measures.get(key)
        if not record:
            continue
        if level:
            through.append(record['name'])
        found, called = expression_references(record.get('expression'), model)
        if record.get('home_table') and level == 0 and record['home_table'] not in tables:
            pass  # a measure's home table is where it is stored, not necessarily what it reads
        for table in found:
            if table not in tables:
                tables.append(table)
        frontier += [(other, level + 1) for other in called]
    return tables, through


def measures_used(pages, model):
    """Every measure the captured visuals use, then every other measure the model defines.

    A used record carries `used_by` (page, visual and the label on screen), the
    expression when the model capture has it, and the tables that expression -
    and the measures it calls - name. A measure no captured visual uses is
    returned with `used` False so the page can mark it.
    """
    model = model or {}
    defined = model.get('measures') or {}
    records = {}
    for page in pages or []:
        for visual in page.get('visuals') or []:
            for field in visual.get('fields') or []:
                if field.get('kind') != 'measure' or not field.get('measure'):
                    continue
                name = str(field['measure']).strip()
                key = name.lower()
                record = records.get(key)
                if record is None:
                    known = defined.get(key) or {}
                    tables, through = measure_closure(name, model) if known else ([], [])
                    record = records[key] = {
                        'name': known.get('name') or name, 'key': key,
                        'anchor': 'measure-' + anchor_slug(key), 'used': True, 'used_by': [],
                        'expression': known.get('expression'), 'captured': bool(known),
                        'home_table': known.get('home_table'), 'format': known.get('format'),
                        'hidden': known.get('hidden'), 'error': known.get('error'),
                        'entity': field.get('entity'), 'tables': tables, 'through': through}
                record['used_by'].append({'page': page.get('name'), 'page_anchor': page.get('anchor'),
                                          'visual': visual_label(visual), 'label': field.get('label'),
                                          'role': field.get('role_word')})
    used = sorted(records.values(), key=lambda r: r['name'].lower())
    unused = []
    for key, known in sorted(defined.items(), key=lambda item: item[1]['name'].lower()):
        if key in records:
            continue
        tables, through = measure_closure(known['name'], model)
        unused.append({'name': known['name'], 'key': key, 'anchor': 'measure-' + anchor_slug(key),
                       'used': False, 'used_by': [], 'expression': known.get('expression'),
                       'captured': True, 'home_table': known.get('home_table'),
                       'format': known.get('format'), 'hidden': known.get('hidden'),
                       'error': known.get('error'), 'entity': None, 'tables': tables, 'through': through})
    return used + unused


# --- tables -----------------------------------------------------------------

M_STEP = re.compile(r'\[\s*Name\s*=\s*"([^"]+)"\s*,\s*Kind\s*=\s*"([^"]+)"\s*\]')
M_CONNECTOR = re.compile(r'([A-Za-z][A-Za-z0-9]*)\.[A-Za-z][A-Za-z0-9]*\s*\(')
NATIVE_QUERY = re.compile(r'Value\.NativeQuery\s*\(', re.IGNORECASE)


def summarise_source(partition):
    """The source a partition reads, as far as its query says so in plain words."""
    if not isinstance(partition, dict):
        return NOT_CAPTURED, ''
    query = str(partition.get('QueryDefinition') or '').strip()
    if not query:
        return NOT_CAPTURED, ''
    if partition.get('Type') == 2:
        return 'calculated inside the model', query
    steps = {kind: name for name, kind in M_STEP.findall(query)}
    connector = M_CONNECTOR.search(query)
    source = CONNECTORS.get(connector.group(1), connector.group(1)) if connector else ''
    table = steps.get('Table') or steps.get('View')
    schema = steps.get('Schema')
    database = steps.get('Database')
    if table:
        qualified = '.'.join(part for part in (database if not schema else None, schema, table) if part)
        return (f'{source}, table {qualified}' if source else f'table {qualified}'), query
    if NATIVE_QUERY.search(query):
        return (f'{source}, a native query' if source else 'a native query'), query
    if source:
        return f'{source} (the query does not name a single table)', query
    line = next((l.strip() for l in query.splitlines() if l.strip()), '')
    return (line[:110] + ('...' if len(line) > 110 else '')), query


def table_sources(model):
    """table name -> (source in plain words, the queries behind it, how many partitions)."""
    sources = {}
    for name, entries in ((model or {}).get('partitions') or {}).items():
        if not name:
            continue
        summaries = [summarise_source(entry) for entry in entries or []]
        sources[name] = ('; '.join(dict.fromkeys(s for s, _ in summaries if s)) or NOT_CAPTURED,
                         '\n\n'.join(q for _, q in summaries if q), len(entries or []))
    return sources


def tables_for_measures(measures, model):
    """One record per table the used measures read: where it comes from, and who reads it.

    Only measures marked `used` contribute, because this section answers "where do
    the numbers on the screen come from". A table whose partition was not captured
    is listed with its source as 'not captured'.
    """
    model = model or {}
    sources = table_sources(model)
    hidden = model.get('hidden_tables') or set()
    records = {}
    for measure in measures or []:
        if not measure.get('used'):
            continue
        for table in measure.get('tables') or []:
            record = records.setdefault(table, {'name': table, 'anchor': 'table-' + anchor_slug(table),
                                                'measures': [], 'hidden': table in hidden})
            if measure['name'] not in record['measures']:
                record['measures'].append(measure['name'])
    for name, record in records.items():
        source, query, count = sources.get(name, (NOT_CAPTURED, '', 0))
        record['source'], record['query'], record['partitions'] = source, query, count
    order = {'fact': 0, 'dim': 1}
    return sorted(records.values(),
                  key=lambda r: (order.get(r['name'].split('_')[0].lower(), 2), r['name'].lower()))


# --- one expression, in plain words -----------------------------------------
#
# Every sentence here is produced by matching the shape of the DAX, never by
# paraphrasing it. When no pattern matches, `plain_sentence` says "see the
# definition" and the reader is sent to the expression itself.

LINE_COMMENT = re.compile(r'//[^\n]*')
BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.S)
FUNCTION_CALL = re.compile(r'^([A-Za-z][A-Za-z0-9_.]*)\s*\((.*)\)$', re.S)
COLUMN_REF = re.compile(r"^'?([A-Za-z_][^'\[\]]*?)'?\s*\[([^\]]+)\]$", re.S)
MEASURE_REF = re.compile(r'^\[([^\]]+)\]$', re.S)
TABLE_ONLY = re.compile(r"^'?([A-Za-z_][A-Za-z0-9_ ]*?)'?$")
CLOCK = re.compile(r'\b(TODAY|NOW|UTCNOW|UTCTODAY)\s*\(', re.I)
FILTER_REMOVAL = re.compile(r'\b(ALL|ALLEXCEPT|ALLSELECTED|ALLNOBLANKROW|REMOVEFILTERS)\s*\(', re.I)
EQUALITY = re.compile(r"^('?[A-Za-z_][^'\[\]]*?'?\s*\[[^\]]+\])\s*=(?!=)\s*(.+)$", re.S)
IN_SET = re.compile(r"^('?[A-Za-z_][^'\[\]]*?'?\s*\[[^\]]+\])\s+IN\s*\{(.+)\}$", re.S | re.I)
NUMBER = re.compile(r'^-?\d+(\.\d+)?$')
SEE_DEFINITION = 'see the definition'
UNKNOWN = 'unknown'

REMOVERS = {'ALL', 'ALLSELECTED', 'ALLNOBLANKROW', 'REMOVEFILTERS'}
CLOCK_UNITS = (('MONTH', 'month'), ('QUARTER', 'quarter'), ('YEAR', 'year'),
               ('WEEKNUM', 'week'), ('DAY', 'day'))
AGGREGATIONS_IN_WORDS = {
    'SUM': 'adds up', 'AVERAGE': 'averages', 'MIN': 'takes the smallest', 'MAX': 'takes the largest',
    'MEDIAN': 'takes the median of', 'DISTINCTCOUNT': 'counts the distinct values of',
    'COUNT': 'counts the values of', 'COUNTA': 'counts the values of'}


def without_comments(expression):
    """The expression with its DAX comments removed, so patterns match the code only."""
    return LINE_COMMENT.sub(' ', BLOCK_COMMENT.sub(' ', str(expression or '')))


def _scan(text):
    """(index, character, depth, in_string) for each character, quotes counted as text."""
    depth, quote = 0, ''
    for index, character in enumerate(text):
        if quote:
            yield index, character, depth, True
            if character == quote:
                quote = ''
            continue
        if character in '"\'':
            quote = character
            yield index, character, depth, True
            continue
        if character in '([{':
            depth += 1
        elif character in ')]}':
            depth -= 1
        yield index, character, depth, False


def balanced(text):
    """True when every bracket in `text` closes, ignoring what is inside quotes."""
    depth = 0
    for _, character, level, quoted in _scan(text):
        if quoted:
            continue
        depth = level
        if depth < 0:
            return False
    return depth == 0


def strip_outer(expression):
    """The expression without surrounding whitespace or a pair of wrapping parentheses."""
    body = str(expression or '').strip()
    while body.startswith('(') and body.endswith(')'):
        inner = body[1:-1]
        if not balanced(inner):
            break
        body = inner.strip()
    return body


def split_arguments(body):
    """The top-level, comma-separated arguments of a call body."""
    parts, start = [], 0
    for index, character, depth, quoted in _scan(body):
        if character == ',' and depth == 0 and not quoted:
            parts.append(body[start:index].strip())
            start = index + 1
    current = body[start:].strip()
    if current:
        parts.append(current)
    return [part for part in parts if part]


def call_of(expression):
    """('CALCULATE', 'its arguments') when this is exactly one call, else (None, '')."""
    body = strip_outer(expression)
    match = FUNCTION_CALL.match(body)
    if not match or not balanced(match.group(2)):
        return None, ''
    return match.group(1).upper(), match.group(2)


def table_noun(name):
    """'dim_channel' -> 'channel'; the name itself when it carries no warehouse prefix."""
    text = str(name or '').strip().strip("'")
    for prefix in ('dim_', 'fact_', 'bridge_', 'stg_'):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break
    return text.replace('_', ' ').strip() or str(name or '')


def column_words(name):
    """'is_first_contract_for_user' -> 'is first contract for user'."""
    return str(name or '').strip().replace('_', ' ').strip()


def pretty_value(value):
    """'google_ads' -> 'Google Ads'; a value that already reads as words is left alone."""
    text = str(value or '').strip().strip('"').strip("'").replace('_', ' ').strip()
    if not text:
        return ''
    if text == text.lower():
        return ' '.join(word[:1].upper() + word[1:] for word in text.split())
    return text


def column_parts(expression):
    """('dim_channel', 'channel_key') for a column reference, or None."""
    match = COLUMN_REF.match(strip_outer(expression))
    return (match.group(1).strip(), match.group(2).strip()) if match else None


def is_dimension(table):
    """True for a table whose name says it describes a thing, so "the Meta channel" reads right."""
    return str(table or '').strip().strip("'").lower().startswith('dim')


def condition_phrase(expression):
    """One comparison, in plain words: `dim_channel[channel_key] = "meta"` -> 'only the Meta channel'.

    The "the <value> <thing>" wording is only used for a dimension table, where the
    table names the thing being picked. On a fact table it would read "only the
    Inquiry program inquiry", so the column does the talking instead.
    """
    text = strip_outer(without_comments(expression))
    match = IN_SET.match(text)
    if match:
        parts = column_parts(match.group(1))
        values = [pretty_value(v) for v in split_arguments(match.group(2)) if pretty_value(v)]
        if not parts or not values:
            return None
        listed = ', '.join(values[:-1]) + ' and ' + values[-1] if len(values) > 1 else values[0]
        if is_dimension(parts[0]):
            return f'only the {listed} {table_noun(parts[0])}' + ('s' if len(values) > 1 else '')
        return f'only rows where {column_words(parts[1])} is {listed}' if len(values) == 1 \
            else f'only rows where {column_words(parts[1])} is one of {listed}'
    match = EQUALITY.match(text)
    if not match:
        return None
    parts = column_parts(match.group(1))
    if not parts:
        return None
    table, column = parts
    value = strip_outer(match.group(2))
    flag = value.upper().rstrip('()')
    if flag in ('TRUE', 'FALSE'):
        return f'only rows where {column_words(column)} is {flag.lower()}'
    literal = value.strip('"').strip("'")
    if NUMBER.match(literal):
        words = column_words(column)
        if literal in ('0', '1') and re.match(r'^(is|has)\b', words):
            return f'only rows where {words} is {"true" if literal == "1" else "false"}'
        return f'only rows where {words} is {literal}'
    if not literal:
        return None
    if is_dimension(table):
        return f'only the {pretty_value(literal)} {table_noun(table)}'
    return f'only rows where {column_words(column)} is {pretty_value(literal)}'


def clock_unit(expression):
    """'month' for an expression that compares against MONTH(TODAY()), else None."""
    text = without_comments(expression)
    if not CLOCK.search(text):
        return None
    for token, word in CLOCK_UNITS:
        if re.search(r'\b' + token + r'\s*\(', text, re.I):
            return word
    return 'date'


def plain_filter(expression, depth=0):
    """(sentence, is_clause) for one filter argument, or None when no pattern matches.

    A clause is a phrase that reads as something the measure *does* ("ignores the
    date filter"); anything else is a noun phrase that follows "restricted to".
    """
    text = strip_outer(without_comments(expression))
    if not text or depth > 4:
        return None
    condition = condition_phrase(text)
    if condition:
        return condition, False
    name, body = call_of(text)
    if name is None:
        return None
    arguments = split_arguments(body)
    if name in REMOVERS:
        if not arguments:
            return 'ignores every filter on the report', True
        target = column_parts(arguments[0])
        noun = table_noun(target[0]) if target else table_noun(strip_outer(arguments[0]).strip("'"))
        return f'ignores the {noun} filter', True
    if name == 'KEEPFILTERS' and len(arguments) == 1:
        return plain_filter(arguments[0], depth + 1)
    if name == 'FILTER' and len(arguments) == 2:
        inner, condition_text = arguments
        inner_name, inner_body = call_of(inner)
        unit = clock_unit(condition_text)
        if inner_name in REMOVERS:
            target = column_parts(strip_outer(inner_body)) or (strip_outer(inner_body).strip("'"), '')
            noun = table_noun(target[0])
            if unit:
                return (f"ignores the {noun} filter and uses the machine's current {unit}", True)
            inside = condition_phrase(condition_text)
            if inside:
                return f'ignores the {noun} filter and keeps {inside}', True
            return None
        if unit:
            return (f"uses the machine's current {unit}", True)
        inside = condition_phrase(condition_text)
        table = TABLE_ONLY.match(strip_outer(inner))
        if inside and table:
            return f'{inside} in {table.group(1).strip()}', False
        return None
    return None


NOUN_FORMS = (
    (re.compile(r'^counts rows of (.+)$', re.S), r'the number of rows in \1'),
    (re.compile(r'^adds up (.+)$', re.S), r'the total of \1'),
    (re.compile(r'^averages (.+)$', re.S), r'the average of \1'),
    (re.compile(r'^takes the (smallest|largest|median of) (.+)$', re.S), r'the \1 \2'),
    (re.compile(r'^counts the (distinct values of|values of) (.+)$', re.S), r'the number of \1 \2'),
)


def noun_phrase(text):
    """'counts rows of t' -> 'the number of rows in t', so it can follow 'divided by'."""
    for pattern, replacement in NOUN_FORMS:
        if pattern.match(str(text or '')):
            return pattern.sub(replacement, text)
    return text


def plain_phrase(expression, depth=0):
    """What this expression computes, in plain words, or None when no pattern matches."""
    text = strip_outer(without_comments(expression))
    if not text or depth > 4:
        return None
    measure = MEASURE_REF.match(text)
    if measure:
        return measure.group(1).strip()
    name, body = call_of(text)
    if name is None:
        return None
    arguments = split_arguments(body)
    if name == 'COUNTROWS' and len(arguments) == 1:
        table = TABLE_ONLY.match(strip_outer(arguments[0]))
        return f'counts rows of {table.group(1).strip()}' if table else None
    if name in AGGREGATIONS_IN_WORDS and len(arguments) == 1:
        parts = column_parts(arguments[0])
        return f'{AGGREGATIONS_IN_WORDS[name]} {parts[1]} in {parts[0]}' if parts else None
    if name == 'DIVIDE' and len(arguments) >= 2:
        top, bottom = plain_phrase(arguments[0], depth + 1), plain_phrase(arguments[1], depth + 1)
        return f'{noun_phrase(top)} divided by {noun_phrase(bottom)}' if top and bottom else None
    if name == 'CALCULATE' and len(arguments) >= 2:
        base = plain_phrase(arguments[0], depth + 1)
        filters = [plain_filter(argument, depth + 1) for argument in arguments[1:]]
        if not base or not all(filters):
            return None
        restricted = [text for text, clause in filters if not clause]
        clauses = [text for text, clause in filters if clause]
        sentence = base
        if restricted:
            listed = ', '.join(restricted[:-1]) + ' and ' + restricted[-1] \
                if len(restricted) > 1 else restricted[0]
            sentence += f', restricted to {listed}'
        for clause in clauses:
            sentence += f', and it {clause}'
        return sentence
    if name == 'CALCULATE' and len(arguments) == 1:
        return plain_phrase(arguments[0], depth + 1)
    return None


def plain_sentence(expression):
    """One sentence for a measure, or 'see the definition' when no pattern fits.

    `plain_sentence(None)` is None: an expression that was never captured is a gap,
    not an expression nobody could read.
    """
    if expression is None or not str(expression).strip():
        return None
    return plain_phrase(expression) or (plain_filter(expression) or (None,))[0] or SEE_DEFINITION


def sentence_case(text):
    """'counts rows of x' -> 'Counts rows of x.', leaving an already-final stop alone."""
    body = str(text or '').strip()
    if not body:
        return ''
    return body[:1].upper() + body[1:] + ('' if body.endswith(('.', '?', '!')) else '.')


# --- the steps worth a second look ------------------------------------------

CHIP_CLOCK = 'reads the machine clock'
CHIP_REMOVES = 'removes a filter'
CHIP_DIVIDES = 'divides without a guard'
CHIP_ONE_SEGMENT = 'restricted to one channel or segment'


def calls_named(expression, name):
    """The argument lists of every `name(...)` call in this expression."""
    text = without_comments(expression)
    found = []
    for match in re.finditer(r'\b' + re.escape(name) + r'\s*\(', text, re.I):
        start = match.end()
        depth = 1
        for index, character, _, quoted in _scan(text[start:]):
            if quoted:
                continue
            if character in '([':
                depth += 1
            elif character in ')]':
                depth -= 1
                if depth == 0:
                    found.append(split_arguments(text[start:start + index]))
                    break
    return found


def divides_bare(expression):
    """True when the expression uses the `/` operator, which has no blank or zero guard."""
    text = without_comments(expression)
    for index, character, _, quoted in _scan(text):
        if quoted or character != '/':
            continue
        if text[index - 1:index] == '/' or text[index + 1:index + 2] in ('/', '*'):
            continue
        return True
    return False


def correctness_chips(expression):
    """The steps in this expression a reviewer should look at, as observations.

    These are patterns, not verdicts: a measure may read the clock or drop a filter
    entirely on purpose. An expression that shows none of them gets no chips.
    """
    text = without_comments(expression or '')
    if not text.strip():
        return []
    chips = []
    if CLOCK.search(text):
        chips.append(CHIP_CLOCK)
    if FILTER_REMOVAL.search(text):
        chips.append(CHIP_REMOVES)
    if divides_bare(text) or any(len(arguments) < 3 for arguments in calls_named(text, 'DIVIDE')):
        chips.append(CHIP_DIVIDES)
    for function in ('CALCULATE', 'CALCULATETABLE', 'FILTER'):
        for arguments in calls_named(text, function):
            if any((condition_phrase(argument) or '').startswith('only the') for argument in arguments[1:]):
                chips.append(CHIP_ONE_SEGMENT)
                break
        if CHIP_ONE_SEGMENT in chips:
            break
    return chips


def measure_chips(measure, model):
    """The chips of a measure's own expression and of every measure it calls."""
    measures = (model or {}).get('measures') or {}
    ordered, seen = [], set()
    for chip in correctness_chips(measure.get('expression')):
        if chip not in seen:
            seen.add(chip)
            ordered.append(chip)
    for name in measure.get('through') or []:
        record = measures.get(str(name).strip().lower()) or {}
        for chip in correctness_chips(record.get('expression')):
            if chip in seen:
                continue
            seen.add(chip)
            ordered.append(f'{chip} (in {record.get("name") or name})')
    return ordered


# --- follow one number ------------------------------------------------------

HEADLINE_VISUALS = {'card', 'multi-row card', 'KPI', 'gauge'}
TOTAL_VISUALS = {'table', 'matrix'}
VALUE_ROLES = {'Values', 'Value', 'Data', 'Y', 'Y2'}


def follow_one_number(pages, measures, model):
    """One chain per measure a card shows or a table totals, in page order.

    Each chain is the five steps the reader follows: what is on the screen, the
    measure behind it, what that measure does, the tables it reads and where each
    of those tables comes from. A step the evidence cannot establish carries
    `unknown` rather than being left out.
    """
    by_key = {m['key']: m for m in measures or []}
    sources = table_sources(model or {})
    chains = {}
    for page in pages or []:
        for visual in page.get('visuals') or []:
            kind = visual.get('type')
            headline, total = kind in HEADLINE_VISUALS, kind in TOTAL_VISUALS
            if not (headline or total):
                continue
            for field in visual.get('fields') or []:
                if field.get('kind') != 'measure' or not field.get('measure'):
                    continue
                if total and field.get('role') not in VALUE_ROLES:
                    continue
                key = str(field['measure']).strip().lower()
                measure = by_key.get(key)
                if not measure:
                    continue
                chain = chains.get(key)
                if chain is None:
                    tables = [{'name': name, 'anchor': 'table-' + anchor_slug(name),
                               'source': sources.get(name, (NOT_CAPTURED, '', 0))[0]}
                              for name in measure.get('tables') or []]
                    sentence = plain_sentence(measure.get('expression'))
                    chain = chains[key] = {
                        'key': key, 'name': measure['name'], 'anchor': 'chain-' + anchor_slug(key),
                        'measure_anchor': measure['anchor'], 'expression': measure.get('expression'),
                        'captured': bool(measure.get('expression')),
                        'sentence': sentence, 'chips': measure_chips(measure, model),
                        'through': list(measure.get('through') or []), 'tables': tables,
                        'seen': []}
                chain['seen'].append({
                    'label': field.get('label'), 'page': page.get('name'),
                    'page_anchor': page.get('anchor'), 'visual': visual_label(visual),
                    'visual_anchor': visual.get('anchor'),
                    'how': 'on a card' if headline else 'as a table total',
                    'kind': kind})
    return list(chains.values())


# --- rendering --------------------------------------------------------------

def esc(value):
    return html.escape(str(value if value is not None else ''))


def details(summary, body, pre=True):
    inner = f'<pre>{esc(body)}</pre>' if pre else f'<p>{esc(body)}</p>'
    return f'<details><summary>{esc(summary)}</summary>{inner}</details>'


MAX_APPEARANCES = 3
MAX_CHAIN_TABLES = 6


def unknown_step(reason):
    return f'<p class="unknown">{esc(UNKNOWN)} &mdash; {esc(reason)}</p>'


def chain_block(chain):
    """One measure's five-step chain, screen to source, as boxes and arrows."""
    seen = chain['seen']
    shown = seen[:MAX_APPEARANCES]
    items = ''.join(
        f'<li><a href="#{esc(place["visual_anchor"])}">{esc(place["label"] or chain["name"])}</a> '
        f'{esc(place["how"])} on <a href="#{esc(place["page_anchor"])}">{esc(place["page"])}</a></li>'
        for place in shown)
    if len(seen) > len(shown):
        items += f'<li>... and {len(seen) - len(shown)} more place(s).</li>'
    screen = f'<ul>{items}</ul>'

    measure = (f'<p><a href="#{esc(chain["measure_anchor"])}">{esc(chain["name"])}</a></p>')

    if not chain['captured']:
        does = unknown_step('the model capture holds no expression for this measure')
    else:
        does = f'<p>{esc(sentence_case(chain["sentence"]))}</p>'
        if chain['sentence'] == SEE_DEFINITION:
            does += '<p class="src">No pattern this page knows matches the expression, so it is shown ' \
                    'rather than described.</p>'
    if chain['chips']:
        does += '<div class="chips">' + ''.join(f'<span class="chip">{esc(chip)}</span>'
                                                for chip in chain['chips']) + '</div>'
    if chain['through']:
        does += f'<p class="src">Through {esc(", ".join(chain["through"][:6]))}.</p>'
    if chain['expression']:
        does += details('Its definition', chain['expression'])

    tables = chain['tables'][:MAX_CHAIN_TABLES]
    if not chain['captured']:
        reads = unknown_step('without the expression, the tables it reads cannot be read either')
        comes = unknown_step('no table to follow')
    elif not tables:
        reads = unknown_step('the expression names no table this model capture defines')
        comes = unknown_step('no table to follow')
    else:
        more = f'<li>... and {len(chain["tables"]) - len(tables)} more.</li>' \
            if len(chain['tables']) > len(tables) else ''
        reads = '<ul>' + ''.join(f'<li><a href="#{esc(t["anchor"])}">{esc(t["name"])}</a></li>'
                                 for t in tables) + more + '</ul>'
        comes = '<ul>' + ''.join(
            f'<li>{esc(t["name"])}<span class="src">{esc(t["source"])}</span></li>'
            for t in tables) + more + '</ul>'

    arrow = '<span class="arrow" aria-hidden="true">&rarr;</span>'
    steps = arrow.join([
        f'<div class="step"><b>1 &middot; On screen</b>{screen}</div>',
        f'<div class="step"><b>2 &middot; The measure behind it</b>{measure}</div>',
        f'<div class="step wide"><b>3 &middot; What it does</b>{does}</div>',
        f'<div class="step"><b>4 &middot; Tables it reads</b>{reads}</div>',
        f'<div class="step"><b>5 &middot; Where each table comes from</b>{comes}</div>'])
    words = ' '.join([chain['name'], chain['sentence'] or '', ' '.join(t['name'] for t in chain['tables']),
                      ' '.join(f"{p['label'] or ''} {p['page'] or ''}" for p in seen)])
    return (f'<li class="chain" id="{esc(chain["anchor"])}" data-kind="chain" data-find="{esc(words.lower())}">'
            f'<h4><a href="#{esc(chain["anchor"])}">{esc(chain["name"])}</a></h4>'
            f'<p class="seen">Shown as {esc(", ".join(dict.fromkeys(p["label"] or chain["name"] for p in shown)))}'
            f' on {esc(", ".join(dict.fromkeys(p["page"] for p in shown)))}.</p>'
            f'<div class="steps">{steps}</div></li>')


def chains_card(chains, pages):
    """The follow-one-number section, before the page-by-page catalogue."""
    if not pages:
        return ''
    if not chains:
        body = ('<p class="none">No captured card, KPI or table total on these pages binds a measure, so '
                'there is no number to follow from the screen back to a table. That is a gap in the capture, '
                'not a statement about the report.</p>')
    else:
        body = '<ol class="chains">' + ''.join(chain_block(chain) for chain in chains) + '</ol>'
    return ('<section class="card" id="follow"><div class="head"><div><h2>Follow one number</h2>'
            f'<div class="page">{len(chains)} number(s) a card shows or a table totals, traced from the '
            'screen back to the warehouse</div></div></div>'
            '<p class="hint">Read one row left to right: what you see, the measure behind it, what that '
            'measure does in one sentence, the tables it reads and where each table comes from. Every step is '
            'read from the evidence this case captured; a step the evidence cannot establish says '
            f'"{esc(UNKNOWN)}" instead of guessing, and an expression no pattern matches says '
            f'"{esc(SEE_DEFINITION)}" and shows the DAX.</p>'
            '<p class="hint">The amber chips mark steps worth a second look - reading the machine clock, '
            'dropping a filter, dividing without a guard, narrowing to one segment. They are observations '
            'about how the measure is written, not verdicts: any of them can be exactly what was intended.</p>'
            + body + '</section>')


def field_table(visual, measures_by_key):
    if not visual['fields']:
        return '<p class="none">This visual binds no fields in the definition.</p>'
    rows = []
    for field in visual['fields']:
        behind = esc(field['behind'] or NOT_CAPTURED)
        if field['kind'] == 'measure' and field['measure']:
            record = measures_by_key.get(str(field['measure']).strip().lower())
            if record:
                behind = f'<a href="#{esc(record["anchor"])}">{esc(record["name"])}</a>'
            expression = (record or {}).get('expression')
            tail = details('Its definition', expression) if expression \
                else f'<p class="hint">Expression {NOT_CAPTURED}.</p>'
            reads = ', '.join((record or {}).get('tables') or [])
            if reads:
                tail += f'<p class="hint">Reads {esc(reads)}.</p>'
            rows.append(f'<tr><td>{esc(field["label"])}<br><span class="kind">{esc(field["role_word"])}</span></td>'
                        f'<td>measure {behind}{tail}</td></tr>')
            continue
        rows.append(f'<tr><td>{esc(field["label"])}<br><span class="kind">{esc(field["role_word"])}</span></td>'
                    f'<td>{esc(field["kind"])} {behind}</td></tr>')
    return ('<div class="scroll"><table class="fields"><thead><tr><th>On screen</th>'
            '<th>What is behind it</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')


def searchable(*parts):
    """One lower-case haystack for the filter box, from whatever text is worth matching."""
    words = ' '.join(str(part) for part in parts if part)
    return re.sub(r'\s+', ' ', words).strip().lower()


def visual_words(visual, page_name=''):
    """Everything about one visual a reader might type into the filter box."""
    return searchable(page_name, visual_label(visual), visual.get('type'), visual.get('type_raw'),
                      ' '.join(f"{f.get('label') or ''} {f.get('behind') or ''} {f.get('measure') or ''} "
                               f"{f.get('entity') or ''}" for f in visual.get('fields') or []))


def visual_block(visual, measures_by_key, page_name=''):
    notes = []
    if visual['hidden']:
        notes.append('hidden in the definition')
    if visual['type'] in ('custom visual', 'visual') and visual['type_raw']:
        # The plain name lost the only identifying detail; keep the definition's own word.
        notes.append(f"definition type {visual['type_raw']}")
    note = f' <span class="kind">({esc(", ".join(notes))})</span>' if notes else ''
    body = field_table(visual, measures_by_key)
    if visual['filters']:
        items = ''.join(f'<li>{esc(f["text"])}</li>' for f in visual['filters'])
        body += f'<p class="hint">Filters on this visual:</p><ul class="plain">{items}</ul>'
    return (f'<figure class="v{" mute" if visual["hidden"] else ""}" id="{esc(visual["anchor"])}" '
            f'data-kind="visual" data-find="{esc(visual_words(visual, page_name))}">'
            f'<h4>{esc(visual_label(visual))}</h4>'
            f'<div class="kind">{esc(visual["type"])}{note}</div>{body}</figure>')


def interactions_block(page, titles):
    entries = page.get('interactions') or []
    if not entries:
        return '<p class="none">The definition records no per-visual interaction settings for this page; ' \
               'every visual filters the others the way Power BI does by default.</p>'
    named, others = [], 0
    for entry in entries:
        kind = str(entry.get('type') or '')
        source, target = titles.get(str(entry.get('source'))), titles.get(str(entry.get('target')))
        if kind == 'NoFilter':
            named.append(f"{source or entry.get('source')} {INTERACTION_WORDS[kind]} {target or entry.get('target')}.")
        else:
            others += 1
    body = ''
    if named:
        shown = named[:MAX_INTERACTIONS]
        more = f'<li>... and {len(named) - len(shown)} more.</li>' if len(named) > len(shown) else ''
        body += ('<p class="hint">Set not to filter:</p><ul class="plain">'
                 + ''.join(f'<li>{esc(text)}</li>' for text in shown) + more + '</ul>')
    if others:
        body += (f'<p class="hint">{others} other pair(s) are explicitly set to filter or highlight each other; '
                 'the definition records them one by one.</p>')
    return body or '<p class="none">No interaction setting on this page changes the default behaviour.</p>'


def page_card(page, measures_by_key, component_by_page):
    titles = {v['id']: visual_label(v) + f' ({v["type"]})' for v in page['visuals']}
    reviewed = component_by_page.get(str(page['name']).strip().lower())
    header = f'<div class="page">{len(page["visuals"])} visual(s) captured'
    if page['hidden']:
        header += ' · hidden in view mode'
    if reviewed:
        header += f' · reviewed in this case as "{esc(reviewed)}"'
    header += '</div>'
    detail = ''
    if page['filters']:
        detail += ('<p class="hint">Filters on this page:</p><ul class="plain">'
                   + ''.join(f'<li>{esc(f["text"])}</li>' for f in page['filters']) + '</ul>')
    detail += '<p class="hint">How the visuals affect each other:</p>' + interactions_block(page, titles)
    words = searchable(page['name'], ' '.join(visual_words(v) for v in page['visuals']),
                       ' '.join(f['text'] for f in page['filters']))
    return (f'<section class="card" id="{esc(page["anchor"])}" data-kind="page" data-find="{esc(words)}">'
            f'<div class="head"><div><h2>{esc(page["name"])}</h2>{header}</div>'
            '<div class="up"><a href="#top">Back to top</a></div></div>'
            '<h3>What is on the page</h3><div class="vis">'
            + ''.join(visual_block(v, measures_by_key, page['name']) for v in page['visuals'])
            + '</div><details class="pagedetail"><summary>Page filters and how the visuals affect each '
              'other</summary>' + detail + '</details></section>')


def measures_card(measures):
    used = [m for m in measures if m['used']]
    unused = [m for m in measures if not m['used']]
    if not used:
        body = '<p class="none">No captured visual binds a measure.</p>'
    else:
        rows = []
        for measure in used:
            places = ', '.join(dict.fromkeys(f"{u['visual']} ({u['page']})" for u in measure['used_by']))
            expression = measure['expression']
            behind = details('Its definition', expression) if expression \
                else f'<p class="hint">Expression {NOT_CAPTURED}.</p>'
            reads = ', '.join(measure['tables']) or NOT_CAPTURED
            through = f'<br><span class="kind">through {esc(", ".join(measure["through"]))}</span>' if measure['through'] else ''
            sentence = plain_sentence(expression)
            says = f'<div>{esc(sentence_case(sentence))}</div>' if sentence else ''
            words = searchable(measure['name'], places, ', '.join(measure['tables']), sentence or '')
            rows.append(f'<tr id="{esc(measure["anchor"])}" data-kind="measure" data-find="{esc(words)}">'
                        f'<td>{esc(measure["name"])}'
                        + (f'<br><span class="kind">stored in {esc(measure["home_table"])}</span>' if measure['home_table'] else '')
                        + f'</td><td>{esc(places)}</td><td>{says}<span class="kind">Reads {esc(reads)}</span>'
                        + f'{through}{behind}</td></tr>')
        body = ('<div class="scroll"><table><thead><tr><th>Measure</th><th>Used by</th>'
                '<th>What it does, what it reads, and its definition</th></tr></thead><tbody>'
                + ''.join(rows) + '</tbody></table></div>')
    if unused:
        listing = ', '.join(m['name'] for m in unused)
        body += (f'<h3>Defined but not used on the captured pages</h3>'
                 f'<p class="hint">{len(unused)} measure(s) exist in the model that no captured visual binds. '
                 'That is not a defect by itself: another report, a drillthrough or a page this evidence does '
                 'not cover may use them.</p>'
                 + details('List them', listing, pre=False))
    return ('<section class="card" id="measures"><div class="head"><div><h2>Measures</h2>'
            f'<div class="page">{len(used)} used by the captured visuals'
            + (f', {len(unused)} defined and unused' if unused else '') + '</div></div></div>' + body + '</section>')


def tables_card(tables, model):
    if not model.get('captured'):
        body = ('<p class="none">No model capture is attached to this case, so the tables behind the '
                'measures are ' + NOT_CAPTURED + '.</p>')
    elif not tables:
        body = '<p class="none">No captured measure expression names a table of this model.</p>'
    else:
        rows = []
        for table in tables:
            query = details('Its source query', table['query']) if table.get('query') else ''
            rows.append(f'<tr id="{esc(table["anchor"])}" data-kind="table" '
                        f'data-find="{esc(searchable(table["name"], table["source"], ", ".join(table["measures"])))}">'
                        f'<td>{esc(table["name"])}'
                        + ('<br><span class="kind">hidden in the model</span>' if table['hidden'] else '')
                        + f'</td><td>{esc(table["source"])}{query}</td>'
                        f'<td>{esc(", ".join(table["measures"]))}</td></tr>')
        body = ('<div class="scroll"><table><thead><tr><th>Table</th><th>Where it comes from</th>'
                '<th>Measures that read it</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')
    return ('<section class="card" id="tables"><div class="head"><div><h2>Where the numbers come from</h2>'
            '<div class="page">Screen label &rarr; measure &rarr; table &rarr; source, as far as the evidence '
            'records it</div></div></div>'
            '<p class="hint">A measure reads the tables its own definition names, plus the tables of the '
            'measures it calls. Relationships are not followed here: a filter can reach a table this list '
            'does not name.</p>' + body + '</section>')


def report_filters_card(pages):
    """Filters the definition applies to every page of the report, when it declares any."""
    filters = (pages or [{}])[0].get('report_filters') or []
    if not filters:
        return ''
    items = ''.join(f'<li>{esc(f["text"])}</li>' for f in filters)
    return ('<section class="card" id="report-filters"><div class="head"><div>'
            '<h2>Filters on the whole report</h2>'
            '<div class="page">Applied to every page unless a page or visual overrides them</div></div></div>'
            f'<ul class="plain">{items}</ul></section>')


def trace_card(case):
    links = [t for t in case.get('trace') or [] if isinstance(t, dict)]
    if not links:
        return ''
    rows = ''.join(
        f'<tr><td>{esc(link.get("from"))}</td><td>{esc(link.get("to"))}</td>'
        f'<td>{esc(link.get("explanation"))}</td><td>{esc(link.get("confidence"))}</td></tr>'
        for link in links)
    return ('<section class="card" id="trace"><div class="head"><div><h2>What the investigation traced</h2>'
            '<div class="page">Links the case recorded by hand, with the confidence it stated</div></div></div>'
            '<div class="scroll"><table><thead><tr><th>From</th><th>To</th><th>Explanation</th>'
            '<th>Confidence</th></tr></thead><tbody>' + rows + '</tbody></table></div></section>')


def gaps_card(gaps):
    items = ''.join(f'<li>{esc(text)}</li>' for text in gaps)
    return ('<section class="card" id="gaps"><div class="head"><div><h2>What this does not cover</h2>'
            '<div class="page">Read before relying on anything above</div></div></div>'
            f'<ul class="gaps">{items}</ul></section>')


def collect_gaps(case_dir, case, pages, measures, model):
    case_dir = Path(case_dir)
    gaps = []
    if not (case_dir / 'evidence/inventory.json').is_file():
        gaps.append('The case holds no evidence/inventory.json, so no page, visual or field binding could be '
                    'read. Run the investigation step against the project to capture one.')
    elif not pages:
        gaps.append('The inventory records no visual bindings: the report definition was not available as PBIP, '
                    'PBIR or legacy report.json when the case was created.')
    if not model.get('captured'):
        gaps.append('No model capture (evidence/model/TMSCHEMA_*.json) is attached, so measure definitions, '
                    'their tables and the source queries behind those tables are ' + NOT_CAPTURED + '.')
    else:
        missing = [m['name'] for m in measures if m['used'] and not m['expression']]
        if missing:
            gaps.append(f'{len(missing)} measure(s) a visual uses are not in the model capture, so their '
                        'definitions are ' + NOT_CAPTURED + ': ' + ', '.join(missing[:12])
                        + ('...' if len(missing) > 12 else '') + '.')
    empty = [f'{v["title"] or v["id"]} on {page["name"]}' for page in pages for v in page['visuals']
             if not v['fields'] and v['type'] not in ('text box', 'image', 'shape', 'button', 'group of visuals')]
    if empty:
        gaps.append(f'{len(empty)} visual(s) bind no field in the definition, so what they show cannot be '
                    'read from this evidence: ' + ', '.join(empty[:8]) + ('...' if len(empty) > 8 else '') + '.')
    covered = {str(c.get('page') or '').strip().lower() for c in case.get('components') or []}
    if covered:
        uncovered = [p['name'] for p in pages if p['name'].strip().lower() not in covered]
        if uncovered:
            gaps.append(f'{len(uncovered)} page(s) were never opened and captured in this case, only read from '
                        'the definition: ' + ', '.join(uncovered[:8]) + ('...' if len(uncovered) > 8 else '') + '.')
    else:
        gaps.append('No page of this report was opened and captured as a reviewed component in this case; '
                    'everything above is the definition, not the running report.')
    gaps.append('This describes the report as the captured snapshot defines it. It does not prove the numbers '
                'are right, that the deployed report matches this definition, or that a refresh since the '
                'capture left it unchanged.')
    gaps.append('Filter propagation through relationships is not shown: a table not named beside a measure can '
                'still filter it.')
    return gaps


FILTER_SCRIPT = '''<script>
(function () {
  // Anchors must land below the sticky contents, whatever height it wraps to.
  var toc = document.querySelector('.toc');
  function pad() {
    if (toc) {
      document.documentElement.style.setProperty('--sticky', (toc.offsetHeight + 16) + 'px');
    }
  }
  window.addEventListener('resize', pad);
  pad();
})();
(function () {
  var box = document.getElementById('find');
  if (!box) { return; }
  var count = document.getElementById('find-count');
  var empty = document.getElementById('find-empty');
  var clear = document.getElementById('find-clear');
  var targets = Array.prototype.slice.call(document.querySelectorAll('[data-find]'));
  function apply() {
    var query = box.value.trim().toLowerCase();
    var kept = {page: 0, chain: 0, visual: 0, measure: 0, table: 0, toc: 0};
    targets.forEach(function (node) {
      var match = !query || node.getAttribute('data-find').indexOf(query) !== -1;
      node.hidden = !match;
      if (match) {
        var kind = node.getAttribute('data-kind');
        if (kind in kept) { kept[kind] += 1; }
      }
    });
    if (count) {
      count.textContent = query
        ? kept.page + ' page(s), ' + kept.chain + ' trace(s), ' + kept.measure + ' measure(s) match'
        : '';
    }
    if (empty) {
      empty.hidden = !query || kept.page + kept.chain + kept.measure + kept.table > 0;
    }
  }
  box.addEventListener('input', apply);
  if (clear) { clear.addEventListener('click', function () { box.value = ''; apply(); box.focus(); }); }
  window.addEventListener('beforeprint', function () {
    box.value = '';
    apply();
    Array.prototype.forEach.call(document.querySelectorAll('details'), function (node) { node.open = true; });
  });
  apply();
})();
</script>'''


def table_of_contents(pages, has_trace, has_tables, has_chains=False):
    items = []
    if has_chains:
        items.append('<li><a href="#follow">Follow one number</a></li>')
    items += [f'<li data-kind="toc" data-find="{esc(page["name"].lower())}">'
              f'<a href="#{esc(page["anchor"])}">{esc(page["name"])}</a>'
              f'<span class="tag">{len(page["visuals"])} visual(s)</span>'
              + ('<span class="tag warn">hidden</span>' if page['hidden'] else '') + '</li>'
              for page in pages]
    items.append('<li><a href="#measures">Measures</a></li>')
    if has_tables:
        items.append('<li><a href="#tables">Where the numbers come from</a></li>')
    if has_trace:
        items.append('<li><a href="#trace">What the investigation traced</a></li>')
    items.append('<li><a href="#gaps">What this does not cover</a></li>')
    find = ('<div class="find"><label for="find">Filter</label>'
            '<input id="find" type="search" autocomplete="off" '
            'placeholder="Type a page, a visual, a measure or a table - everything else is hidden">'
            '<button type="button" id="find-clear">Clear</button>'
            '<span class="count" id="find-count" role="status"></span></div>')
    return ('<nav class="toc" aria-label="Contents"><b>Jump to</b><ol>' + ''.join(items) + '</ol>'
            + find + '</nav><p class="empty" id="find-empty" hidden>Nothing on this page matches what you '
            'typed. Clear the filter to see everything again.</p>')


def manifest_state(case_dir):
    """(digest, sealed) for the case, or ('unsealed', False) when it carries no manifest."""
    path = Path(case_dir) / 'manifest.json'
    if not path.is_file():
        return 'unsealed', False
    from connections import digest
    return digest(path), True


def render(case_dir, out, title=None):
    """Write the how-it-works page beside the case and return the summary counts."""
    case_dir, out = Path(case_dir).resolve(), Path(out).resolve()
    if out.is_relative_to(case_dir):
        raise ValueError('Use an output outside the case; the case holds evidence, not derived pages')
    case = read_json(case_dir / 'case.json') or {}
    model = load_model(case_dir)
    pages = pages_from_evidence(case_dir)
    measures = measures_used(pages, model)
    tables = tables_for_measures(measures, model)
    chains = follow_one_number(pages, measures, model)
    measures_by_key = {m['key']: m for m in measures}
    component_by_page = {str(c.get('page') or '').strip().lower(): c.get('name')
                         for c in case.get('components') or [] if isinstance(c, dict)}
    gaps = collect_gaps(case_dir, case, pages, measures, model)
    inventory = read_json(case_dir / 'evidence/inventory.json') or {}
    version = plugin_version()
    generator = f'analytics-qa lineage_report {version}'
    sha, sealed = manifest_state(case_dir)
    used = [m for m in measures if m['used']]
    visuals = sum(len(page['visuals']) for page in pages)
    heading = str(title or case.get('target') or case.get('id') or 'How this report works')
    body = ''.join(page_card(page, measures_by_key, component_by_page) for page in pages) \
        or '<section class="card"><p class="none">No page could be read from this case\'s evidence.</p></section>'
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="generator" content="{esc(generator)}"><meta name="analytics-qa-case" content="{esc(case.get('id', ''))}">
<meta name="analytics-qa-manifest-sha256" content="{esc(sha)}">
<title>{{{{ title }}}} - how it works</title><style>{STYLE}</style>
<main id="top" data-generator="{esc(generator)}" data-case-id="{esc(case.get('id', ''))}" data-manifest-sha256="{esc(sha)}">
<h1>{{{{ title }}}}: how it works</h1>
<p class="lead">What each page shows, which measure is behind each figure, which tables those measures read and
where those tables come from. Everything here is read from the evidence this case captured - nothing was queried
to write it, and nothing is inferred beyond what the definitions say.</p>
<p class="notice">This describes the captured snapshot of the report and its model, taken {{{{ captured }}}}. It is a
description, not a verdict: it says how the report is built, not whether its numbers are right.</p>
<div class="summary"><span class="pill"><b>{len(pages)}</b> pages</span><span class="pill"><b>{visuals}</b> visuals</span>
<span class="pill"><b>{len(used)}</b> measures used</span><span class="pill"><b>{len(chains)}</b> numbers traced</span>
<span class="pill"><b>{len(tables)}</b> tables</span>
<span class="pill">{{{{ seal }}}}</span></div>
{{{{ toc }}}}
{{{{ chains }}}}
{{{{ report_filters }}}}
{{{{ pages }}}}
{{{{ measures }}}}
{{{{ tables }}}}
{{{{ trace }}}}
{{{{ gaps }}}}
<p class="manifest">Generated by {esc(generator)} from case {esc(case.get('id', ''))}; evidence manifest {{{{ manifest }}}}.</p>
<a class="totop" href="#top">Back to top</a>
{{{{ script }}}}
</main></html>'''
    rendered = Template(document, autoescape=True).render(
        title=heading, captured=str(inventory.get('captured_at') or 'at an unrecorded time'),
        seal='sealed evidence' if sealed else 'unsealed case', manifest=sha,
        toc=Markup(table_of_contents(pages, bool(case.get('trace')), bool(tables), bool(pages))),
        chains=Markup(chains_card(chains, pages)),
        report_filters=Markup(report_filters_card(pages)),
        pages=Markup(body), measures=Markup(measures_card(measures)),
        tables=Markup(tables_card(tables, model)), trace=Markup(trace_card(case)),
        gaps=Markup(gaps_card(gaps)), script=Markup(FILTER_SCRIPT))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding='utf-8')
    return {'report': str(out), 'case': case.get('id'), 'pages': len(pages), 'visuals': visuals,
            'measures_used': len(used), 'measures_defined': len(measures), 'tables': len(tables),
            'traced': len(chains), 'model_captured': model['captured'], 'manifest_sha256': sha,
            'sealed': sealed, 'gaps': len(gaps), 'generator': generator}


def main():
    parser = argparse.ArgumentParser(description='Build the "how it works" report for a validation case.')
    parser.add_argument('--case', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--title', help='heading for the page; the case target is used when omitted')
    args = parser.parse_args()
    print(json.dumps(render(args.case, args.out, args.title), default=str))


if __name__ == '__main__':
    main()
