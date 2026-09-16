"""Statistical methods for analytics QA, on typed series exported from the engine or warehouse.

Every function is pure, deterministic and returns plain JSON-serialisable
dictionaries with the inputs it used, so a probe receipt can retain the exact
calculation. Nothing here decides business truth: a flagged point is a review
lead that the agent must explain from the source; a clean result is not proof.

Series are lists of {"key": <date or category>, "value": <number or None>};
helpers accept engine rows plus column names and build them.
"""
from collections import defaultdict
import datetime as dt
import math
import statistics

MAD_SCALE = 1.4826  # MAD -> sigma for normal data


def series(rows, key_column, value_column):
    """Build a series from engine rows, dropping rows whose value is None."""
    result = []
    for row in rows:
        value = row.get(value_column)
        result.append({'key': row.get(key_column), 'value': None if value is None else float(value)})
    return result


def as_date(text):
    if isinstance(text, (dt.date, dt.datetime)):
        return text if isinstance(text, dt.date) and not isinstance(text, dt.datetime) else text.date()
    for pattern in ('%Y-%m-%d', '%m/%d/%Y %I:%M:%S %p', '%m/%d/%Y', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return dt.datetime.strptime(str(text).strip(), pattern).date()
        except ValueError:
            continue
    raise ValueError(f'Unrecognised date {text!r}')


def robust_center(values):
    values = [v for v in values if v is not None]
    if len(values) < 3:
        return {'n': len(values), 'median': None, 'mad': None, 'sigma': None}
    median = statistics.median(values)
    mad = statistics.median(abs(v - median) for v in values)
    return {'n': len(values), 'median': median, 'mad': mad, 'sigma': mad * MAD_SCALE}


def robust_zscores(points, threshold=3.5, minimum_points=8):
    """Median/MAD z-scores over one series. Points beyond the threshold are flagged.

    With a zero MAD (many identical values) the score is undefined and the
    method reports 'inconclusive' rather than flagging everything.
    """
    values = [p['value'] for p in points if p['value'] is not None]
    center = robust_center(values)
    if center['n'] < minimum_points:
        return {'method': 'robust_zscores', 'status': 'inconclusive', 'reason': f"only {center['n']} points (< {minimum_points})", 'center': center, 'flagged': []}
    if not center['sigma']:
        return {'method': 'robust_zscores', 'status': 'inconclusive', 'reason': 'zero MAD; values too uniform for a robust scale', 'center': center, 'flagged': []}
    flagged = []
    for p in points:
        if p['value'] is None:
            continue
        z = (p['value'] - center['median']) / center['sigma']
        if abs(z) > threshold:
            flagged.append({'key': p['key'], 'value': p['value'], 'z': round(z, 2)})
    return {'method': 'robust_zscores', 'status': 'flagged' if flagged else 'clean', 'threshold': threshold,
            'center': center, 'flagged': flagged}


def weekday_robust_band(points, threshold=3.5, minimum_per_weekday=4):
    """Day-of-week aware anomaly detection for daily series.

    Each weekday gets its own median/MAD from the other points of that weekday;
    a point is flagged when its robust z-score against its weekday peers exceeds
    the threshold. Weekdays with too few peers are reported as not testable.
    """
    by_weekday = defaultdict(list)
    dated = []
    for p in points:
        if p['value'] is None:
            continue
        day = as_date(p['key'])
        dated.append((day, p['value']))
        by_weekday[day.weekday()].append(p['value'])
    flagged, untestable = [], []
    for day, value in dated:
        peers = [v for v in by_weekday[day.weekday()]]
        if len(peers) < minimum_per_weekday:
            untestable.append(day.isoformat())
            continue
        others = list(peers)
        others.remove(value)
        center = robust_center(others)
        if center['median'] is None:
            continue
        # identical peers give a zero MAD; use a small floor so a spike is still visible
        scale = center['sigma'] or max(1.0, abs(center['median']) * 0.02)
        z = (value - center['median']) / scale
        if abs(z) > threshold:
            flagged.append({'key': day.isoformat(), 'weekday': day.strftime('%A'), 'value': value,
                            'weekday_median': center['median'], 'z': round(z, 2)})
    status = 'inconclusive' if not dated or len(untestable) == len(dated) else ('flagged' if flagged else 'clean')
    return {'method': 'weekday_robust_band', 'status': status, 'threshold': threshold, 'points': len(dated),
            'untestable_days': sorted(set(untestable)), 'flagged': sorted(flagged, key=lambda f: f['key'])}


def ratio_stability(rows, numerator, denominator, key_column, minimum_denominator=30, iqr_multiplier=1.5, upper_bound=None):
    """Daily ratio review with a volume guard and an optional hard bound (e.g. 1.0 for a share).

    Points with denominator below the guard are reported but never flagged.
    Points above the hard bound are flagged as 'bound' regardless of the band.
    """
    ratios = []
    low_volume = []
    for row in rows:
        num, den = row.get(numerator), row.get(denominator)
        if den is None or den == 0 or num is None:
            low_volume.append({'key': row.get(key_column), 'numerator': num, 'denominator': den})
            continue
        ratio = float(num) / float(den)
        if den < minimum_denominator:
            low_volume.append({'key': row.get(key_column), 'numerator': num, 'denominator': den, 'ratio': ratio})
            continue
        ratios.append({'key': row.get(key_column), 'numerator': num, 'denominator': den, 'ratio': ratio})
    flagged = []
    if upper_bound is not None:
        flagged += [dict(r, reason='bound', bound=upper_bound) for r in ratios if r['ratio'] > upper_bound]
    if len(ratios) >= 8:
        values = sorted(r['ratio'] for r in ratios)
        q1, q3 = quantile(values, 0.25), quantile(values, 0.75)
        iqr = q3 - q1
        lo, hi = q1 - iqr_multiplier * iqr, q3 + iqr_multiplier * iqr
        for r in ratios:
            if (r['ratio'] < lo or r['ratio'] > hi) and not any(f['key'] == r['key'] for f in flagged):
                flagged.append(dict(r, reason='band', band=[lo, hi]))
        band = {'q1': q1, 'q3': q3, 'low': lo, 'high': hi}
    else:
        band = {'note': f'{len(ratios)} testable points; band needs 8'}
    return {'method': 'ratio_stability', 'status': 'flagged' if flagged else ('clean' if ratios else 'inconclusive'),
            'minimum_denominator': minimum_denominator, 'band': band, 'testable_points': len(ratios),
            'low_volume_points': low_volume, 'flagged': flagged}


def quantile(sorted_values, q):
    """Inclusive (Excel/DAX PERCENTILE.INC) quantile of an already sorted list."""
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def changepoints(points, minimum_segment=7, minimum_shift_sigma=2.0, max_points=3):
    """Binary segmentation on the mean with a robust noise scale; returns level shifts.

    The noise scale is the median absolute first difference (scaled), which is
    not inflated by the shift itself. A split is accepted when the two segment
    medians differ by more than minimum_shift_sigma noise sigmas. Intended to locate
    tracking breaks or campaign launches, not to prove either.
    """
    values = [(p['key'], p['value']) for p in points if p['value'] is not None]
    if len(values) < 2 * minimum_segment:
        return {'method': 'changepoints', 'status': 'inconclusive', 'reason': f'{len(values)} points (< {2 * minimum_segment})', 'shifts': []}
    diffs = [abs(values[i][1] - values[i - 1][1]) for i in range(1, len(values))]
    noise = statistics.median(diffs) * MAD_SCALE / math.sqrt(2) if diffs else 0
    scale = noise or max(1.0, abs(statistics.median(v for _, v in values)) * 0.02)
    shifts = []

    def split(segment_start, segment_end, depth):
        if depth >= max_points or segment_end - segment_start < 2 * minimum_segment:
            return
        best, best_cost = None, None
        total_cost = sum_squared_deviation(values[segment_start:segment_end])
        for cut in range(segment_start + minimum_segment, segment_end - minimum_segment + 1):
            cost = sum_squared_deviation(values[segment_start:cut]) + sum_squared_deviation(values[cut:segment_end])
            if best_cost is None or cost < best_cost:
                best, best_cost = cut, cost
        if best is None:
            return
        left = statistics.median(v for _, v in values[segment_start:best])
        right = statistics.median(v for _, v in values[best:segment_end])
        if abs(right - left) / scale >= minimum_shift_sigma and best_cost < total_cost:
            shifts.append({'key': values[best][0], 'before_median': left, 'after_median': right,
                           'shift_sigma': round((right - left) / scale, 2)})
            split(segment_start, best, depth + 1)
            split(best, segment_end, depth + 1)

    split(0, len(values), 0)
    shifts.sort(key=lambda s: str(s['key']))
    return {'method': 'changepoints', 'status': 'flagged' if shifts else 'clean', 'scale_sigma': scale, 'shifts': shifts}


def sum_squared_deviation(pairs):
    vals = [v for _, v in pairs]
    if not vals:
        return 0.0
    mean = sum(vals) / len(vals)
    return sum((v - mean) ** 2 for v in vals)


def population_stability(baseline_counts, current_counts, threshold=0.25):
    """Population Stability Index between two categorical distributions (dict label -> count).

    PSI below 0.1 is usually stable, 0.1-0.25 moderate, above 0.25 a material
    shift. Categories absent on one side are floored at a small share so the
    logarithm stays defined; those categories are listed explicitly.
    """
    labels = set(baseline_counts) | set(current_counts)
    base_total = sum(baseline_counts.values()) or 1
    cur_total = sum(current_counts.values()) or 1
    floor = 1e-4
    contributions, new_or_gone = [], []
    psi = 0.0
    for label in sorted(labels, key=str):
        b = baseline_counts.get(label, 0) / base_total
        c = current_counts.get(label, 0) / cur_total
        if label not in baseline_counts or label not in current_counts:
            new_or_gone.append({'label': label, 'baseline_share': b, 'current_share': c})
        b, c = max(b, floor), max(c, floor)
        term = (c - b) * math.log(c / b)
        psi += term
        contributions.append({'label': label, 'baseline_share': round(b, 4), 'current_share': round(c, 4), 'psi': round(term, 4)})
    contributions.sort(key=lambda x: -abs(x['psi']))
    return {'method': 'population_stability', 'status': 'flagged' if psi > threshold else 'clean', 'psi': round(psi, 4),
            'threshold': threshold, 'top_contributors': contributions[:8], 'new_or_missing_categories': new_or_gone}


def additivity(parts, total, tolerance=0.5):
    """Do the parts sum to the total? For additive measures the remainder must be ~0.

    A non-zero remainder points at BLANK members dropped from the breakdown,
    many-to-many double counting, or a visual-level filter on the breakdown.
    """
    parts_sum = sum(float(v) for v in parts.values() if v is not None)
    remainder = float(total) - parts_sum
    return {'method': 'additivity', 'status': 'clean' if abs(remainder) <= tolerance else 'flagged',
            'parts_sum': parts_sum, 'total': float(total), 'remainder': remainder, 'tolerance': tolerance, 'parts': len(parts)}


def ratio_of_totals_vs_mean_of_ratios(rows, numerator, denominator):
    """Show the gap between the correct ratio (sum/sum) and the average of member ratios."""
    num = sum(float(r[numerator]) for r in rows if r.get(numerator) is not None)
    den = sum(float(r[denominator]) for r in rows if r.get(denominator) is not None)
    member = [float(r[numerator]) / float(r[denominator]) for r in rows if r.get(denominator) not in (None, 0) and r.get(numerator) is not None]
    ratio_totals = num / den if den else None
    mean_ratios = sum(member) / len(member) if member else None
    gap = None if ratio_totals is None or mean_ratios is None else mean_ratios - ratio_totals
    return {'method': 'ratio_of_totals_vs_mean_of_ratios', 'ratio_of_totals': ratio_totals, 'mean_of_member_ratios': mean_ratios,
            'gap': gap, 'members': len(member), 'status': 'informational'}


def fan_out(base_rows, joined_rows, distinct_keys=None):
    """Row multiplication through a join or relationship path, and duplicate business keys."""
    result = {'method': 'fan_out', 'base_rows': base_rows, 'joined_rows': joined_rows,
              'multiplier': (joined_rows / base_rows) if base_rows else None}
    if distinct_keys is not None:
        result['distinct_keys'] = distinct_keys
        result['duplicate_rows'] = base_rows - distinct_keys
    flagged = (joined_rows != base_rows) or (distinct_keys is not None and distinct_keys != base_rows)
    result['status'] = 'flagged' if flagged else 'clean'
    return result
