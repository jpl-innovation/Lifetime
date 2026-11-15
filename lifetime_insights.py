"""What true awake time lets Lifetime say that other tools cannot: energy and cost, after-hours waste, replacement
forecasts and the inputs of the health score.

Pure and stdlib only: the desktop app, the server and both report writers call the same functions with whatever the
computer shares. Nothing here measures anything. Power is *measured* only when the computer reported its own draw
(the battery's discharge rate, which on battery is the whole computer's draw); otherwise it is a typical wattage the
administrator set or the default for the kind of computer, and every result says which (``method``). A missing input
gives "unknown" or "not enough data", never a guess.
"""
import datetime as dt
import math
import re

LAPTOP_WATTS, DESKTOP_WATTS = 20.0, 80.0    # typical draw: the middle of 15-25 W (laptops) and 60-100 W (desktops)
WATTS_RANGE = (1.0, 2000.0)
MIN_MEASURED = 3                            # discharge readings before their mean counts as a measurement
SOURCE_TEXT = {'battery': 'measured on battery', 'computer': 'estimated: set for this computer',
               'group': 'estimated: set for its group', 'typical-laptop': 'estimated: typical laptop',
               'typical-desktop': 'estimated: typical desktop', 'typical-unknown': 'estimated: kind unknown, typical desktop'}
ENERGY_NOTE = ('Energy = awake hours × power draw. Power is measured only when the computer reported its own draw on '
               'battery; otherwise it is a typical wattage, and it says estimated. Computers that do not share their '
               'awake history are left out and counted as unknown.')
DEFAULTS = dict(price=2500.0, currency='VND', laptop_watts=LAPTOP_WATTS, desktop_watts=DESKTOP_WATTS,
                group_watts={}, device_watts={})


def _watts(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not WATTS_RANGE[0] <= value <= WATTS_RANGE[1]:
        raise ValueError('Wattage must be between 1 and 2000 W')
    return float(value)


def _overrides(value):
    if not isinstance(value, dict) or len(value) > 5000:
        raise ValueError('Invalid wattage overrides')
    result = {}
    for key, watts in value.items():
        if not isinstance(key, str) or len(key) > 128 or any(ord(c) < 32 for c in key):
            raise ValueError('Invalid wattage override name')
        result[key.casefold()] = _watts(watts)
    return result


def settings(value=None):
    """The energy settings with defaults filled in; ValueError for anything out of range (the server refuses it)."""
    result = dict(DEFAULTS, group_watts={}, device_watts={})
    for key, item in (value or {}).items():
        if key not in DEFAULTS:
            continue
        if key == 'price':
            if type(item) not in (int, float) or not math.isfinite(item) or not 0 <= item <= 1e9:
                raise ValueError('Invalid electricity price')
            result[key] = float(item)
        elif key == 'currency':
            if not isinstance(item, str) or not re.fullmatch(r'[A-Z]{3}', item):
                raise ValueError('Currency must be a three-letter code such as VND')
            result[key] = item
        elif key in ('laptop_watts', 'desktop_watts'):
            result[key] = _watts(item)
        else:
            result[key] = _overrides(item)
    return result


def discharge_readings(values):
    """Valid on-battery power readings (watts) from a list that may hold None or nonsense."""
    return [float(v) for v in values or () if type(v) in (int, float) and math.isfinite(v) and 0 < v <= WATTS_RANGE[1]]


def power(kind, discharge=(), device_id='', group='', config=None):
    """The wattage used for one computer and how it was obtained.

    method 'measured': the mean of at least MIN_MEASURED on-battery discharge readings (source 'battery').
    method 'estimated': the administrator's wattage for this computer ('computer') or its group ('group'), else the
    typical laptop or desktop wattage ('typical-laptop', 'typical-desktop'); a computer of unknown kind is treated as a
    desktop ('typical-unknown'), the higher figure, and says so."""
    config = config or settings()
    readings = discharge_readings(discharge)
    if len(readings) >= MIN_MEASURED:
        return dict(watts=round(sum(readings) / len(readings), 1), method='measured', source='battery', readings=len(readings))
    for source, table, key in (('computer', config['device_watts'], device_id), ('group', config['group_watts'], group)):
        if key and key.casefold() in table:
            return dict(watts=table[key.casefold()], method='estimated', source=source, readings=len(readings))
    if kind == 'laptop':
        return dict(watts=config['laptop_watts'], method='estimated', source='typical-laptop', readings=len(readings))
    return dict(watts=config['desktop_watts'], method='estimated',
                source='typical-desktop' if kind == 'desktop' else 'typical-unknown', readings=len(readings))


def kwh(seconds, watts):
    """Energy for this much awake time at this draw: seconds / 3600 h x watts / 1000."""
    return seconds / 3600 * watts / 1000


def energy(daily, watts, today, price):
    """kWh and cost for today, the last 7 and the last 30 days (each including today), plus the 30 daily values.

    ``daily``: {local date 'YYYY-MM-DD': awake seconds}. A day with no record counts as nothing awake, and
    ``days_known`` says how many of the 30 had one."""
    days = [(today - dt.timedelta(days=offset)).isoformat() for offset in range(29, -1, -1)]
    values = {day: kwh(daily[day], watts) for day in days if day in daily}
    result = dict(daily={day: round(values.get(day, 0.0), 4) for day in days}, days_known=len(values))
    for name, count in (('today', 1), ('week', 7), ('month', 30)):
        total = sum(values.get(day, 0.0) for day in days[-count:])
        result[name] = dict(kwh=round(total, 3), cost=round(total * price, 2),
                            awake_hours=round(sum(daily.get(day, 0) for day in days[-count:]) / 3600, 2))
    return result


def money(value, currency):
    """1,234,567 VND; other currencies keep two decimals (12.50 USD)."""
    if value is None:
        return None
    return (f'{value:,.0f}' if currency == 'VND' else f'{value:,.2f}') + ' ' + currency


def summarize(items):
    """Totals for a group or the fleet from per-computer results (see ``computer``): sums of kWh and cost, how many
    computers have a measured draw, and how many have no shared awake history (left out, never counted as zero)."""
    known = [item for item in items if item['energy'] is not None]
    total = dict(computers=len(items), known=len(known), unknown=len(items) - len(known),
                 measured=sum(item['power']['method'] == 'measured' for item in known))
    for name in ('today', 'week', 'month'):
        total[name] = dict(kwh=round(sum(item['energy'][name]['kwh'] for item in known), 3),
                           cost=round(sum(item['energy'][name]['cost'] for item in known), 2))
    daily = {}
    for item in known:
        for day, value in item['energy']['daily'].items():
            daily[day] = round(daily.get(day, 0.0) + value, 4)
    total['daily'] = dict(sorted(daily.items()))
    return total


def computer(item, config, today):
    """One computer's results from what it shares (lifetime_server_store.Store.insight_inputs shape, which the desktop
    app builds for itself too)."""
    watts = power(item.get('kind'), item.get('discharge'), item.get('device_id', ''), item.get('group', ''), config)
    history = item.get('history')
    return dict(device_id=item.get('device_id'), name=item.get('name'), group=item.get('group') or '', power=watts,
                energy=energy(history['daily'], watts['watts'], today, config['price']) if history else None)


def fleet(items, config, today):
    """Every computer, each group and the whole fleet, plus the top consumers of the last 30 days."""
    computers = [computer(item, config, today) for item in items]
    groups = {}
    for result in computers:
        groups.setdefault(result['group'], []).append(result)
    ranked = sorted((c for c in computers if c['energy']), key=lambda c: (-c['energy']['month']['kwh'], c['name'] or ''))
    return dict(settings=config, today=today.isoformat(), computers=computers, fleet=summarize(computers),
                groups={name: summarize(members) for name, members in sorted(groups.items())},
                top=[c['device_id'] for c in ranked[:10]])
