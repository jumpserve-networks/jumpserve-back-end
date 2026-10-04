# LEO national emergency failover reproduction

This study executes a computational reproduction of the six-country capacity
claim in [Bhosale et al., IMC 2025](https://doi.org/10.1145/3730567.3764482).
It records 562 deterministic model states, 376 configurations, two separately
frozen protocols, source provenance, and a bibliography audit in JumpServe's
Supabase Postgres project `regphejnlvfpyokpniny`. The public frontend module is
`/module/leo-emergency-failover`. The module is released through the existing
JumpServe infrastructure and Amplify production deployment workflows.

## Findings and interpretation

The primary estimand is the mean maximum downlink flow across 15 consecutive
one-second orbital states with 200,000 requested terminals, Config A,
uncapped paper GCB placement, coordinated beams and no incumbent traffic.
The predeclared numerical agreement threshold is ±5% of the paper's Table 1
capacity. Table 1 reports maxima; a fixed terminal budget and short time window
need not achieve the same maximum. The comparison therefore tests numerical
agreement under the recorded design, rather than empirical accuracy.

| Country | Published Gbps | Initial mean Gbps | State min–max Gbps | Difference |
| --- | ---: | ---: | ---: | ---: |
| Tonga | 41 | 40.960 | 40.960–40.960 | −0.10% |
| Haiti | 1,389 | 1,201.216 | 1,191.040–1,238.080 | −13.52% |
| Lithuania | 2,005 | 1,946.283 | 1,877.760–2,000.960 | −2.93% |
| Ghana | 2,163 | 2,016.640 | 1,968.960–2,083.200 | −6.77% |
| Great Britain | 3,530 | 3,090.389 | 3,009.920–3,135.360 | −12.45% |
| South Africa | 4,653 | 4,428.011 | 4,384.320–4,453.440 | −4.84% |

Tonga, Lithuania and South Africa are within the threshold. The other three
are outside it. These differences do not identify a cause or establish that
real satellite capacity is wrong. Shared source data, heuristics and idealized
assumptions mean this is an artifact reproduction, not independent validation.

A separately designed exploratory follow-up tests 500,000, one million,
two million and four million terminals at the initial epoch, plus 15 states at
two million terminals and an artifact-allocation check at four million. The
two-million-state means are unchanged for five countries; South Africa increases
to 4,851.477 Gbps (+4.27% against the paper). Thus simply increasing terminal
budgets does not resolve the Haiti, Ghana or Britain discrepancies. At the initial
epoch, Lithuania falls from 2,000.960 Gbps at 200,000 terminals to 1,877.760 Gbps
at larger budgets. The reused beam heuristic can make nonmonotonic choices;
we have not proven their cause or a global RF optimum. The follow-up does not
replace the initial campaign.

Capacity divided by cable capacity is a comparison with historical design or
literature values, not measured traffic lost. In particular, Table 1 prints
Lithuania's satellite capacity as 2,005 Gbps, lost cable proxy as 101 Gbps and
fraction as 198%. The two printed capacities imply 1,985.15%. We retain the
printed capacities, flag the inconsistent percentage, and do not guess which
source field is erroneous.

## Design, chronology and coverage

`protocol.json` was frozen before the primary 448-state campaign. It specifies
the numerical threshold, allocation variants, time sensitivity, comparison
units and uncertainty interpretation. `saturation-protocol.json` was frozen
before its 114-state execution, after inspection of the first campaign. Primary
execution preceded completion of the supporting-literature review; later reading
does not retrospectively change the predeclared design. The excluded pilot
encountered a CSV parsing error recorded in `results/failures.jsonl`; all planned
primary and follow-up states subsequently completed with no validation errors.

The pinned upstream artifact is
[GT-ANSR-Lab/CosmoSim](https://github.com/GT-ANSR-Lab/CosmoSim), commit
`57af374432fbde4cd7b1f2dade64095f87296e05`. Its GPL license remains with the
separate source checkout. The baseline comprises 6,364 idealized satellites in
five shells and 198 gateways. It uses three ISLs per satellite where configured;
the first shell has none. Config A uses 1.28 Gbps per Ku beam, eight beams with
fourfold reuse per satellite, eight Ku channels per cell, 2.6 Gbps per Ka beam,
20.8 Gbps satellite feeder capacity, 166.4 Gbps per gateway, 100 Gbps ISLs and
100 Mbps terminal demand. Decimal 1 Gbps equals 1,000 Mbit/s.
All three generated constellations use the idealized TLE epoch
`2022-01-01 00:00:00.000`; this is distinct from the 2026 execution date and does
not describe the measured operational satellite fleet.

The initial campaign includes population and four GCB eligibility cutoffs,
requested-budget curves, coordinated/uncoordinated beam contrasts under each
allocation variant, adjacent states and 6/12/18-hour sensitivities, link-rate
sensitivities, and uniform deployment in 14,316- and 34,224-satellite constellations.
The matched contrasts preserve country, constellation, budget, allocation
variant, Ku rate and epoch. Requested and deployed terminal counts remain
separate, because positive caps in the artifact variant can leave a surplus
undeployed. A GCB cap is a minimum population for eligible cells.

| Published claim | Executed coverage | Remaining gap |
| --- | --- | --- |
| Table 1 capacity | Six countries, 15 primary states and saturation follow-up | Different objective/window from a published maximum; no live validation |
| Figures 5 and 13 placement | Budget curves, eligibility caps and two allocation semantics | Corrected allocation, not every published parameter combination |
| Figure 9 beam policy | Matched coordinated/uncoordinated contrasts at t=0 | Heuristic bounds, not an optimal spectrum solution |
| Figure 12 constellation growth | Two larger released constellations, three budgets | One epoch, idealized deployment |
| Figures 15–16 rates | 0.956 and 2.5 Gbps Ku sensitivity | Partial upgrade; not full Config C |
| Figures 6–8, 10 and 22 routing/utilization | Not reproduced | Cost-minimizing/hot-potato routing and utilization |
| Figure 11 sovereignty | Not reproduced | Domestic/foreign gateway restrictions |
| Figures 14 and 23–24 incumbents | Not reproduced | Existing-user competition |
| Figures 1, 18–19 and 21 measurements | Published inputs reused | Independent cable rankings, outage observations and physical satellite measurements |

This is a partial reproduction of the paper's results. Missing claim coverage
is stored as data and shown on the module's methods page; it must not be inferred
from numerical agreement in Table 1.

## Artifact corrections and uncertainty

`run.py` corrects cumulative shell offsets, uses nanosecond timestamps
consistently, and remaps beams at each state rather than reusing stale demand.
Paper GCB uses the Algorithm 1 batch of 200 and redistributes residual terminals
among eligible cells, including zero-population cells for cutoff zero. Artifact
GCB keeps `floor(80 × Ku)` batches, excludes empty cells and leaves residual
terminals undeployed for positive cutoffs. Population placement uses Hamilton
apportionment so rounding cannot exceed the requested budget.

Conservative spatial pruning uses a 100-km margin, followed by exact upstream
PyEphem distance checks. First and last country cells are also checked against
every satellite at each generated epoch. Global incumbent-cell nodes are omitted
only for no-incumbent experiments. Maximum flow replaces the cost objective for
aggregate capacity; this does not reproduce the paper's routes or utilization.

After execution, source inspection clarified that the released capacity helper
automatically doubles ISL rates when Ku is 2.5 Gbps. Ka and the baseline
constellation remain unchanged. The frozen protocol's original Ku-only wording
is preserved; campaign limitations and the dashboard explicitly label this as a
Ku+ISL sensitivity and partial Config C. The runner has not been edited after
recorded execution.

Adjacent seconds are correlated deterministic states, not independent research
replicates. Mean/min/max report descriptive temporal variation. No inferential
confidence interval, bootstrap of seconds, or empirical significance test is
claimed. Weather, terrain, interference, operational scheduling, demand
contention, uplink, power, terminal distribution logistics and long-term service
availability remain unmeasured. A future empirical validation would require
independent sites and outage episodes, actual traffic demand, operational
gateway availability, packet-level/application outcomes and measured RF inputs.

## Literature audit

The complete 96-entry bibliography is in `literature.json`; 39 entries are
classified as research works (including a thesis, chapter and industry capacity
analysis). The twenty-page main paper was downloaded and reviewed with all
figures and appendices. Of the supporting works, 29 verified PDFs were downloaded
and their cited texts reviewed, including the full 315-page dissertation.
Reference 53 was additionally reviewed in complete author-uploaded web text,
including its tables, captions and references; its PDF and independent artwork
inspection remain unavailable. Thus 30 works were reviewed, ten PDFs remain
inaccessible, and nine works remain unread.

Unavailable PDFs are references 11, 17, 20, 30, 53, 54, 55, 67, 75 and 96.
Publisher, author and institutional access attempts are recorded without
bypassing access controls. Version differences are explicit: accepted manuscripts,
preprints, an earlier conference precursor, updated references and inconsistent
conference years are not asserted to equal the final cited version. For reference
72, the downloaded magazine issue has 61 pages, but only the cited four-page
industry article was reviewed. Reference 26's PDF extraction warned about large
form counts; its text and selected result figures were inspected separately.
An unrelated paper returned by an early guessed identifier for reference 91 was
rejected and excluded. Downloads alone never count as completed reviews.

`review-notes.json` contains per-source methods, findings, limitations and
relevance. `retrieve.py` maintains PDF hashes, page counts and failed access
attempts. Full PDFs, extracted text, source artwork and upstream checkout stay
in ignored `sources/` and `results/figures/`; the module publishes citations and
our review notes, not copies of copyrighted papers.

## Reproduction commands and evidence

Run commands from `jumpserve-back-end`. The scientific environment is isolated
in `experiments/leo_failover/.venv`; dependencies are pinned in `requirements.txt`.
For a fresh workspace, obtain the main paper from its
[author PDF](https://saeed.github.io/files/cosmosim-imc25.pdf), retain page text
files 13–15 for bibliography parsing, and clone the exact artifact commit into
`experiments/leo_failover/sources/CosmoSim`. Generate the three constellations
using the artifact's `constellation_configurations/generate_constellation.py`
with `starlink_5shells.yaml`, `starlink_double.yaml` and `starlink_all.yaml`.
Compare their generated TLE/ISL/description hashes with `evidence/provenance.json`
before running a replication. The source checkout and caches already exist in
this workspace.

For a fresh setup, the following commands create the isolated environment,
archive the main paper and generate the pinned model inputs. Run them before the
execution commands below; do not regenerate or edit an existing campaign's
inputs after measurement.

```bash
python3 -m venv experiments/leo_failover/.venv
experiments/leo_failover/.venv/bin/python -m pip install -r experiments/leo_failover/requirements.txt
mkdir -p experiments/leo_failover/sources
git clone https://github.com/GT-ANSR-Lab/CosmoSim.git experiments/leo_failover/sources/CosmoSim
git -C experiments/leo_failover/sources/CosmoSim checkout 57af374432fbde4cd7b1f2dade64095f87296e05
curl -fL https://saeed.github.io/files/cosmosim-imc25.pdf -o experiments/leo_failover/sources/main-paper.pdf
experiments/leo_failover/.venv/bin/python -c 'from pathlib import Path; from pypdf import PdfReader; root=Path("experiments/leo_failover/sources"); reader=PdfReader(root/"main-paper.pdf"); [root.joinpath(f"page-{n:02}.txt").write_text(page.extract_text()) for n,page in enumerate(reader.pages,1)]'
experiments/leo_failover/.venv/bin/python experiments/leo_failover/sources/CosmoSim/constellation_configurations/generate_constellation.py experiments/leo_failover/sources/CosmoSim/constellation_configurations/starlink_5shells.yaml
experiments/leo_failover/.venv/bin/python experiments/leo_failover/sources/CosmoSim/constellation_configurations/generate_constellation.py experiments/leo_failover/sources/CosmoSim/constellation_configurations/starlink_double.yaml
experiments/leo_failover/.venv/bin/python experiments/leo_failover/sources/CosmoSim/constellation_configurations/generate_constellation.py experiments/leo_failover/sources/CosmoSim/constellation_configurations/starlink_all.yaml
```

Compare the main-paper and generated input hashes with recorded provenance.
PDF extraction is a reading aid, not a substitute for inspecting equations,
figures or unreadable pages. Crossref search candidates are metadata only;
source identity must be verified before adding a new download URL.

```bash
experiments/leo_failover/.venv/bin/python -B experiments/leo_failover/retrieve.py
experiments/leo_failover/.venv/bin/python -B experiments/leo_failover/run.py
experiments/leo_failover/.venv/bin/python -B experiments/leo_failover/saturation.py
experiments/leo_failover/.venv/bin/python -B experiments/leo_failover/check.py
experiments/leo_failover/.venv/bin/python -B experiments/leo_failover/analyze.py
experiments/leo_failover/.venv/bin/python -B experiments/leo_failover/analyze.py --saturation
```

Execution resumes completed scenario IDs; it does not create independent repeats.
`check.py` tests edge-case allocation and 40 fixed-seed flow networks, then audits
all 562 archived flows for conservation, visible links, ISL/gateway/satellite
limits, RF demand bounds, terminal accounting and archived input hashes.
`analyze.py` requires exact schedule coverage and unchanged protocol/runner hashes
before producing relational records. Graph/flow caches are local in ignored
`results/raw/`; summary records, full configurations, samples, source hashes,
claim coverage and bibliography are persisted in Supabase.

From `jumpserve-infra`, the purpose-specific importer verifies the JumpServe
project, rejects incomplete campaigns or different registered provenance, and
updates each campaign transactionally:

```bash
python3 -B bin/leo-study-database.py --apply
python3 -B bin/leo-study-database.py --apply --saturation
python3 -B bin/leo-study-database.py --verify
```

The six new relations are `leo_study_campaigns`, `leo_study_configurations`,
`leo_study_samples`, `leo_study_summaries`, `leo_study_papers` and
`leo_study_claims`. Composite foreign keys bind samples to their campaign and
configuration. RLS remains enabled; anonymous reads are explicitly granted and
browser writes denied. Service credentials never enter frontend code. The AI
prompt uses existing `agent_prompt_versions`, `agent_prompt_settings` and
`agent_prompt_publications`; see `jumpserve-infra/docs/leo-study-chat.md`.

Recorded summaries and hashes are in `evidence/` and `evidence/saturation/`.
`evidence/checks.json` records the scientific validation outcome and verifier hash.
The public module's JSON endpoint exports both campaigns with all 562 states
and 376 configurations. The initial protocol SHA-256 is
`acdfe7c64722a0c27766591d5425aa711972ab54967077390a03cbf5c78e6290`;
runner SHA-256 is
`657798d339dc3b9157ca3fa948d5163525a078d24f8dd913387b664ea367d281`.
