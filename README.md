# Powder inventory app, version 8.0

This version adds guided scenario analysis to the existing inventory models. Save a baseline, generate variations, run the scenario list together, and export comparison tables. The Wagner–Whitin, ROP and OUTL calculations retain the version 6 methodology.

## Run

Extract the entire ZIP into a new folder. On Windows, double-click **START_APP.bat** after installing the requirements below. The new app uses port **8774** to avoid older copies on port 8765. Its opening page shows **App version 8.0** and asks **What would you like to do?** with two choices. Do not open `app.html` directly and do not reuse an old browser tab. If the port is occupied, the terminal explains how to stop the old server or choose another port.

Use Python 3.10 or later. Keep `powder_inventory_app.py` and `app.html` in the same folder.

```bash
python -m pip install -r requirements.txt
python powder_inventory_app.py
```

Open http://127.0.0.1:8774 if the browser does not open automatically. To select another port or avoid automatic browser opening:

```bash
python powder_inventory_app.py --port 8775 --no-browser
```

The server binds to localhost. Stop it with Ctrl+C. This is a local research application, not an authenticated public web service.

## Field information

Each input has a small information button beside its label or source cell. Click it to read the definition, units and relevant example. The shared information popover supports keyboard activation, Escape, clicking outside, and a Close button. Use a modern browser with native popover support, such as current Chrome or Edge. Input explanations no longer appear as long paragraphs beneath the fields. Error messages, calculation outcomes and model assumptions remain visible where relevant. Demand-occurrence behaviour and the inventory models are unchanged.

## Interface

Version 8.0 adds a redesigned start page, an at-a-glance setup guide, cost charts and a batch results summary. Charts show the same evaluated results as the tables. The inventory models and calculation settings are unchanged.

## Workflow

On the opening page choose **Single-scenario calculation** or **Multiple-scenario analysis**. The single-scenario workflow then proceeds as follows.

1. Answer whether monthly quantities are known.
2. For known demand, enter monthly quantities. For uncertain demand, choose Normal, Poisson, or Geometric and enter its parameters and optional positive-month frequency.
3. Review demand characterization. Smooth/erratic demand uses ROP; intermittent/lumpy demand uses OUTL.
4. Enter costs, scrap availability, yield, and source information. Add sources up to a total of ten.
5. For uncertain demand, select coordination policies, the immediate fill-rate target, and simulation/search settings. OUTL review intervals are measured in days.
6. Review inputs, calculate, and download the results JSON. It includes the inputs, parameters, cost breakdown, comparisons, bounds, uncertainty estimates, and evaluation settings.

The adopted ROP/OUTL assignment is a study rule. It does not demonstrate optimality across policy families.

Demand classification and inventory-model assignment are automatic. The classification thresholds are fixed at ADI = 1.32 and positive-size CV² = 0.49, with equality assigned to the higher category. The interface has no controls for changing the thresholds or selecting a different demand category. Older imported threshold settings do not override the fixed rule.

## Separate app routes

- `http://127.0.0.1:8774/` opens the start page with two explicit choices.
- `http://127.0.0.1:8774/single` opens the single-scenario questions and results.
- `http://127.0.0.1:8774/scenarios` opens the multiple-scenario workspace directly.

Each workflow page has only a **Home** navigation button. Clicking it asks whether you are sure you want to return to the opening page. Cancel keeps the current page; confirming returns Home while preserving tab-held inputs, scenarios and results. The same confirmation applies when browser Back or Forward would return Home. Choose the single- or multiple-scenario workflow from the opening page. Direct links and refreshes are supported by the local server. As before, refreshing clears tab-held inputs and results, so save the scenario list first. While a calculation is running, finish or stop it before switching routes.

The multiple-scenario page has **Load demonstration scenarios** for a quick start, plus scenario import, baseline configuration, variations, batch calculations and exports. All calculations use the same inventory models. Demand-occurrence inputs retain the existing behaviour pending the later methodological discussion.

## Scenario analysis

1. Configure an operating scenario through the existing questions.
2. On **Review your inputs**, enter a scenario name and choose **Add to scenario analysis**.
3. In **Create variations**, select a starting scenario, an input, and comma-separated values. Choose **Add variations**. Each variation is a separate copy; other inputs stay fixed.
4. Review or rename scenarios. **Edit inputs** loads a saved scenario into the questions. **Save scenario changes** replaces that scenario and clears its old result; **Save as a new scenario** keeps the original. **Duplicate** makes a new snapshot without copying results.
5. Choose **Run all scenarios**. Evaluations run sequentially and use the existing solver. A failed evaluation does not stop the remaining scenarios. Progress counts completed scenarios, not internal simulation steps.
6. **Stop after current scenario** finishes the in-flight evaluation and retains completed results. **Run remaining / retry failed** evaluates scenarios without a returned result and keeps completed evaluations. **Run all scenarios** starts a fresh evaluation of the complete list.
7. Open a result to inspect all configurations. Export a selected-solution comparison CSV, a CSV of every alternative, or full JSON results. CSV files include each scenario's complete input JSON; the full JSON also contains uncertainty estimates and calculation settings. Scrap revenue is a positive component subtracted from total cost.
8. **Save scenario list** downloads inputs and names for re-use. Importing appends checked scenarios to the existing list. Invalid imports and variation groups are rejected together, so they do not leave partial additions.

A list can contain up to 100 scenarios. Keep the tab open during calculations and save the list before closing or refreshing. Lists and results are held in the current tab, not automatically persisted. Reopening the browser requires importing a saved scenario list and rerunning it. Full results JSON is an output archive, not the scenario-list import format.

For a quick demonstration, import `examples/scenarios_demo.json` from the **Scenario analysis** view. It includes known demand, ROP, OUTL and a scenario with no annual supply capacity. Its small simulation and search settings are for demonstration, not journal conclusions.

**Demand size multiplier:** for known demand, scales all monthly quantities; for Normal demand, scales both underlying Normal parameters; for count demand, scales kg per count. Thus it changes demand size while preserving occurrence probability and positive-size CV². Varying lambda, Geometric q, or occurrence separately can change classification, so the app checks every scenario again.

**Unsuccessful evaluations:** a valid calculation can return `best: null`. The result still contains the alternative statuses. Resource infeasibility, no service-feasible candidate found in search, and a missed validation target are separate outcomes. A failed finite search is not a proof that no feasible inventory policy exists. The HTTP response is successful for these valid evaluation outcomes; invalid inputs remain errors. Consumers of the JSON/API must check `best` before reading selected-solution fields.

## Implementation and methodology mapping

| Methodology | Implementation |
| --- | --- |
| Known monthly demand | Wagner–Whitin dynamic programming, synchronized source receipts, fixed source allocation within each alternative |
| Demand classification | Monthly ADI and conditional positive-size CV²; fixed cutoffs 1.32 and 0.49, equality assigned to the higher category |
| Smooth/erratic | Direct positive truncated Normal, Poisson on counts 0,1,…, or Geometric on counts 0,1,… |
| Intermittent/lumpy | Monthly Bernoulli occurrence multiplied by a strictly positive size; Normal truncated at zero, Poisson conditioned on positive counts, or Geometric conditioned on positive counts |
| Timing | Independent monthly totals, thirty equal daily quantities per month, 360 days per model year |
| Supply configurations | Each source alone and on-site source 1 paired with each external supplier; no external–external pairs or triple sourcing |
| SYN | Common planning position includes unreleased commitments; source-specific release delays synchronize receipts at the longest lead time |
| SIM | Common inventory position; immediate joint release and source-specific receipt times |
| ROP–IND | Separately searched source batches Qj, reorder points Rj, and allocation shares; Qj is not constrained to equal wj Q |
| OUTL–IND | Separately searched source review intervals Tj, order-up-to levels Sj, and allocation shares |
| Shared physical inventory | Pooled stock and backlog; virtual accounts affect replenishment control without reserving stock |
| Immediate fill rate | One minus initially unmet quantity divided by demand; quantity weighted across evaluation replications |
| Ordering costs | Each fixed ROP batch incurs setup; each positive OUTL source order incurs setup; zero OUTL orders incur none |
| Resource feasibility | Expected annual flow D wj respects source capacity; onsite flow divided by yield respects annual-average scrap availability |
| Costs | Powder, ordering, holding, shortage, powder transport, scrap transport, annualized investment, less scrap revenue |
| Numerical evaluation | Warm-up, common demand paths across alternatives, independent search/validation streams, replication uncertainty estimates |

With n sources, comparison mode produces 4n−3 stochastic configuration–policy rows, including any infeasible rows. Single-source policies are evaluated once, labelled SYN. Known-demand evaluation produces 2n−1 configuration rows. SYN/SIM are equivalent at equal lead times **for identical parameters**. Separate finite searches can return different parameters and outcomes even when policy families coincide.

## Important input conventions

- **Lead times:** the source input remains in 30-day months for compatibility. Values must correspond to an integer number of days. For example, 0.2 month is six days. OUTL review intervals are integer daily periods.
- **Normal:** mean and standard deviation refer to the underlying Normal before truncation. Actual moments are displayed. Standard deviation zero is allowed only with a positive mean.
- **Counts:** `unit` is kg per count. Geometric q is P(count = 0) for the direct distribution. Its positive component has counts 1,2,… after conditioning.
- **Occurrence:** blank uses the distribution-implied positive-month probability. A supplied infrequent occurrence probability becomes the Bernoulli probability. A supplied frequent probability must agree with the direct distribution. Conflicting inputs are rejected, rather than classifying one process and simulating another. Positive count-size conditioning avoids introducing a second zero mechanism.
- **Service:** the target is the immediate quantity fill rate, not the probability of a shortage-free cycle. Any target strictly between zero and one is accepted.
- **All-zero data:** all-zero known demand and samples with no evaluation demand are rejected explicitly. Increase the stochastic evaluation horizon for very rare demand.
- **Unit costs:** powder cost is per kg of usable powder. Yield converts onsite powder requirements into scrap requirements.

The legacy workbook importer reads the existing Parameters/Sources layout for the first three sources. Add further sources in the form. `immediate_fill_rate_min` is imported as the fill-rate target. A legacy `cycle_service_level` is not silently treated as a fill-rate target; the app issues a review warning.

## Simulation convention

Each day processes previous scheduled receipts, serves demand, records newly unmet demand, reviews replenishment positions, receives newly ordered zero-delay quantities, and records closing stock/backlog. Zero-delay orders placed after demand cannot retrospectively improve its immediate fill rate. Backorders are cleared by subsequent receipts through the net-inventory balance.

ROP uses the at-or-below trigger and orders enough fixed batches to restore the position above the reorder point. SYN commitments enter the planning position immediately; their later physical release does not enter it again. IND virtual positions are reduced by allocated demand and increased by commitments, never by receipts.

OUTL reviews use epochs T, 2T, …; IND sources share the initial origin but have separately searched intervals. Variable quantities restore the applicable position to its target when it is below the target. Multiple outstanding orders are retained in a receipt schedule.

Initialization uses an empty pipeline and net stock equal to the common replenishment level, or the sum of source levels under IND. IND accounts initially allocate that physical stock by source shares. A configurable warm-up is excluded from performance measurement. Assess warm-up adequacy for paper experiments rather than assuming the default is sufficient.

## Costs and constraints

The objective is annual cost. Capacity and scrap constraints are expected annual constraints, not per-batch or realized-year limits. Fixed-share stable flow is D wj. Powder, transport, and scrap costs use that expected flow; observed finite-run order quantities are also exported separately. Holding and shortage use simulation estimates. Pooled stock is valued using the share-weighted effective powder cost. Shortage is charged once per initially unmet kg, with no time-dependent backlog penalty.

Investments are annualized as investment divided by amortization years, without discounting or residual value. Monetized emissions enter powder and transportation unit rates. All unused annual-average scrap is sold.

Known-demand scheduling assumes zero initial stock, no shortages, synchronized receipts, no batch-capacity constraints, and optionally permitted pre-horizon releases. The DP is exact conditional on configuration and fixed shares. Allocation endpoints are evaluated because the optimal fixed-share scheduling cost is the minimum of affine functions of the dual-source share; the remaining costs are affine for a fixed configuration. Zero-share dual-source endpoints are represented by single-source configurations. The reported horizon total and annual equivalent distinguish horizon costs from annual rates.

## Search, validation, and uncertainty

Search is a bounded candidate search, not a global optimization certificate. It includes baseline and random proposals. ROP proposals start from EOQ-based batch scales; normal-quantile-like level proposals only initialize candidates. Every candidate's actual performance is evaluated through the shared simulation, including count demand and overlapping source receipts. These proposals do not replace simulation with a Normal demand approximation.

Each alternative receives the specified candidate budget. The lowest-cost training candidate meeting the estimated fill-rate target is evaluated on independent validation demand. If it misses the validation target, the alternative is reported accordingly. Only alternatives meeting the estimated validation target are eligible for final selection. This is an estimated service rule, not a statistical service guarantee. A validation failure does not establish that the configuration has no feasible policy.

Search bounds are exported for each alternative. Automatic bounds depend on demand moments, lead times, EOQ scales, and the maximum review interval. The JSON/API settings can additionally specify `quantity_min_kg`, `quantity_max_kg`, `reorder_max_kg`, and `target_max_kg`. For IND, quantity and level bounds apply to each source control; for SYN/SIM they apply to the common quantity or level.

Cost intervals use replication means and Student's t. Fill-rate intervals are approximate delta-method intervals for the pooled ratio. Paired cost-difference intervals use common validation paths. Intervals are conditional simulation summaries and do not remove selection uncertainty. More replications and longer horizons may be needed for research conclusions.

The app reports the **lowest-cost feasible solution found in the evaluated search space**. It does not claim global optimality or complete the paper's industrial validation, sensitivity analyses, or final computational experiments.

## Tests

```bash
python powder_inventory_app.py --self-test
```

The model tests cover the six demand branches, conditional-distribution moments, routing consistency, inventory/pipeline conservation, source timing, zero-delay service, zero-order setup costs, demand-weighted fill rate, policy equivalence under identical parameters, separately free IND controls, ten-source configurations, expected resource constraints, reproducibility, and Wagner–Whitin against enumerated schedules.

An optional interaction check requires Node.js:

```bash
python check_wizard.py
```

It uses a lightweight DOM and the actual local HTTP API to check the wizard, source addition/removal, ROP/OUTL calculations, snapshot isolation, variation generation, scenario editing, mixed batch execution, unsuccessful outcomes, stop/resume, atomic scenario imports and CSV/JSON exports. It is not a browser rendering or visual-layout test.

## Command-line examples

```bash
python powder_inventory_app.py --solve-input examples/known.json --output known_results.json
python powder_inventory_app.py --solve-input examples/rop.json --output rop_results.json
python powder_inventory_app.py --solve-input examples/outl.json --output outl_results.json
```

Examples use demonstration inputs only. They are not results for the article.

## Files

- `powder_inventory_app.py`: demand calculations, shared simulation, search, Wagner–Whitin, workbook import, HTTP API and CLI.
- `app.html`: sequential input workflow, scenario analysis and results display.
- `requirements.txt`: Python dependencies.
- `test_models.py`: automated mathematical and integration checks.
- `check_wizard.py`, `check_wizard.cjs`: optional API-backed wizard check.
- `examples/`: known-demand, ROP and OUTL inputs, plus an importable demonstration scenario list.
