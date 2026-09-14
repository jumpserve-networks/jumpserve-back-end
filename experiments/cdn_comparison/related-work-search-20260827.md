# Structured Novelty Search: Regional-Demand Inference from CDN Caches

Search date: 2026-08-27
Status: systematic scoping search, not proof of nonexistence

## Bottom line

The broad research idea is **not new**. Prior work has inferred web usage from
geographically distributed DNS caches, inferred recent content access from cache
timing, detected HTTP caches through unauthenticated timing measurements, and
measured per-object hit/miss behavior across commercial CDN providers.

This search did **not** locate a prior study combining all of the following:

1. deployed shared commercial HTTP CDNs;
2. controlled, randomized, object-by-region recent-demand ground truth;
3. unauthenticated measurements from infrastructure outside the CDN under test;
4. evaluation on multiple independent CDN providers; and
5. prediction of assigned demand with held-out operating points and
   cross-region or cross-provider transfer tests.

The defensible novelty claim is therefore about that conjunction, not about the
general cache side channel, geographic inference, cache detection, or external
CDN measurement.

Recommended wording:

> To our knowledge, this is the first controlled, multi-provider empirical
> evaluation of whether an unauthenticated external observer can infer an HTTP
> object's recent region-specific request history on deployed commercial CDNs
> from public response metadata and timing.

This remains a qualified literature-search conclusion, not a guarantee that no
unindexed, unpublished, contemporaneous, or differently worded work exists.

## Scope used to test the claim

The target claim was decomposed into five independently screenable properties:

- **Object:** an individually addressable HTTP response object, not merely a
  domain, website, user, service, or aggregate traffic class.
- **Secret/outcome:** recent region-specific request history, initially binary
  zero versus one-or-more controlled prior requests.
- **System:** a deployed, shared, commercial HTTP CDN cache.
- **Observer:** an unauthenticated external client without provider logs,
  dashboards, APIs, credentials, or cooperation.
- **Evaluation:** controlled ground truth on at least two independent providers,
  with predictive performance rather than cache-state anecdotes alone.

A paper was treated as an exact predecessor only if it substantially satisfied
all five properties. Papers satisfying one or more properties were retained as
near-neighbors and must still be discussed as prior art.

## Search method

The search covered security, privacy, networking, web measurement, CDN, DNS,
proxy-cache, and information-centric-network terminology through 2026-08-27.
It used:

- general scholarly web search with venue/domain targeting for ACM, USENIX,
  IEEE, RAID, PETS, NDSS, and institutional repositories;
- OpenAlex title/abstract/metadata search across eight query families, reviewing
  the first 50 result slots per family before deduplication;
- Crossref title/abstract/metadata search across four broad query families,
  reviewing up to 100 journal result slots per family before deduplication;
- DBLP title search, with partial coverage because repeated searches encountered
  rate limiting; and
- backward and forward citation searches from the closest cache-privacy,
  browser-timing, DNS-cache, CDN-measurement, and web-cache papers. The largest
  forward searches included 306 works citing Felten and Schneider, 101 citing
  Acs et al., and 49 citing Mohaisen et al., as indexed by OpenAlex.

Representative query families included combinations and variants of:

- `CDN cache timing side channel privacy`
- `cache probing content popularity inference`
- `regional demand inference CDN object`
- `cache snooping HTTP proxy cache`
- `recent access inference shared cache`
- `geographic web usage cache monitoring`
- `commercial CDN per-object hit miss measurement`
- `external observer CDN cache status timing`
- `hot cold objects CDN timing experiment`
- `object popularity leakage content delivery network`

Exact-phrase searches for the proposed contribution wording and targeted
searches for `controlled`, `randomized`, `hot`, `cold`, `regional`, `commercial
HTTP CDN`, and `external observer` did not produce an exact-scope match.

The search deliberately included work outside HTTP CDNs because older papers use
different vocabulary for the same inference mechanism. YouTube-specific
measurement was not used to define the project scope, but general video-CDN
measurement papers were screened when they tested commercial cache behavior.

## Closest prior work

| Work | What it establishes | Why it is not the same study |
| --- | --- | --- |
| Akcan, Suel, and Bronnimann, *Geographic Web Usage Estimation By Monitoring DNS Caches* (LocWeb/WWW 2008) | Public, geographically distributed DNS caches reveal recently visited domains; repeated non-recursive probes support local-to-global access-rate estimates. This is the closest conceptual predecessor for geographic demand inference from remote cache state. | DNS records and domain-level usage, not HTTP objects or commercial CDN data caches. It does not evaluate a controlled, multi-CDN external classifier. |
| Felten and Schneider, *Timing Attacks on Web Privacy* (CCS 2000) | Browser-side caching can expose a user's prior browsing through timing. | The victim/browser setting, cache layer, and individual-history outcome differ from region-level demand at CDN edges. |
| Acs et al., *Cache Privacy in Named-Data Networking* (ICDCS 2013) | Timing a shared NDN router cache can reveal whether a nearby consumer recently requested named content. | NDN is an experimental future-network architecture, and the adversary/topology is generally a nearby consumer sharing routers, not an arbitrary external observer of a commercial HTTP CDN. |
| Mohaisen et al., *Timing Attacks on Access Privacy in Information Centric Networks and Countermeasures* (IEEE TDSC 2015) | Cached-versus-uncached timing can reveal whether a nearby user previously requested content in ICN. | ICN rather than deployed HTTP CDNs; no controlled, multi-provider commercial evaluation. |
| Bansal, Preibusch, and Milic-Frayling, *Cache Timing Attacks Revisited* (IFIP SEC 2015) | Robust cache attacks can operate at browser, OS, and Web-proxy levels and infer browsing activity. | It targets user history and conventional proxy/browser caches, not regional object demand in commercial CDN POPs. |
| Jia et al., *Anonymity in Peer-assisted CDNs* (PoPETs 2016) | Peers can infer resources browsed by other peers in deployed peer-assisted delivery systems. | The attacker is a participating peer and the leakage comes from peer-assisted protocols, not shared infrastructure HTTP cache residency or timing. |
| Cui et al., *Multi-CDN: Towards Privacy in Content Delivery Networks* (IEEE TDSC 2018) | Object popularity and user request patterns are sensitive to a curious CDN provider; proposes a cryptographic multi-CDN design. | The observer is the CDN with internal request visibility, not an unauthenticated external client; this is a defense/design paper rather than the proposed black-box empirical test. |
| Mirheidari et al., *Cached and Confused: Web Cache Deception in the Wild* (USENIX Security 2020) | External experiments cover Akamai, Cloudflare, CloudFront, and Fastly and show that geography and tiered caches affect whether attacker and victim share cached content. | The outcome is web-cache-deception exploitability and private-response leakage, not inference of regional demand from public objects. |
| Golinelli and Crispo, *Hidden Web Caches Discovery* (RAID 2024) | A black-box timing method distinguishes cached and origin responses without cache-status headers; it reports 89.6% estimated accuracy and scans the Tranco Top 50k. | The label is whether a cache is present/responding, not whether a controlled region recently requested an object; it uses paired/cache-busted requests rather than one-shot randomized demand observations. |
| Abdullah et al., *Edge Caching as Differentiation* (SIGCOMM 2025) | Eight AWS vantage points measure public response headers, latency, and per-object hit rates across five commercial cache providers to study QoE differentiation. This is the closest deployed-HTTP-CDN measurement predecessor. | It observes naturally encountered content and explains QoE disparities. It does not randomly assign regional demand, establish object-region demand ground truth, evaluate a demand-inference attacker, or report held-out demand-prediction performance. |
| Kumar, Bustamante, and Flores, *Who Holds the Steering Wheel?* (NINeS 2026) | Public measurements from global vantage infrastructure infer opaque CDN replica-selection strategies across 17 CDNs. | It infers CDN steering architecture, not object cache history or regional demand. It shows that global, unprivileged CDN inference methodology is not by itself novel. |

## Novelty assessment

### Claims contradicted by prior work

Do not claim any of the following:

- the first use of cache state to infer prior demand or access;
- the first geographic web-usage inference from remotely observable caches;
- the first timing attack that distinguishes cached from uncached content;
- the first unauthenticated or black-box detection of HTTP cache behavior;
- the first external, geographically distributed, per-object measurement of
  commercial CDN hit/miss behavior; or
- the first multi-CDN empirical measurement involving public response headers.

### Narrow claim not contradicted by the located work

No located paper performs a controlled intervention that independently assigns
fresh HTTP objects to hot/cold state by region, observes each object once from an
external vantage, treats demand assignment as ground truth, repeats the design
on multiple deployed commercial CDN providers, and evaluates unchanged rules on
held-out regions/days/providers.

That combination appears novel as of the search date. The paper should present
the contribution as **controlled empirical validation and transfer testing of a
known class of cache side channel in a new deployed setting**.

### Claim language to avoid and retain

Avoid:

> We introduce the first method for inferring regional demand from caches.

Avoid:

> We are the first to infer content popularity from CDN caching behavior.

Prefer:

> To our knowledge, this is the first controlled, multi-provider empirical
> evaluation of whether an unauthenticated external observer can infer an HTTP
> object's recent region-specific request history on deployed commercial CDNs
> from public response metadata and timing.

In the paper, immediately follow that sentence with a contrast to Akcan et al.,
Acs et al., Golinelli and Crispo, and Abdullah et al. The definition of recent
request history must remain binary and intervention-based unless later
experiments support stronger organic-popularity or request-volume claims.

## Search limitations and remaining due diligence

- A novelty search cannot prove that no prior work exists.
- DBLP coverage was incomplete because of rate limiting.
- Title/abstract indexing can miss a method described only in a paper's body,
  appendix, dissertation, patent, technical report, or industry presentation.
- The 2025 SIGCOMM paper is recent, and work published or posted after the search
  date can change the assessment.
- Search terminology is unusually fragmented across DNS cache snooping, browser
  history, web-proxy timing, ICN/NDN cache privacy, CDN measurement, and edge
  caching.

Before submission, repeat the search, inspect the forward citations of the four
closest papers, search the target venue's proceedings directly, and ask at least
one CDN-measurement or web-security researcher to challenge the related-work
boundary. Retain “to our knowledge” even after those checks.

## Primary references

1. Huseyin Akcan, Torsten Suel, and Herve Bronnimann. [Geographic Web Usage
   Estimation By Monitoring DNS Caches](https://research.engineering.nyu.edu/~suel/papers/dns.pdf).
   LocWeb at WWW, 2008. DOI: `10.1145/1367798.1367813`.
2. Edward W. Felten and Michael A. Schneider. [Timing Attacks on Web
   Privacy](https://collaborate.princeton.edu/en/publications/timing-attacks-on-web-privacy/).
   ACM CCS, 2000. DOI: `10.1145/352600.352606`.
3. Gergely Acs et al. [Cache Privacy in Named-Data
   Networking](https://named-data.net/publications/cache_privacy-icdcs13/).
   IEEE ICDCS, 2013. DOI: `10.1109/ICDCS.2013.12`.
4. Aziz Mohaisen et al. [Timing Attacks on Access Privacy in Information
   Centric Networks and Countermeasures](https://koasas.kaist.ac.kr/handle/10203/207473).
   IEEE TDSC 12(6), 2015. DOI: `10.1109/TDSC.2014.2382592`.
5. Chetan Bansal, Soren Preibusch, and Natasa Milic-Frayling. [Cache Timing
   Attacks Revisited](https://www.microsoft.com/en-us/research/publication/cache-timing-attacks-revisited-efficient-repeatable-browser-history-os-network-sniffing/).
   IFIP SEC, 2015. DOI: `10.1007/978-3-319-18467-8_7`.
6. Jinyuan Jia et al. [Anonymity in Peer-assisted CDNs: Inference Attacks and
   Mitigation](https://petsymposium.org/popets/2016/popets-2016-0041.php).
   PoPETs, 2016. DOI: `10.1515/popets-2016-0041`.
7. Shujie Cui et al. [Multi-CDN: Towards Privacy in Content Delivery
   Networks](https://www.cs.auckland.ac.nz/~asghar/PDFs/TDSC18-CDN.pdf).
   IEEE TDSC, 2018. DOI: `10.1109/TDSC.2018.2833110`.
8. Seyed Ali Mirheidari et al. [Cached and Confused: Web Cache Deception in
   the Wild](https://www.usenix.org/conference/usenixsecurity20/presentation/mirheidari).
   USENIX Security, 2020.
9. Matteo Golinelli and Bruno Crispo. [Hidden Web Caches
   Discovery](https://raid2024.github.io/papers/raid2024-40.pdf). RAID, 2024.
   DOI: `10.1145/3678890.3678931`.
10. Muhammad Abdullah et al. [Edge Caching as
    Differentiation](https://vtechworks.lib.vt.edu/items/d2d076f3-4141-462a-9f2b-b5262ed31d61).
    ACM SIGCOMM, 2025. DOI: `10.1145/3718958.3754350`.
11. Rashna Kumar, Fabian E. Bustamante, and Marcel Flores. [Who Holds the
    Steering Wheel? Opacity and Consolidation in CDN Replica
    Selection](https://drops.dagstuhl.de/entities/document/10.4230/OASIcs.NINeS.2026.23).
    NINeS, 2026. DOI: `10.4230/OASIcs.NINeS.2026.23`.
