"""Observed Desktop WebView controls. These mechanics do not establish BI correctness.

Every operation here reads the actual DOM before and after acting. Helpers return
what was observed; they never infer a state from the requested action. Business
meaning, experiment design and the choice of discriminating values stay with the
agent and the recorded case.
"""
import datetime as dt
import json
import re
from pathlib import Path

from connections import dump, ensure_evidence_writable
from pbi import check_report_title

SELECTED_CLASS = re.compile(r'(?:^|\s)selected(?:\s|$)')
PARTIAL_CLASS = re.compile(r'(?:^|\s)partiallySelected(?:\s|$)')


def connect_page(playwright, endpoint, page_id, report_title):
    browser = playwright.chromium.connect_over_cdp(endpoint)
    try:
        for context in browser.contexts:
            for page in context.pages:
                if 'reportView' not in page.url:
                    continue
                session = context.new_cdp_session(page)
                try:
                    identity = session.send('Target.getTargetInfo')['targetInfo']['targetId']
                finally:
                    session.detach()
                if identity == page_id:
                    page.set_default_timeout(15000)
                    check_report_title(page.locator('body').inner_text(), report_title)
                    return browser, page
        raise ValueError('Exact report target not found')
    except Exception:
        browser.close()
        raise


def wait_stable(page, rounds=30):
    previous, stable = None, 0
    for _ in range(rounds):
        text = page.locator('body').inner_text()
        stable = stable + 1 if text == previous else 0
        if stable >= 3:
            return
        previous = text
        page.wait_for_timeout(500)
    raise RuntimeError('Report text did not settle; this check alone does not prove data loaded')


def one_visible(locator, label):
    items = [locator.nth(i) for i in range(locator.count()) if locator.nth(i).is_visible()]
    if len(items) != 1:
        raise ValueError(f'{label}: expected one visible element, found {len(items)}')
    return items[0]


def visible_visuals(page):
    """Visible report visual containers with their first text line as a title hint."""
    containers = page.locator('.visualContainer')
    result = []
    for i in range(containers.count()):
        item = containers.nth(i)
        if not item.is_visible():
            continue
        lines = item.inner_text().splitlines()
        result.append({'index': i, 'title': lines[0].strip() if lines else '', 'locator': item})
    return result


def find_visual(page, title):
    matches = [v['locator'] for v in visible_visuals(page) if v['title'] == title]
    if len(matches) != 1:
        raise ValueError(f'Visual title {title!r}: found {len(matches)}; supply a more specific discovered selector')
    return matches[0]


def slicer_caption(slicer):
    return one_visible(slicer.locator('.slicer-restatement'), 'slicer caption').inner_text()


def slicer_state(slicer):
    """Dropdown caption, or the selected option labels of a list/tile slicer, or None."""
    captions = slicer.locator('.slicer-restatement')
    visible = [captions.nth(i) for i in range(captions.count()) if captions.nth(i).is_visible()]
    if len(visible) == 1:
        return visible[0].inner_text()
    options = slicer.locator('[role=option]')
    if options.count():
        selected = [(options.nth(i).get_attribute('aria-label') or options.nth(i).inner_text().strip())
                    for i in range(options.count()) if options.nth(i).is_visible() and options.nth(i).get_attribute('aria-selected') == 'true']
        return 'selected: ' + ', '.join(selected) if selected else 'selected: (none)'
    return None


def select_option(page, slicer, label):
    """Click one option of a list/tile slicer by its aria-label or text and verify aria-selected."""
    dismiss_edit_overlays(page)
    options = slicer.locator('[role=option]')
    before = [{'label': (options.nth(i).get_attribute('aria-label') or options.nth(i).inner_text().strip()),
               'selected': options.nth(i).get_attribute('aria-selected') == 'true'} for i in range(options.count()) if options.nth(i).is_visible()]
    matches = [i for i in range(options.count()) if options.nth(i).is_visible()
               and (options.nth(i).get_attribute('aria-label') or options.nth(i).inner_text().strip()) == label]
    if len(matches) != 1:
        raise ValueError(f'Option {label!r}: found {len(matches)} visible options; observed {[b["label"] for b in before]}')
    target = options.nth(matches[0])
    if target.get_attribute('aria-selected') != 'true':
        target.click()
        page.wait_for_timeout(800)
        wait_stable(page)
    after = [{'label': (options.nth(i).get_attribute('aria-label') or options.nth(i).inner_text().strip()),
              'selected': options.nth(i).get_attribute('aria-selected') == 'true'} for i in range(options.count()) if options.nth(i).is_visible()]
    if not any(o['label'] == label and o['selected'] for o in after):
        raise AssertionError(f'Option {label!r} did not become selected; observed {after}')
    return {'label': label, 'before': before, 'after': after}


def visual_text(state, title):
    """Last non-empty text line of an observed visual (a card's displayed value)."""
    visual = next((v for v in state['visuals'] if v['title'] == title), None)
    if visual is None:
        raise KeyError(f'No observed visual titled {title!r}')
    lines = [l.strip() for l in visual['text'].splitlines() if l.strip()]
    return lines[-1] if lines else ''


def read_date_inputs(slicer):
    """Both observed date-slicer input values. Between-mode slicers expose two inputs."""
    inputs = slicer.locator('input')
    visible = [inputs.nth(i) for i in range(inputs.count()) if inputs.nth(i).is_visible()]
    if len(visible) != 2:
        raise ValueError(f'Date slicer: expected two visible inputs, found {len(visible)}')
    return {'start': visible[0].input_value(), 'end': visible[1].input_value()}


def calendar_open(page):
    body = page.locator('body').inner_text()
    return 'Month Picker' in body or 'Go to today' in body


def close_calendar(page):
    for _ in range(3):
        if not calendar_open(page):
            return
        page.keyboard.press('Escape')
        page.wait_for_timeout(700)
    raise RuntimeError('Calendar overlay did not close with Escape; inspect the DOM before continuing')


def edit_overlay_open(page):
    """Desktop edit-mode floating toolbars (text formatting) can cover report controls."""
    overlays = page.locator('.toolbarOverlay.overlayActive')
    return any(overlays.nth(i).is_visible() for i in range(overlays.count()))


def dismiss_edit_overlays(page):
    """Close a selected-visual formatting toolbar with Escape and verify it is gone."""
    for _ in range(3):
        if not edit_overlay_open(page):
            return
        page.keyboard.press('Escape')
        page.wait_for_timeout(600)
    raise RuntimeError('An edit-mode toolbar overlay stayed open; inspect the DOM instead of clicking through it')


def set_date_range(page, slicer, start=None, end=None):
    """Type new bounds into the observed inputs, choosing edit order from the bounds.

    Moving the window earlier edits the start first; moving it later edits the end
    first, so that no intermediate state has start after end. Both final values are
    asserted from the DOM. Returns before/after observations.
    """
    dismiss_edit_overlays(page)
    before = read_date_inputs(slicer)
    wanted = {'start': start or before['start'], 'end': end or before['end']}
    inputs = slicer.locator('input')
    visible = [inputs.nth(i) for i in range(inputs.count()) if inputs.nth(i).is_visible()]
    order = ['start', 'end'] if _as_date(wanted['start']) < _as_date(before['start']) else ['end', 'start']
    for key in order:
        if wanted[key] == before[key]:
            continue
        control = visible[0 if key == 'start' else 1]
        control.click(click_count=3)
        page.wait_for_timeout(200)
        control.type(wanted[key], delay=40)
        control.press('Enter')
        page.wait_for_timeout(1500)
        close_calendar(page)
        page.keyboard.press('Escape')
        page.wait_for_timeout(500)
        close_calendar(page)
        wait_stable(page)
    after = read_date_inputs(slicer)
    if after != wanted:
        raise AssertionError(f'Date inputs did not reach the requested bounds: {after} != {wanted}')
    return {'before': before, 'after': after, 'edit_order': order}


def _as_date(text):
    for pattern in ('%m/%d/%Y', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return dt.datetime.strptime(text.strip(), pattern)
        except ValueError:
            continue
    raise ValueError(f'Unrecognized date input format: {text!r}')


def selected_data_cells(page):
    """Visible selected table cells or chart marks; screen-reader live regions are excluded."""
    return page.locator('.cell-selected:visible, .selected.bar:visible, .selected.column:visible, .selected.slice:visible').count()


def report_page_tabs(page):
    """Report page tabs (not ribbon tabs): role=tab elements inside the page navigation."""
    tabs = page.locator('[role=tab].section, .pageNavigation [role=tab], [class*="pageNavigation"] [role=tab], [class*="page-tabs"] [role=tab]')
    if not tabs.count():
        tabs = page.locator('[role=tab]')
    result = []
    for i in range(tabs.count()):
        tab = tabs.nth(i)
        if tab.is_visible():
            lines = [l.strip() for l in tab.inner_text().splitlines() if l.strip()]
            # A selected Desktop page tab appends its close glyph as a second line.
            result.append({'name': lines[0] if lines else '', 'selected': tab.get_attribute('aria-selected') == 'true', 'locator': tab})
    return result


def report_pages(page):
    """Names of the report's page tabs (Desktop renders them as [role=tab].section)."""
    tabs = page.locator('[role=tab].section')
    names = []
    for i in range(tabs.count()):
        tab = tabs.nth(i)
        if tab.is_visible():
            lines = [l.strip() for l in tab.inner_text().splitlines() if l.strip()]
            if lines:
                names.append(lines[0])
    if not names:
        raise ValueError('No report page tabs found; is this the Desktop report view?')
    return tuple(names)


def active_page(page, known_pages):
    """The selected report page among the caller-supplied known page names."""
    known = {k.strip() for k in known_pages}
    selected = [t['name'] for t in report_page_tabs(page) if t['selected'] and t['name'].strip() in known]
    if len(selected) != 1:
        raise ValueError(f'Expected one active report page among known pages, found {selected}')
    return selected[0]


def go_to_page(page, name, known_pages):
    name = name.strip()
    if active_page(page, known_pages) == name:
        return name
    matches = [t['locator'] for t in report_page_tabs(page) if t['name'] == name]
    if len(matches) != 1:
        raise ValueError(f'Page tab {name!r}: found {len(matches)}')
    matches[0].click()
    wait_stable(page)
    actual = active_page(page, known_pages)
    if actual != name:
        raise AssertionError(f'Navigation did not activate {name!r}; active page is {actual!r}')
    return actual


def visible_popup(page):
    popups = page.locator('.slicer-dropdown-popup')
    visible = [popups.nth(i) for i in range(popups.count()) if popups.nth(i).is_visible()]
    if len(visible) > 1:
        raise ValueError('Ambiguous open dropdowns')
    return visible[0] if visible else None


def read_visible_members(popup):
    result = []
    items = popup.locator('.slicerItemContainer')
    for i in range(items.count()):
        item = items.nth(i)
        if not item.is_visible():
            continue
        label = one_visible(item.locator('.slicerText'), 'member label').inner_text()
        checkbox = one_visible(item.locator('.slicerCheckbox'), 'member checkbox')
        classes = checkbox.get_attribute('class') or ''
        result.append({'label': label, 'selected': bool(SELECTED_CLASS.search(classes)),
                       'partial': bool(PARTIAL_CLASS.search(classes)),
                       'aria_selected': item.get_attribute('aria-selected')})
    return result


def open_dropdown(page, slicer):
    if visible_popup(page) is not None:
        raise ValueError('An existing popup must be identified and closed before opening another')
    one_visible(slicer.locator('.slicer-dropdown-menu'), 'dropdown trigger').click()
    page.wait_for_timeout(500)
    popup = visible_popup(page)
    if popup is None:
        raise RuntimeError('Dropdown did not open')
    return popup


def close_dropdown(page, slicer):
    if visible_popup(page) is None:
        return
    one_visible(slicer.locator('.slicer-dropdown-menu'), 'dropdown trigger').click()
    page.wait_for_timeout(500)
    if visible_popup(page) is not None:
        raise RuntimeError('Dropdown did not close; no background click fallback')


def _member(popup, label):
    members = popup.locator('.slicerItemContainer')
    matches = [members.nth(i) for i in range(members.count())
               if members.nth(i).is_visible() and members.nth(i).locator('.slicerText').inner_text() == label]
    if len(matches) != 1:
        raise ValueError('Member must be uniquely visible; inspect or scroll before selecting')
    return matches[0]


def set_visible_member(popup, label, selected, allow_other_changes=False, timeout_ms=5000):
    """Bring one visible member to the requested checked state and report side effects.

    The member is re-resolved after the click because Power BI can re-render the
    virtualized list. Other visible members must keep their state unless the caller
    explicitly allows it (for example, a single-select slicer replacing the selection).
    """
    before = read_visible_members(popup)
    target = next((m for m in before if m['label'] == label), None)
    if target is None:
        raise ValueError('Member must be uniquely visible; inspect or scroll before selecting')
    if target['partial']:
        raise ValueError('Partial Select all needs an explicit observed-state transition')
    clicked = False
    if target['selected'] != selected:
        _member(popup, label).locator('.slicerCheckbox').click()
        clicked = True
        deadline = dt.datetime.now() + dt.timedelta(milliseconds=timeout_ms)
        while True:
            current = next((m for m in read_visible_members(popup) if m['label'] == label), None)
            if current and current['selected'] == selected and not current['partial']:
                break
            if dt.datetime.now() > deadline:
                raise AssertionError(f'Member {label!r} did not reach selected={selected}; observed {current}')
            popup.page.wait_for_timeout(250)
    after = read_visible_members(popup)
    # The list is virtualized: members can scroll out of the DOM after a click. Compare only
    # members visible both before and after; 'Select all' legitimately flips to partial.
    others_before = {m['label']: (m['selected'], m['partial']) for m in before if m['label'] not in (label, 'Select all')}
    others_after = {m['label']: (m['selected'], m['partial']) for m in after if m['label'] not in (label, 'Select all')}
    common = others_before.keys() & others_after.keys()
    changed_others = sorted(k for k in common if others_before[k] != others_after[k])
    scrolled_out = sorted(others_before.keys() - others_after.keys())
    if changed_others and not allow_other_changes:
        raise AssertionError(f'Clicking {label!r} also changed {changed_others}; this slicer may replace selections instead of toggling')
    return {'label': label, 'clicked': clicked,
            'before': target, 'after': next(m for m in after if m['label'] == label),
            'other_members_changed': changed_others, 'members_scrolled_out_of_view': scrolled_out,
            'select_all_after': next((m for m in after if m['label'] == 'Select all'), None), 'members_after': after,
            'scope': 'Visible member only; virtualized lists and overall inclusion mode need separate verification.'}


def toggle_member(page, slicer, label, selected, allow_other_changes=False):
    """Open the slicer, set one member, close it and wait for the report to settle."""
    dismiss_edit_overlays(page)
    popup = open_dropdown(page, slicer)
    try:
        result = set_visible_member(popup, label, selected, allow_other_changes)
    finally:
        close_dropdown(page, slicer)
    wait_stable(page)
    result['caption_after'] = slicer_caption(slicer)
    return result


def observe(page, out, report_title, label, slicer_titles=(), date_slicer_title='Date',
            known_pages=(), extra=None):
    """Capture screen, DOM and observed control state into a fresh directory.

    The observation records what is on screen: report title, active page, slicer
    captions, both date bounds, selected data cells, overlays and every visible
    visual's text. It never records the requested action as if it were the result.
    """
    out = Path(out)
    ensure_evidence_writable(out)
    if out.exists():
        raise ValueError('Observation directory exists; use a fresh directory')
    check_report_title(page.locator('body').inner_text(), report_title)
    wait_stable(page)
    slicers = {}
    for title in slicer_titles:
        slicers[title] = slicer_state(find_visual(page, title))
    dates = read_date_inputs(find_visual(page, date_slicer_title)) if date_slicer_title else None
    visuals = []
    for item in visible_visuals(page):
        box = item['locator'].bounding_box() or {}
        visuals.append({'index': item['index'], 'title': item['title'],
                        'text': item['locator'].inner_text()[:4000],
                        'rect': {k: round(box.get(k, 0)) for k in ('x', 'y', 'width', 'height')}})
    state = {'kind': 'render', 'timestamp': dt.datetime.now(dt.timezone.utc).isoformat(),
             'verified_report_title': report_title, 'label': label,
             'active_page': active_page(page, known_pages) if known_pages else None,
             'slicers': slicers, 'date_start': dates['start'] if dates else None,
             'date_end': dates['end'] if dates else None,
             'cell_selected_count': selected_data_cells(page), 'calendar_open': calendar_open(page),
             'popup_open': visible_popup(page) is not None, 'edit_overlay_open': edit_overlay_open(page),
             'visuals': visuals,
             'screenshot': 'screen.png', 'settled': True,
             'note': 'Observed WebView state. Text stability is not proof every visual finished; interpret values against the cited oracle.'}
    if extra:
        state.update(extra)
    out.mkdir(parents=True)
    page.screenshot(path=str(out / 'screen.png'))
    (out / 'dom.html').write_text(page.content(), encoding='utf-8')
    dump(out / 'state.json', state)
    return state


def visual_value(state, title, line_index=None):
    """Numeric value from an observed visual: a card shows its value below its title."""
    visual = next((v for v in state['visuals'] if v['title'] == title), None)
    if visual is None:
        raise KeyError(f'No observed visual titled {title!r}')
    lines = [l.strip() for l in visual['text'].splitlines() if l.strip()]
    text = lines[line_index] if line_index is not None else lines[-1]
    return parse_number(text)


def parse_number(text):
    cleaned = text.replace(',', '').replace('$', '').replace('%', '').strip()
    if re.fullmatch(r'-?\d+', cleaned):
        return int(cleaned)
    if re.fullmatch(r'-?\d*\.\d+', cleaned):
        return float(cleaned)
    raise ValueError(f'Not a plain number: {text!r}')


def table_total(state, title_contains, value_offset=1):
    """Grand-total value that follows the 'Total' line in an observed table's text."""
    for visual in state['visuals']:
        if title_contains in visual['text']:
            lines = [l.strip() for l in visual['text'].splitlines()]
            if 'Total' in lines:
                index = lines.index('Total')
                return parse_number(lines[index + value_offset])
    raise KeyError('No table with a Total row matching ' + title_contains)
