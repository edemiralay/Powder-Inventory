#!/usr/bin/env python3
"""Powder inventory DSS aligned with the methodology and Appendix A.
Run: python powder_inventory_app.py; tests: python -m unittest discover -v.
All uncertain-demand alternatives use the same daily simulation.
"""
import argparse
import base64
import copy
import io
import json
import math
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import numpy as np
from scipy.stats import norm, poisson, truncnorm, t as student_t

POLICIES = ('synchronized', 'simultaneous', 'independent')
MODEL_NAMES = {'rop': 'Reorder Point', 'outl': 'Order-Up-To-Level'}
VERSION = '8.0'
ADI_CUTOFF = 1.32
CV2_CUTOFF = 0.49

DEFAULT = {'mode': 'uncertain', 'monthly_demand': [1000, 900, 1100, 1200, 800, 1000, 950, 1050, 1150, 850, 1000, 1000], 'demand': {'family': 'Normal', 'mu': 1000, 'sigma': 200, 'lam': 1000, 'q': 0.5, 'unit': 1, 'p': 1, 'occurrence_probability': None}, 'costs': {'h': 0.2, 'cb': 50, 'ce': 0, 'tc': 0.002, 'te': 0, 'scrap': 12000, 'yield': 0.8, 'scrap_price': 0, 'scrap_distance': 0, 'amortization': 10, 'service': 0.95}, 'sources': [{'id': 1, 'name': 'On-site recycled production', 'lead': 2, 'cp': 10, 'ep': 0, 'co': 100, 'distance': 0, 'capacity': None, 'investment': 12000}, {'id': 2, 'name': 'Purchased recycled powder', 'lead': 1, 'cp': 14, 'ep': 0, 'co': 60, 'distance': 200, 'capacity': None, 'investment': 0}, {'id': 3, 'name': 'Purchased commercial powder', 'lead': 1, 'cp': 18, 'ep': 0, 'co': 50, 'distance': 300, 'capacity': None, 'investment': 0}], 'settings': {'adi_cutoff': 1.32, 'cv2_cutoff': 0.49, 'max_review': 6, 'samples': 20000, 'seed': 42, 'allow_pre_horizon': True, 'coordination': 'compare', 'search_candidates': 60, 'simulation_years': 8, 'replications': 5, 'warmup_years': 2, 'training_replications': 2, 'min_review_days': 1, 'max_review_days': 180}}

def source_configurations(sources):
    """Preserve the sourcing scope: each source alone, or on-site + one supplier."""
    ids=sorted(s['id'] for s in sources)
    return tuple((j,) for j in ids)+tuple((1,j) for j in ids if j!=1)

def number(x, name, low=0, strict=False):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValueError(f'{name} must be a finite number.')
    if x < low or (strict and x == low):
        raise ValueError(f'{name} must be {">" if strict else ">="} {low}.')
    return float(x)

def wagner_whitin(demand, unit_cost, setup, holding_rate):
    """Exact uncapacitated DP, receipts at month start, holding at month end."""
    n = len(demand)
    dp = [math.inf]*(n+1)
    choice = [None]*n
    dp[n] = 0.
    for t in range(n-1,-1,-1):
        if demand[t] == 0:
            dp[t], choice[t] = dp[t+1], None
            continue
        quantity, holding = 0., 0.
        for end in range(t,n):
            quantity += demand[end]
            holding += demand[end]*(end-t)*unit_cost*holding_rate/12
            value = setup+unit_cost*quantity+holding+dp[end+1]
            if value < dp[t]: dp[t], choice[t] = value, end
    lots, t = [], 0
    while t < n:
        end = choice[t]
        if end is None: t += 1; continue
        lots.append({'receipt_month':t+1, 'covers_through_month':end+1,
                     'quantity_kg':float(sum(demand[t:end+1]))})
        t = end+1
    return dp[0], lots

def allocation_endpoints(sources, annual, c):
    upper = []
    for s in sources:
        u = 1. if s['capacity'] is None else min(1., s['capacity']/annual)
        if s['id'] == 1: u = min(u, c['yield']*c['scrap']/annual)
        upper.append(u)
    if len(sources) == 1: return [[1.]] if upper[0] >= 1-1e-12 else []
    lo, hi = max(0.,1-upper[1]), min(1.,upper[0])
    if lo > hi+1e-12: return []
    return [[f,1-f] for f in sorted({lo,hi}) if 0 < f < 1]

def import_excel(encoded):
    from openpyxl import load_workbook
    raw=base64.b64decode(encoded,validate=True)
    if len(raw)>10*1024*1024: raise ValueError('Workbook is too large (10 MB limit).')
    wb=load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
    result={}
    try:
        common={row[0]:row[1] for row in wb['Parameters'].iter_rows(min_row=6,values_only=True) if row[0] is not None}
        mapping={'holding_rate_per_year':'h','shortage_eur_per_kg':'cb','carbon_eur_per_emission_unit':'ce',
            'transport_eur_per_kg_km':'tc','transport_emissions_per_kg_km':'te','scrap_available_kg_per_year':'scrap',
            'powder_yield':'yield','scrap_sale_eur_per_kg':'scrap_price','scrap_recycler_distance_km':'scrap_distance',
            'amortization_years':'amortization','immediate_fill_rate_min':'service'}
        result['costs']={to:common[key] for key,to in mapping.items() if key in common}
        result['import_warnings'] = ['Legacy cycle service level was not imported as an immediate fill-rate target; review the service input.'] if 'cycle_service_level' in common and 'immediate_fill_rate_min' not in common else []
        result['demand']={}
        for key,to in [('mu_monthly_kg','mu'),('sigma_monthly_kg','sigma'),('lambda_monthly','lam'),('demand_unit_kg','unit')]:
            if key in common: result['demand'][to]=common[key]
        result['demand']['family']='Poisson' if 'lambda_monthly' in common else 'Normal'
        sources=[{'id':i} for i in (1,2,3)]
        mapping={'name':'name','lead_months':'lead','unit_cost_eur_per_kg':'cp','powder_emissions_per_kg':'ep',
            'order_cost_eur':'co','powder_distance_km':'distance','capacity_kg_per_year':'capacity','investment_eur':'investment'}
        for row in wb['Sources'].iter_rows(min_row=6,values_only=True):
            if row[0] in mapping:
                for i,s in enumerate(sources): s[mapping[row[0]]]=row[i+2]
        result['sources']=sources
        for block in [result['costs'],result['demand'],*sources]:
            if any(isinstance(v,str) and v.startswith('=') for v in block.values()):
                raise ValueError('Excel input cells must contain explicit values, not formulas.')
        return result
    finally: wb.close()

def integer(value, label, low, high):
    x = number(value, label, low=low)
    if x != int(x) or x > high:
        raise ValueError(f'{label} must be an integer from {low} to {high}.')
    return int(x)


def base_demand_stats(d):
    """Direct-distribution and conditional positive-size population moments."""
    family = d.get('family')
    if family == 'Normal':
        mu = d.get('mu')
        if isinstance(mu, bool) or not isinstance(mu, (float, int)) or not math.isfinite(mu):
            raise ValueError('Underlying Normal mean must be finite.')
        sigma = number(d.get('sigma'), 'Underlying Normal standard deviation')
        if sigma == 0:
            if mu <= 0:
                raise ValueError('A constant demand size must be positive.')
            mean, variance = float(mu), 0.
        else:
            mean, variance = map(float, truncnorm.stats(-mu/sigma, np.inf,
                loc=mu, scale=sigma, moments='mv'))
        probability = 1.
    elif family == 'Poisson':
        lam = number(d.get('lam'), 'Monthly Poisson lambda', strict=True)
        unit = number(d.get('unit'), 'Powder quantity per count', strict=True)
        mean, variance = unit*lam, unit*unit*lam
        probability = -math.expm1(-lam)
    elif family == 'Geometric':
        q = number(d.get('q'), 'Geometric q', strict=True)
        if q >= 1:
            raise ValueError('Geometric q must be below one.')
        unit = number(d.get('unit'), 'Powder quantity per count', strict=True)
        mean, variance = unit*(1-q)/q, unit*unit*(1-q)/(q*q)
        probability = 1-q
    else:
        raise ValueError('Choose Normal, Poisson, or Geometric.')
    positive_mean = mean/probability
    positive_var = max(0., (variance+mean*mean)/probability-positive_mean**2)
    if not all(math.isfinite(x) for x in (mean, variance, positive_mean, positive_var)) or mean <= 0:
        raise ValueError('Demand parameters must produce finite moments and positive mean demand.')
    return dict(mean=mean, sd=math.sqrt(max(variance, 0.)),
        positive_mean=positive_mean, positive_sd=math.sqrt(positive_var),
        positive_probability=probability, ADI=1/probability,
        CV2=positive_var/positive_mean**2)


def demand_stats(d):
    s = base_demand_stats(d)
    if d.get('positive_component'):
        p = number(d.get('p'), 'Monthly positive-demand probability', strict=True)
        if p > 1:
            raise ValueError('Monthly positive-demand probability must not exceed one.')
        mean, variance = s['positive_mean'], s['positive_sd']**2
        s.update(mean=p*mean, sd=math.sqrt(p*variance+p*(1-p)*mean**2),
                 positive_probability=p, ADI=1/p)
    return s


def characterize_inputs(d, settings):
    """Classification and simulated demand always use the same occurrence law."""
    base = base_demand_stats(d)
    # Classification is fixed by the study methodology, not a user setting.
    cutoff, cv_cutoff = ADI_CUTOFF, CV2_CUTOFF
    occurrence = d.get('occurrence_probability')
    if occurrence is None and d.get('positive_component'):
        occurrence = d.get('p')
    if occurrence is None and d.get('p', 1) != 1:
        # Accept prior API activation probabilities, preserving their positive-month rate.
        occurrence = number(d['p'], 'Occurrence multiplier', strict=True)*base['positive_probability']
    observed = occurrence is not None
    if not observed:
        occurrence = base['positive_probability']
    occurrence = number(occurrence, 'Fraction of months with positive demand', strict=True)
    if occurrence > 1:
        raise ValueError('Positive-month fraction must not exceed one.')
    frequent = 1/occurrence < cutoff
    if frequent and not math.isclose(occurrence, base['positive_probability'], abs_tol=1e-8, rel_tol=1e-8):
        raise ValueError(
            f'Direct {d["family"]} demand implies a positive-month fraction of '
            f'{base["positive_probability"]:.8g}, but {occurrence:.8g} was supplied. '
            'Revise the distribution parameters or leave occurrence blank. '
            'The same occurrence probability must be used for classification and simulation.')
    low = base['CV2'] < cv_cutoff
    prepared = dict(d, p=occurrence if not frequent else 1., positive_component=not frequent)
    actual = demand_stats(prepared)
    detail = ('Direct monthly distribution without an additional Bernoulli variable.' if frequent else
        'Bernoulli monthly occurrence with strictly positive sizes; count sizes are conditioned on positive counts.')
    return dict(actual, category=('Smooth' if low else 'Erratic') if frequent else ('Intermittent' if low else 'Lumpy'),
        model=MODEL_NAMES['rop' if frequent else 'outl'],
        distribution=d['family'] if frequent else 'Bernoulli–positive '+d['family'],
        occurrence_probability=occurrence, modeled_positive_probability=actual['positive_probability'],
        occurrence_origin='Provided positive-month frequency.' if observed else 'Distribution-implied frequency.',
        detail=detail, prepared_demand=prepared)


def characterize(d, settings):
    return characterize_inputs(d, settings)


def monthly_paths(d, months, reps, seed):
    """Monthly iid demand. Bernoulli is applied once per month to positive sizes."""
    rng = np.random.default_rng(seed)
    shape = (reps, months)
    if d['family'] == 'Normal':
        if d['sigma'] == 0:
            values = np.full(shape, d['mu'], dtype=float)
        else:
            values = truncnorm.rvs(-d['mu']/d['sigma'], np.inf, loc=d['mu'],
                                  scale=d['sigma'], size=shape, random_state=rng)
    elif d['family'] == 'Poisson':
        if d.get('positive_component'):
            # Inverse survival avoids rejection loops when lambda is very small.
            pos = -math.expm1(-d['lam'])
            survival = np.maximum((1-rng.random(shape))*pos, np.finfo(float).tiny)
            values = np.maximum(1., poisson.isf(survival, d['lam']))*d['unit']
        else:
            values = rng.poisson(d['lam'], shape)*d['unit']
    else:
        offset = 0 if d.get('positive_component') else 1
        values = (rng.geometric(d['q'], shape)-offset)*d['unit']
    if d.get('positive_component'):
        values = values*(rng.random(shape) < d['p'])
    return np.asarray(values, dtype=float)


def validate(payload):
    if payload.get('mode') not in ('known', 'uncertain'):
        raise ValueError('Choose whether monthly demand is known or uncertain.')
    c, settings = payload['costs'], payload['settings']
    for key in ('h', 'yield', 'amortization'):
        number(c[key], key, strict=True)
    if c['yield'] > 1:
        raise ValueError('Powder yield must not exceed one.')
    for key in ('cb', 'ce', 'tc', 'te', 'scrap', 'scrap_price', 'scrap_distance'):
        number(c[key], key)
    if payload['mode'] == 'uncertain':
        target = number(c['service'], 'Immediate fill-rate target')
        if not 0 < target < 1:
            raise ValueError('Immediate fill-rate target must be strictly between zero and one.')
        characterize_inputs(payload['demand'], settings)
    sources = payload['sources']
    if not isinstance(sources, list) or not 3 <= len(sources) <= 10:
        raise ValueError('Enter between 3 and 10 sources.')
    ids = [s['id'] for s in sources]
    if any(isinstance(i, bool) or not isinstance(i, int) or not 1 <= i <= 10 for i in ids) or len(set(ids)) != len(ids) or not {1, 2, 3}.issubset(ids):
        raise ValueError('Use unique source IDs from 1 to 10, including 1, 2 and 3.')
    for s in sources:
        for key in ('lead', 'cp', 'ep', 'co', 'distance', 'investment'):
            number(s[key], f'Source {s["id"]}: {key}')
        days = s['lead']*30
        if s['lead'] > 24 or not math.isclose(days, round(days), abs_tol=1e-7):
            raise ValueError('Lead times are entered in 30-day months and must correspond to a whole number of days, up to 24 months.')
        if not isinstance(s['name'], str) or not s['name'].strip():
            raise ValueError('Every source needs a name.')
        if s['cp']+s['ep']*c['ce'] <= 0 or s['co'] <= 0:
            raise ValueError('Effective powder costs and setup costs must be positive.')
        if s.get('capacity') is not None:
            number(s['capacity'], 'Expected annual source capacity')
    if payload['mode'] == 'known':
        demands = payload['monthly_demand']
        if not isinstance(demands, list) or not 1 <= len(demands) <= 60:
            raise ValueError('Enter 1–60 monthly demand quantities.')
        for x in demands:
            number(x, 'Monthly demand')
        if sum(demands) <= 0:
            raise ValueError('Total known demand must be positive.')
    else:
        integer(settings.get('search_candidates', 60), 'Search candidates', 10, 500)
        integer(settings.get('simulation_years', 8), 'Evaluation years', 1, 50)
        integer(settings.get('replications', 5), 'Validation replications', 2, 30)
        integer(settings.get('training_replications', 2), 'Search replications', 1, 10)
        integer(settings.get('warmup_years', 2), 'Warm-up years', 0, 20)
        lo = integer(settings.get('min_review_days', 1), 'Minimum review interval', 1, 720)
        hi = integer(settings.get('max_review_days', settings.get('max_review', 6)*30), 'Maximum review interval', 1, 720)
        if lo > hi:
            raise ValueError('Minimum review interval must not exceed maximum review interval.')
        if settings.get('coordination', 'compare') not in (*POLICIES, 'compare'):
            raise ValueError('Choose a valid replenishment coordination policy.')
        integer(settings.get('seed', 42), 'Random seed', 0, 2**31-1)
        for key in ('quantity_min_kg', 'quantity_max_kg', 'reorder_max_kg', 'target_max_kg'):
            if settings.get(key) is not None:
                number(settings[key], key, strict=True)
        if settings.get('quantity_min_kg') and settings.get('quantity_max_kg') and settings['quantity_min_kg'] > settings['quantity_max_kg']:
            raise ValueError('Minimum quantity must not exceed maximum quantity.')
def simulate_inventory_batch(payload, src, policy, shares, levels, paths, *,
                             model='rop', quantities=None, reviews=None, trace=False):
    """Evaluate candidates together on common paths, following Appendix A.

    Candidate arrays have shape (candidates, sources), except common levels and
    review intervals, which have one column. Daily operations are vectorized
    over candidates and replications. Pipeline storage is a finite ring buffer.
    """
    if policy not in POLICIES or model not in MODEL_NAMES:
        raise ValueError('Unknown model or coordination policy.')
    paths = np.asarray(paths, dtype=float)
    if paths.ndim != 2 or not np.all(np.isfinite(paths)) or np.any(paths < 0):
        raise ValueError('Demand paths must be a finite nonnegative matrix.')
    w = np.atleast_2d(np.asarray(shares, dtype=float))
    count, sources = w.shape
    if sources != len(src) or np.any(w <= 0) or not np.allclose(w.sum(axis=1), 1):
        raise ValueError('Each candidate needs positive shares summing to one.')
    level = np.atleast_2d(np.asarray(levels, dtype=float))
    independent = policy == 'independent'
    controls = sources if independent else 1
    if level.shape != (count, controls) or not np.all(np.isfinite(level)) or np.any(level < 0):
        raise ValueError('Invalid source-specific or common replenishment levels.')
    if model == 'rop':
        quantity = np.atleast_2d(np.asarray(quantities, dtype=float))
        if quantity.shape != w.shape or not np.all(np.isfinite(quantity)) or np.any(quantity <= 0):
            raise ValueError('Batch quantities must be positive and match the selected sources.')
        if not independent and not np.allclose(quantity, w*quantity.sum(axis=1)[:, None]):
            raise ValueError('SYN and SIM batch quantities must follow the selected source shares.')
    else:
        review = np.atleast_2d(np.asarray(reviews, dtype=float))
        if review.shape != level.shape or not np.all(np.isfinite(review)) or np.any(review < 1) or np.any(review != np.floor(review)):
            raise ValueError('Review intervals must be positive integer daily periods.')
        review = review.astype(int)
    c, settings = payload['costs'], payload['settings']
    reps, months = paths.shape
    warm_days = int(settings.get('warmup_years', 2)*360)
    duration = months*30
    if duration <= warm_days:
        raise ValueError('Demand paths must extend beyond the warm-up period.')
    eval_days = duration-warm_days
    years = eval_days/360
    demand_observed = paths[:, warm_days//30:].sum(axis=1)
    if demand_observed.sum() <= 0:
        raise ValueError('No positive demand occurred after warm-up. Increase evaluation years or replications.')
    lead = np.array([round(s['lead']*30) for s in src], dtype=int)
    delay = np.full(sources, max(lead)) if policy == 'synchronized' else lead
    ring = np.zeros((int(max(delay))+1, count, reps, sources))
    # Empty initial pipeline; virtual accounts allocate the same initial stock.
    initial = level.sum(axis=1)
    net = np.broadcast_to(initial[:, None], (count, reps)).copy()
    position = (w[:, None, :]*net[:, :, None]) if independent else net.copy()
    stock_sum = np.zeros((count, reps))
    backlog_sum = np.zeros_like(stock_sum)
    unmet = np.zeros_like(stock_sum)
    ordered = np.zeros((count, reps, sources))
    orders = np.zeros_like(ordered)
    all_receipts = np.zeros_like(stock_sum)
    all_demand = np.zeros_like(stock_sum)
    all_orders = np.zeros_like(ordered)
    traces = []
    for day in range(duration):
        slot = day % len(ring)
        received = ring[slot].sum(axis=-1)
        net += received
        all_receipts += received
        ring[slot] = 0.
        demand = paths[:, day//30]/30
        unmet_today = np.maximum(demand[None, :]-np.maximum(net, 0), 0)
        net -= demand[None, :]
        all_demand += demand[None, :]
        if independent:
            position -= w[:, None, :]*demand[None, :, None]
        else:
            position -= demand[None, :]
        if model == 'rop':
            if independent:
                batches = np.maximum(0, 1+np.floor((level[:, None, :]-position)/quantity[:, None, :]))
                amount = batches*quantity[:, None, :]
                position += amount
                event_orders = batches
            else:
                total_q = quantity.sum(axis=1)
                batches = np.maximum(0, 1+np.floor((level[:, 0, None]-position)/total_q[:, None]))
                amount = batches[:, :, None]*quantity[:, None, :]
                position += batches*total_q[:, None]
                event_orders = np.broadcast_to(batches[:, :, None], amount.shape)
        elif independent:
            due = ((day+1) % review[:, None, :]) == 0
            amount = np.where(due, np.maximum(level[:, None, :]-position, 0), 0)
            position += amount
            event_orders = (amount > 0).astype(float)
        else:
            due = ((day+1) % review[:, 0, None]) == 0
            total_order = np.where(due, np.maximum(level[:, 0, None]-position, 0), 0)
            amount = total_order[:, :, None]*w[:, None, :]
            position += total_order
            event_orders = (amount > 0).astype(float)
        all_orders += amount
        for j in range(sources):
            if delay[j] == 0:
                net += amount[:, :, j]
                all_receipts += amount[:, :, j]
            else:
                ring[(day+delay[j]) % len(ring), :, :, j] += amount[:, :, j]
        if day >= warm_days:
            unmet += unmet_today
            ordered += amount
            orders += event_orders
            stock_sum += np.maximum(net, 0)
            backlog_sum += np.maximum(-net, 0)
        if trace:
            if count != 1 or reps != 1:
                raise ValueError('Detailed tracing is supported for one candidate and one replication.')
            traces.append(dict(epoch=day+1, demand_kg=float(demand[0]),
                scheduled_receipts_kg=float(received[0, 0]), newly_unmet_kg=float(unmet_today[0, 0]),
                orders=[dict(source=s['id'], quantity_kg=float(amount[0, 0, j]),
                    release_epoch=day+1+int(max(lead)-lead[j] if policy == 'synchronized' else 0),
                    receipt_epoch=day+1+int(delay[j])) for j, s in enumerate(src) if amount[0, 0, j] > 0],
                net_inventory_kg=float(net[0, 0])))
    outstanding = ring.sum(axis=(0, 3))
    common_position = position.sum(axis=-1) if independent else position
    physical_error = net-initial[:, None]-all_receipts+all_demand
    position_error = common_position-net-outstanding
    source_pipeline_error = all_orders.sum(axis=-1)-all_receipts-outstanding
    annual = demand_stats(payload['demand'])['mean']*12
    units = np.array([s['cp']+c['ce']*s['ep'] for s in src])
    planned_supply = annual*w
    onsite_index = next((j for j, s in enumerate(src) if s['id'] == 1), None)
    used = planned_supply[:, onsite_index]/c['yield'] if onsite_index is not None else np.zeros(count)
    sold = c['scrap']-used
    if np.any(sold < -1e-7):
        raise ValueError('Candidate exceeds expected annual scrap availability.')
    sold = np.maximum(sold, 0.)  # round-off only, never clip a realized scrap imbalance
    tr = c['tc']+c['ce']*c['te']
    fixed = dict(Cp=planned_supply@units,
        Cpt=planned_supply@np.array([s['distance'] for s in src])*tr,
        Cst=sold*c['scrap_distance']*tr,
        Civ=np.full(count, sum(s['investment'] for s in src)/c['amortization']),
        Rev=sold*c['scrap_price'])
    costs = {k: np.broadcast_to(v[:, None], (count, reps)) for k, v in fixed.items()}
    costs.update(Co=(orders/years)@np.array([s['co'] for s in src]),
        Ch=c['h']*(w@units)[:, None]*stock_sum/eval_days,
        Cb=c['cb']*unmet/years)
    totals = sum(v for k, v in costs.items() if k != 'Rev')-costs['Rev']
    answers = []
    for i in range(count):
        fill_rep = [None if d == 0 else float(1-u/d) for u, d in zip(unmet[i], demand_observed)]
        answer = dict(costs={k:float(v[i].mean()) for k, v in costs.items()},
            annual_cost_eur=float(totals[i].mean()), replication_costs=totals[i].tolist(),
            fill_rate=float(1-unmet[i].sum()/demand_observed.sum()), fill_rate_replications=fill_rep,
            demand_kg_replications=demand_observed.tolist(), unmet_kg_replications=unmet[i].tolist(),
            average_inventory_kg=float(stock_sum[i].mean()/eval_days),
            average_backlog_kg=float(backlog_sum[i].mean()/eval_days),
            annual_shortage_kg=float(unmet[i].mean()/years),
            annual_source_supply_kg=planned_supply[i].tolist(),
            realized_annual_supply_kg=(ordered[i].mean(axis=0)/years).tolist(),
            orders_per_year=(orders[i].mean(axis=0)/years).tolist(),
            scrap_used_kg_per_year=float(used[i]), scrap_sold_kg_per_year=float(sold[i]),
            initialization_net_inventory_kg=float(initial[i]),
            diagnostics=dict(physical_balance_error_kg=float(np.max(np.abs(physical_error[i]))),
                inventory_position_error_kg=float(np.max(np.abs(position_error[i]))),
                pipeline_balance_error_kg=float(np.max(np.abs(source_pipeline_error[i]))),
                final_net_inventory_kg=net[i].tolist(),
                final_outstanding_kg=outstanding[i].tolist()))
        if trace:
            answer['trace'] = traces
        answers.append(answer)
    return answers


def simulate_rop(payload, src, policy, shares, quantities, thresholds, paths, trace=False):
    return simulate_inventory_batch(payload, src, policy, [shares], [thresholds], paths,
                                    model='rop', quantities=[quantities], trace=trace)[0]


def simulate_outl(payload, src, policy, shares, targets, reviews, paths, trace=False):
    return simulate_inventory_batch(payload, src, policy, [shares], [targets], paths,
                                    model='outl', reviews=[reviews], trace=trace)[0]


def mean_interval(values):
    values = np.asarray(values, dtype=float)
    mean = float(values.mean())
    if len(values) < 2:
        return [mean, mean]
    half = float(student_t.ppf(.975, len(values)-1)*values.std(ddof=1)/math.sqrt(len(values)))
    return [mean-half, mean+half]


def attach_uncertainty(row):
    row['annual_cost_ci95_eur'] = mean_interval(row['replication_costs'])
    u = np.asarray(row['unmet_kg_replications'])
    d = np.asarray(row['demand_kg_replications'])
    ratio = u.sum()/d.sum()
    if len(d) > 1:
        residual = u-ratio*d
        half = float(student_t.ppf(.975, len(d)-1)*residual.std(ddof=1)/(math.sqrt(len(d))*d.mean()))
    else:
        half = 0.
    row['fill_rate_ci95'] = [max(0., 1-ratio-half), min(1., 1-ratio+half)]
    row['fill_rate_ci_method'] = 'Approximate replication-ratio delta interval; not a service guarantee.'
    return row
def allocation_interval(src, annual, c):
    caps = []
    for s in src:
        upper = 1. if s['capacity'] is None else min(1., s['capacity']/annual)
        if s['id'] == 1:
            upper = min(upper, c['yield']*c['scrap']/annual)
        caps.append(upper)
    if len(src) == 1:
        return (1., 1.) if caps[0] >= 1-1e-12 else None
    lo, hi = max(0., 1-caps[1]), min(1., caps[0])
    if lo > hi+1e-12 or hi <= 0 or lo >= 1:
        return None
    return lo, hi


def candidate_parameters(payload, src, policy, model, stats, count, rng, interval):
    """Bounded proposals; every IND source has free quantities/levels/intervals."""
    c, st = payload['costs'], payload['settings']
    lo, hi = interval
    if len(src) == 1:
        shares = np.ones((count, 1))
    else:
        fractions = rng.uniform(lo, hi, count) if hi > lo else np.full(count, lo)
        fractions[:min(count, 10)] = np.linspace(lo, hi, min(count, 10))
        fractions = np.clip(fractions, max(lo, 1e-7), min(hi, 1-1e-7))
        shares = np.column_stack([fractions, 1-fractions])
    annual = stats['mean']*12
    units = np.array([s['cp']+c['ce']*s['ep'] for s in src])
    value = shares@units
    mean, sd = stats['mean'], stats['sd']
    lead = max(s['lead'] for s in src)
    eoq = np.sqrt(2*annual*sum(s['co'] for s in src)/(c['h']*value))
    qmin = st.get('quantity_min_kg') or max(mean/1000, 1e-6)
    qmax = st.get('quantity_max_kg') or max(4*float(eoq.max()), 24*mean, qmin*10)
    rmax = st.get('reorder_max_kg') or max(mean*(lead+3)+7*sd*math.sqrt(lead+1), mean)
    min_review = int(st.get('min_review_days', 1))
    max_review = int(st.get('max_review_days', st.get('max_review', 6)*30))
    smax = st.get('target_max_kg') or max(mean*(lead+max_review/30+2)+7*sd*math.sqrt(lead+max_review/30+1), mean)
    qmin, qmax, rmax, smax = map(float, (qmin, qmax, rmax, smax))
    if qmax < qmin:
        raise ValueError('Quantity search bounds are inconsistent.')
    z = rng.uniform(0, 5, count)
    z[0] = 5.
    if count > 1:
        z[1] = max(0., float(norm.ppf(c['service'])))
    independent = policy == 'independent'
    bounds = dict(quantity_min_kg=qmin, quantity_max_kg=qmax,
        reorder_max_kg=rmax, target_max_kg=smax,
        min_review_days=min_review, max_review_days=max_review)
    if model == 'rop':
        if independent:
            setup = np.array([s['co'] for s in src])
            qbase = np.sqrt(2*annual*shares*setup[None, :]/(c['h']*value[:, None]))
            quantities = np.clip(qbase*np.exp(rng.uniform(math.log(.25), math.log(4), shares.shape)), qmin, qmax)
            # Include a baseline, then independently perturb each source quantity.
            quantities[0] = np.clip(qbase[0], qmin, qmax)
            levels = np.empty_like(shares)
            for j, s in enumerate(src):
                levels[:, j] = shares[:, j]*(mean*(s['lead']+1/30)+z*sd*math.sqrt(s['lead']+1))
            levels *= rng.uniform(.5, 1.7, levels.shape)
            levels = np.clip(levels, 0., rmax)
            levels[0] = rmax*shares[0]
        else:
            total_q = np.clip(eoq*np.exp(rng.uniform(math.log(.25), math.log(4), count)), qmin, qmax)
            total_q[0] = np.clip(eoq[0], qmin, qmax)
            quantities = shares*total_q[:, None]
            levels = np.clip(mean*(lead+1/30)+z*sd*math.sqrt(lead+1), 0., rmax)[:, None]
            levels[0, 0] = rmax
        return dict(shares=shares, levels=levels, quantities=quantities, bounds=bounds)
    controls = len(src) if independent else 1
    reviews = rng.integers(min_review, max_review+1, (count, controls))
    anchors = [min_review, min(max(30, min_review), max_review), max_review,
               min(max(15, min_review), max_review), min(max(60, min_review), max_review)]
    for i, review in enumerate(anchors[:count]):
        reviews[i] = review
    if independent:
        levels = np.empty_like(shares)
        for j, s in enumerate(src):
            protection = s['lead']+reviews[:, j]/30+1/30
            levels[:, j] = shares[:, j]*(mean*protection+z*sd*np.sqrt(protection+1))
        levels *= rng.uniform(.5, 1.7, levels.shape)
        levels = np.clip(levels, 0., smax)
        levels[0] = smax*shares[0]
    else:
        protection = lead+reviews[:, 0]/30+1/30
        levels = np.clip(mean*protection+z*sd*np.sqrt(protection+1), 0., smax)[:, None]
        levels[0, 0] = smax
    return dict(shares=shares, levels=levels, reviews=reviews, bounds=bounds)


def solve_known(payload):
    """Uncapacitated WW, fixed shares and synchronized receipts, annualized costs."""
    c, settings = payload['costs'], payload['settings']
    demands = payload['monthly_demand']
    months = len(demands)
    annual = sum(demands)*12/months
    tr = c['tc']+c['ce']*c['te']
    lookup = {s['id']:s for s in payload['sources']}
    results = []
    for ids in source_configurations(payload['sources']):
        src = [lookup[j] for j in ids]
        label = '+'.join(map(str, ids))
        fractions = allocation_endpoints(src, annual, c)
        if not fractions:
            results.append(dict(configuration=label, status='capacity/scrap infeasible or single-source boundary'))
            continue
        candidates = []
        for shares in fractions:
            value = sum(w*(s['cp']+c['ce']*s['ep']) for w, s in zip(shares, src))
            setup = sum(s['co'] for s in src)
            _, lots = wagner_whitin(demands, value, setup, c['h'])
            lead = max(s['lead'] for s in src)
            if not settings.get('allow_pre_horizon', True) and any(l['receipt_month']-lead < 1 for l in lots):
                continue
            holding_cost = value*c['h']/12*sum(
                sum(demands[m]*(m-(lot['receipt_month']-1))
                    for m in range(lot['receipt_month']-1, lot['covers_through_month'])) for lot in lots)
            onsite_share = next((w for w, s in zip(shares, src) if s['id'] == 1), 0.)
            used = annual*onsite_share/c['yield']
            sold = max(0., c['scrap']-used)
            costs = dict(Cp=annual*value, Co=len(lots)*setup*12/months,
                Ch=holding_cost*12/months, Cb=0.,
                Cpt=annual*sum(w*s['distance'] for w, s in zip(shares, src))*tr,
                Cst=sold*c['scrap_distance']*tr,
                Civ=sum(s['investment'] for s in src)/c['amortization'], Rev=sold*c['scrap_price'])
            schedule = [dict(source=s['id'], receipt_month=lot['receipt_month'],
                release_month=lot['receipt_month']-s['lead'], quantity_kg=w*lot['quantity_kg'])
                for lot in lots for w, s in zip(shares, src)]
            row = dict(configuration=label, status='feasible', model='Wagner–Whitin',
                lead_months=lead, annual_demand_kg=annual, lots=lots, schedule=schedule,
                orders_in_horizon=len(lots), horizon_months=months, costs=costs,
                annual_cost_eur=sum(v for k, v in costs.items() if k != 'Rev')-costs['Rev'],
                scrap_used_kg_per_year=used, scrap_sold_kg_per_year=sold,
                sources=[dict(id=s['id'], name=s['name'], fraction=w,
                    annual_supply_kg=annual*w, release_offset_months=lead-s['lead'])
                    for w, s in zip(shares, src)])
            row['horizon_cost_eur'] = row['annual_cost_eur']*months/12
            candidates.append(row)
        results.append(min(candidates, key=lambda r:r['annual_cost_eur']) if candidates else
                       dict(configuration=label, status='no feasible schedule without pre-horizon release'))
    feasible = [r for r in results if r['status'] == 'feasible']
    return dict(model='Wagner–Whitin', version=VERSION, characterization=None,
        best=min(feasible, key=lambda r:r['annual_cost_eur']) if feasible else None,
        status='completed' if feasible else 'no feasible solution found', configurations=results,
        warnings=[
            'Exact uncapacitated monthly lot-sizing conditional on configuration and fixed source shares. Receipts are synchronized.',
            'Zero initial stock and no shortages. No batch-capacity or within-year scrap constraints.',
            'Annual-equivalent demand is 12 × total horizon demand / horizon months. Costs are reported per model year.',
            'Straight-line investment annualization; all unused scrap is sold.',
            'Pre-horizon order releases are '+('allowed.' if settings.get('allow_pre_horizon', True) else 'not allowed.')])


def solve(payload):
    validate(payload)
    if payload['mode'] == 'known':
        return solve_known(payload)
    p = copy.deepcopy(payload)
    classification = characterize_inputs(p['demand'], p['settings'])
    p['demand'] = classification['prepared_demand']
    stats = demand_stats(p['demand'])
    model = 'rop' if classification['model'] == MODEL_NAMES['rop'] else 'outl'
    st, c = p['settings'], p['costs']
    count = int(st.get('search_candidates', 60))
    years = int(st.get('simulation_years', 8))
    warmup = int(st.get('warmup_years', 2))
    reps = int(st.get('replications', 5))
    training_reps = int(st.get('training_replications', 2))
    train_seed, test_seed, search_seed = np.random.SeedSequence(st.get('seed', 42)).spawn(3)
    training = monthly_paths(p['demand'], (years+warmup)*12, training_reps, train_seed)
    testing = monthly_paths(p['demand'], (years+warmup)*12, reps, test_seed)
    rng = np.random.default_rng(search_seed)
    if training[:, warmup*12:].sum() <= 0 or testing[:, warmup*12:].sum() <= 0:
        raise ValueError('No positive evaluation demand in a search or validation sample. Increase years or replications.')
    annual = 12*stats['mean']
    results = []
    lookup = {s['id']:s for s in p['sources']}
    for ids in source_configurations(p['sources']):
        src = [lookup[j] for j in ids]
        policies = ('synchronized',) if len(src) == 1 else (POLICIES if st.get('coordination', 'compare') == 'compare' else (st['coordination'],))
        interval = allocation_interval(src, annual, c)
        for policy in policies:
            base = dict(configuration='+'.join(map(str, ids)), policy=policy, model=MODEL_NAMES[model])
            if interval is None:
                results.append(dict(base, status='expected annual capacity or scrap infeasible'))
                continue
            params = candidate_parameters(p, src, policy, model, stats, count, rng, interval)
            metrics = simulate_inventory_batch(p, src, policy, params['shares'], params['levels'], training,
                model=model, quantities=params.get('quantities'), reviews=params.get('reviews'))
            eligible = [i for i, m in enumerate(metrics) if m['fill_rate'] >= c['service']]
            if not eligible:
                results.append(dict(base, status='no service-feasible candidate found in search',
                    candidates_evaluated=count, search_bounds=params['bounds']))
                continue
            best_index = min(eligible, key=lambda i:metrics[i]['annual_cost_eur'])
            w, levels = params['shares'][best_index], params['levels'][best_index]
            q = params['quantities'][best_index] if model == 'rop' else None
            review = params['reviews'][best_index] if model == 'outl' else None
            answer = simulate_inventory_batch(p, src, policy, [w], [levels], testing,
                model=model, quantities=None if q is None else [q], reviews=None if review is None else [review])[0]
            answer.update(base, status='feasible' if answer['fill_rate'] >= c['service'] else 'validation service target missed',
                annual_demand_kg=annual, service_target=c['service'], candidates_evaluated=count,
                search_feasible_candidates=len(eligible), search_bounds=params['bounds'],
                training_fill_rate=metrics[best_index]['fill_rate'],
                training_annual_cost_eur=metrics[best_index]['annual_cost_eur'])
            attach_uncertainty(answer)
            answer['sources'] = []
            longest = max(s['lead'] for s in src)
            for j, s in enumerate(src):
                row = dict(id=s['id'], name=s['name'], fraction=float(w[j]), annual_supply_kg=annual*float(w[j]),
                    release_offset_months=longest-s['lead'] if policy == 'synchronized' else 0,
                    effective_receipt_delay_days=round((longest if policy == 'synchronized' else s['lead'])*30))
                if model == 'rop':
                    row.update(Q_kg=float(q[j]), R_kg=float(levels[j if policy == 'independent' else 0]))
                else:
                    row.update(S_kg=float(levels[j] if policy == 'independent' else w[j]*levels[0]),
                        T_days=int(review[j if policy == 'independent' else 0]))
                answer['sources'].append(row)
            if policy != 'independent':
                if model == 'rop':
                    answer.update(Q_kg=float(q.sum()), R_kg=float(levels[0]))
                else:
                    answer.update(S_kg=float(levels[0]), T_days=int(review[0]))
            results.append(answer)
    feasible = [r for r in results if r['status'] == 'feasible']
    best = min(feasible, key=lambda r:r['annual_cost_eur']) if feasible else None
    for row in results:
        if best is not None and 'replication_costs' in row:
            differences = np.asarray(row['replication_costs'])-np.asarray(best['replication_costs'])
            row['cost_difference_vs_best_eur'] = float(differences.mean())
            row['paired_difference_ci95_eur'] = mean_interval(differences)
    settings_used = dict(search_candidates=count, evaluation_years=years, warmup_years=warmup,
        training_replications=training_reps, validation_replications=reps, seed=st.get('seed', 42),
        review_interval_units='daily periods', source_lead_input_units='30-day months',
        days_per_year=360, adi_cutoff=ADI_CUTOFF, cv2_cutoff=CV2_CUTOFF, demand_timing='independent monthly totals, uniform daily consumption',
        search_method='bounded random candidate search with baseline proposals',
        stopping_rule='fixed candidate budget per configuration-policy alternative',
        acceptance_rule='pooled estimated validation immediate fill rate >= target')
    warnings = [
        'Both ROP and OUTL use the same daily simulation and complete backordering. Multiple replenishments may be outstanding.',
        'SYN commits all source portions immediately and schedules source releases for synchronized receipts. SIM releases together; IND uses virtual accounts.',
        'IND order quantities, reorder points, review intervals and targets are separately searched where applicable.',
        'Monthly demand is iid; each monthly total is spread uniformly over 30 days. The Bernoulli variable is drawn once per month.',
        'Demand classification and simulation use identical positive-month probabilities; direct models have no additional Bernoulli multiplier.',
        'Capacity and scrap constraints apply to expected annual flows. Powder, transport and scrap costs use stable flows D × source share; realized finite-run orders are reported separately.',
        'Holding uses average positive end-of-day stock and a share-weighted unit valuation. Shortage is charged once per kg initially unmet.',
        'ROP setup is charged per fixed-size batch. OUTL setup is charged only for positive source orders.',
        'Search and validation use disjoint random streams and common demand paths across alternatives.',
        'The selected result is the lowest-cost validated candidate found, not a proven global optimum.',
        'Cost intervals and approximate fill-rate intervals describe conditional simulation uncertainty. They do not certify service or remove selection uncertainty.',
        'The ROP/OUTL routing is an adopted study rule, not a proof of optimal policy-family selection.']
    return dict(model=MODEL_NAMES[model], version=VERSION, evaluation='daily simulation',
        characterization={k:v for k, v in classification.items() if k != 'prepared_demand'},
        best=best, status='completed' if best else 'no validated service-feasible solution found',
        configurations=results, warnings=warnings, settings_used=settings_used)
HTML = Path(__file__).with_name('app.html').read_text(encoding='utf-8')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, value, content_type='application/json'):
        data = value.encode() if isinstance(value, str) else json.dumps(value, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type+'; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store, max-age=0')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ('/', '/single', '/single/', '/scenarios', '/scenarios/'):
            page = HTML.replace('__DEFAULT__', json.dumps(DEFAULT)).replace('__APP_VERSION__', VERSION)
            if path != '/':
                page = page.replace('id="home-workspace" class="card"', 'id="home-workspace" class="card hidden"').replace('id="app-nav" class="toolbar hidden"', 'id="app-nav" class="toolbar"')
                if path.startswith('/single'):
                    page = page.replace('id="single-workspace" class="hidden"', 'id="single-workspace"')
                else:
                    page = page.replace('id="batch-workspace" class="hidden"', 'id="batch-workspace"')
            self.send(200, page, 'text/html')
        elif path == '/examples/scenarios_demo.json':
            self.send(200, json.loads(Path(__file__).with_name('examples').joinpath('scenarios_demo.json').read_text(encoding='utf-8')))
        elif path == '/health':
            self.send(200, dict(status='ok', version=VERSION))
        else:
            self.send(404, dict(error='Not found'))

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 15*1024*1024:
                raise ValueError('Invalid request size.')
            data = json.loads(self.rfile.read(length))
            if self.path == '/classify':
                result = characterize_inputs(data['demand'], data['settings'])
            elif self.path == '/validate':
                validate(data)
                result = dict(valid=True)
            elif self.path == '/solve':
                result = solve(data)
            elif self.path == '/import_excel':
                result = import_excel(data['data'])
            else:
                self.send(404, dict(error='Not found'))
                return
            self.send(200, result)
        except (ValueError, KeyError, TypeError, OverflowError) as error:
            self.send(400, dict(error=str(error)))
        except Exception as error:
            self.send(500, dict(error=f'Calculation error: {error}'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8774)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--solve-input', type=Path, help='Solve a JSON input file without opening the app')
    parser.add_argument('--output', type=Path, help='Write CLI results to this JSON file')
    args = parser.parse_args()
    if args.self_test:
        import unittest
        suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern='test_*.py')
        raise SystemExit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
    if args.solve_input:
        result = solve(json.loads(args.solve_input.read_text()))
        output = json.dumps(result, indent=2, allow_nan=False)
        if args.output:
            args.output.write_text(output, encoding='utf-8')
        else:
            print(output)
        return
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError as error:
        parser.error(f'Could not start version {VERSION} on port {args.port}: {error}. An older app may still be running. Stop it, or use --port {args.port+1}. Do not reuse an old browser tab.')
    port = server.server_address[1]
    print(f'Powder inventory planning v{VERSION}: http://127.0.0.1:{port}/', flush=True)
    print('The start page offers Single-scenario calculation and Multiple-scenario analysis.', flush=True)
    if not args.no_browser:
        threading.Timer(.5, lambda:webbrowser.open(f'http://127.0.0.1:{port}/')).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
