# NINeS 2026 propagation-delay reproduction

User request (2026-09-27): download and thoroughly review DOI
10.4230/OASIcs.NINeS.2026.27 and all cited research papers, devise and execute a
reproduction, store measured results in Supabase, and create a JumpServe module.
AWS experiment resources and backend work are authorized. Website deployment and
pushes to main require a separate request.

## Completed campaign

- Downloaded the primary publisher PDF into `literature/primary.pdf`.
- Cloned the linked authors' artifact into the ignored `author-artifact/` folder.
- The publisher landing page lists 57 references; the PDF has 60, now catalogued.
- Primary paper and linked artifact reviewed; findings are in `PRIMARY_READING.md`.
  Reference access, version and individual reading status are in `literature.json`;
  substantive reading notes are in `LITERATURE_READING.md`. All 49 retrieved PDF
  references have been reviewed, plus two complete author-manuscript web
  texts and six cited software/manual/press-release resources. References 29, 44
  and 45 remain inaccessible; do not claim those papers were read. Version and
  figure-access limitations are recorded individually. Reference 58's available
  manuscript clips the right side of several tables; its reading status explicitly
  distinguishes this limitation from complete full-text access.
- Frozen protocol and manifest define 820 trials: 500 homogeneous BBRv1/v3 trials
  and 320 heterogeneous trials, with five paired repetitions per configuration.
  The intervention is calibrated fixed ACK delay, not the paper's adaptive
  Algorithm 1. The grid, measurement method and software version differences
  make this a partial replication, with untested claims explicitly identified.
- Eight c7i.large EC2 workers launched on 2026-09-28 at 00:34–00:40 UTC. Every
  worker passed kernel, single-flow shaping and ACK-versus-forward-delay checks.
  The temporary kernel builder has terminated. Worker resources and hashes are
  recorded in the infra repository's research directory. All eight workers were
  verified terminated after collection. No database credentials went to EC2.
- Real receiver interval measurements, configurations, calibration, trial status,
  literature and summaries are stored in the existing JumpServe Supabase project
  (`regphejnlvfpyokpniny`). Dedicated research tables have RLS enabled, public
  SELECT access and no client write access. Private S3 retains raw evidence.
- The website module is `/module/propagation-delay-study/test-results`, with
  methods, literature, matched-trial details, heatmaps, time series and JSON
  downloads. Verification used the local application. Incomplete grids do not display a final
  sensitivity estimate; confidence intervals and assessments wait for all planned
  paired repetitions. Those display guards do not change the frozen estimator.
- Collection completed at 04:08:02 UTC on 2026-09-28: 820 valid trials, no failed
  or invalid main-campaign trials, 2,280 flows, 249,602 receiver intervals and
  24,000 UDP measurements. Supabase status is `completed`; all 456 cells have five
  matched repetitions. The full raw-evidence and unauthenticated public-export
  audits passed. Final findings and limitations are in `RESULTS.md`.
- All 12 paired sensitivity-reduction intervals are positive. Figure 7b's
  below-one threshold did not reproduce for Illinois, CUBIC or Reno in this
  apparatus; Westwood's interval is below one. This is a partial replication,
  not validation of every published experiment.

## Reproducing ingestion and analysis

From this repository, sync the dedicated campaign evidence, then run the trusted
local controller (its credentials remain in the existing CLI credential store):

```sh
aws s3 sync s3://jumpserve-nines2026-395567831870-us-east-1/campaigns/nines2026-balanced-v1/ experiments/nines2026_delay_equalization/results/campaign-v1/ --only-show-errors
python3 experiments/nines2026_delay_equalization/persist.py analyze
python3 experiments/nines2026_delay_equalization/persist.py literature
python3 experiments/nines2026_delay_equalization/audit_evidence.py
```

Do not rerun `seed`, alter the frozen runner/manifest/protocol, silently replace
failed trials, or mix pilot measurements into the main campaign. On completion,
verify all expected evidence and relational records, write the final assessment,
and confirm all disposable workers have terminated.

`persist.py finalize` independently checks all raw receiver intervals, applied
queue/rate traces, fixed code hashes, calibration, CPU thresholds, planned pairs,
preflights and normalized database counts before setting the completed status.
The read-only audit rejects tampered byte counts, a wrong applied queue, and an
incomplete campaign; these cases were exercised against a copy of real evidence.

Use `requirements-analysis.txt` in the isolated `.venv` for literature review and
figure exports. After final analysis, `export_figures.py` writes PDF, SVG and PNG
figures plus captions and an SHA-256 manifest into the ignored result directory.
Without a complete campaign it refuses final exports; `--allow-incomplete`
produces visibly watermarked previews in a separate directory.

After finalization, run `python3 experiments/nines2026_delay_equalization/audit_public_exports.py`
against the local site (default port 3001). It checks every public summary/cell,
protocol/schedule hashes, all scheduled trial identities, and eight selected trial
exports against raw receiver bytes, interval durations, flow identities and
calibration. It sends no cookies or credentials and writes an audit with export
hashes. The selection is both trials from the first scheduled block in each of
the four experiment/CCA groups; it is not a statistical sample or an additional
experimental replication.

Frontend verification: 97 tests, lint and production build passed; browser checks
covered public access, light/dark themes, mobile layout, experiment/configuration
selection, pagination, matched-trial navigation, JSON exports, and invalid-trial
404 handling. These checks preceded the separately authorized commit and push
to main; production deployment was not part of this verification.

## Integrity requirements

- Separate reproduction observations from the paper's reported results.
- Record software/kernel versions, source hashes, topology, direction and units
  of delay, bottleneck queue, randomization, repetitions, failures, and exclusions.
- Do not claim to have downloaded or read a reference unless its complete text
  was obtained and reviewed. Keep an access/read manifest.
- Preserve unrelated CDN experiment code, frozen protocols, and infrastructure.
- Never place Supabase administrative credentials on experiment instances.

Final browser verification caught and fixed an SVG title hydration mismatch by rendering one interpolated string. A fresh browser session reported no page errors after the fix; all 97 tests, lint and the production build passed again. Final sensitivity, heatmap and packet-direction figures were visually checked; the shared legend sits outside data panels.

The reproducibility bundle is `results/campaign-v1/nines2026-report-v1.zip`, with
selected task source files from all three repositories, final analysis, audits,
public JSON exports, figures and a file-hash manifest. Its private S3 destination
is `reports/nines2026-balanced-v1/v1/` in the research evidence bucket. Original
raw traces stay under the separate campaign prefix; downloaded reference PDFs
are kept locally rather than redistributed in this bundle.
