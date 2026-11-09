#!/usr/bin/env python3
"""Export auditable study figures from collected evidence, with no database key."""
import argparse
import hashlib
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("MPLCONFIGDIR", str(HERE/"results"/"matplotlib-cache"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from analysis import configuration_id

COLORS = ["#164e87", "#b44328", "#387640", "#78449c"]
LABELS = {"bbr":"BBRv3", "bbr1":"BBRv1"}


def name(cca):
    return LABELS.get(cca, cca.upper())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results",type=Path,default=HERE/"results"/"campaign-v1")
    parser.add_argument("--allow-incomplete",action="store_true",help="Watermarked layout previews only")
    args = parser.parse_args()
    manifest = json.loads((HERE/"campaign-v1.json").read_text())
    report = json.loads((args.results/"analysis.json").read_text())
    incomplete = report["missing"] > 0 or report["counts"]["completed"] != len(manifest["trials"])
    if incomplete and not args.allow_incomplete:
        raise ValueError("Refusing final figures for an incomplete campaign")
    if not incomplete and any(s["matched_pairs"] != s["expected_pairs"] for s in report["summaries"]):
        raise ValueError("Summary coverage is inconsistent with campaign completion")
    configs = {configuration_id(manifest["id"],t["config"]):t["config"] for t in manifest["trials"]}
    cells = {(c["configuration_id"],c["flow_index"]):c for c in report["cells"]}
    out = args.results/("figure-previews" if incomplete else "figures")
    out.mkdir(exist_ok=True)
    plt.rcParams.update({"font.size":10,"axes.spines.top":False,"axes.spines.right":False,
                         "svg.fonttype":"none","pdf.fonttype":42})
    exported = {}

    def save(fig, stem, caption):
        if incomplete:
            fig.text(.5,.5,"INCOMPLETE CAMPAIGN",rotation=20,ha="center",va="center",fontsize=28,alpha=.18)
        for extension in ["pdf","svg","png"]:
            path = out/f"{stem}.{extension}"
            fig.savefig(path,dpi=180,bbox_inches="tight")
            exported[path.name] = {"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"caption":caption}
        plt.close(fig)

    # Draw interval endpoints directly: percentile intervals need not contain
    # the plug-in estimate, so negative errorbar magnitudes would be incorrect.
    def interval(ax, x, value, low, high, color, label=None, offset=0):
        if value is None:
            return
        ax.plot(value,x+offset,"o",color=color,label=label,markersize=5)
        if low is not None and high is not None:
            ax.hlines(x+offset,low,high,color=color,lw=1.7)
            ax.vlines([low,high],x+offset-.045,x+offset+.045,color=color,lw=1)

    groups = [("homogeneous_bbr","bbr1/bbr1","Common RTT · BBRv1"),
              ("homogeneous_bbr","bbr/bbr","Common RTT · BBRv3"),
              ("common_shift","bbr/hybla/cubic/reno","Common +50 ms shift"),
              ("imperfect_equalization","illinois/westwood/cubic/reno","Imperfect equalization")]
    fig, axes = plt.subplots(2,2,figsize=(11,7),layout="constrained")
    for ax,(family,cca_group,title) in zip(axes.flat,groups):
        summaries = sorted([s for s in report["summaries"] if s["family"]==family and s["cca_group"]==cca_group],key=lambda s:s["flow_index"])
        for y,s in enumerate(summaries):
            for condition,color,offset,label in [("baseline",COLORS[0],-.12,"Baseline"),("treatment",COLORS[1],.12,"Intervention")]:
                interval(ax,y,s[condition+"_delta"],s[condition+"_ci_low"],s[condition+"_ci_high"],color,label if y==0 else None,offset)
        ax.set_yticks(range(len(summaries)),[f"{name(s['cca'])} · flow {s['flow_index']}" for s in summaries])
        ax.invert_yaxis(); ax.set_title(title); ax.set_xlabel("Delay sensitivity δ (log₂ max/min mean goodput)")
        ax.set_xlim(left=0);ax.grid(axis="x",alpha=.18)
    handles, labels = axes[0,0].get_legend_handles_labels()
    if handles:
        fig.legend(handles,labels,frameon=False,loc="outside upper center",ncols=2)
    save(fig,"delay-sensitivity","Points: configuration-mean sensitivity; whiskers: pointwise 95% paired whole-trial bootstrap intervals (2,000 resamples). Five paired repetitions per assignment; finite grids, fixed ACK delay. Published paper values are not these measurements.")

    delays = [6,18,30,42,54]
    fig, axes = plt.subplots(2,2,figsize=(10,8),layout="constrained")
    for row,cca in enumerate(["bbr1","bbr"]):
        for col,baseline in enumerate([True,False]):
            ax=axes[row,col]; matrix=np.full((5,5),np.nan);counts=np.zeros((5,5),dtype=int)
            for identity,c in configs.items():
                if c["ccas"] != [cca,cca] or (c["treatment"]=="baseline") != baseline: continue
                cell=cells.get((identity,1))
                if not cell or cell["mean_share"] is None: continue
                i,j=delays.index(c["delays_ms"][1]),delays.index(c["delays_ms"][0])
                matrix[i,j]=cell["mean_share"]*100;counts[i,j]=cell["matched_repetitions"]
            img=ax.imshow(matrix,vmin=0,vmax=100,cmap="viridis",origin="lower")
            for i in range(5):
                for j in range(5):
                    if np.isfinite(matrix[i,j]): ax.text(j,i,f"{matrix[i,j]:.1f}%\nn={counts[i,j]}",ha="center",va="center",fontsize=8,color="white" if matrix[i,j]<55 else "black")
            ax.set_xticks(range(5),delays);ax.set_yticks(range(5),delays)
            ax.set_xlabel("Flow 1 configured base RTT (ms)");ax.set_ylabel("Flow 2 configured base RTT (ms)")
            ax.set_title(f"{name(cca)} · {'baseline' if baseline else 'common 100 ms RTT'}")
    fig.colorbar(img,ax=list(axes.flat),label="Flow 1 mean share of combined receiver goodput (%)",shrink=.8)
    save(fig,"bbr-throughput-share","Common 0–100% color scale. Each cell averages the within-trial flow-1 share across matching pairs. n is whole-trial paired repetitions, not one-second samples. Configured delay is a round-trip increment applied once.")

    records=[]
    for path in sorted(args.results.glob("worker-*/preflight/direction.json")):
        worker=int(path.parents[1].name.split("-")[-1])
        records.extend({**r,"worker":worker} for r in json.loads(path.read_text())["records"])
    fig,axes=plt.subplots(1,2,figsize=(10,4),layout="constrained")
    conditions=["baseline","ack_plus_60","forward_plus_60"]
    # Use recorded labels; fail explicitly if a schema changes.
    require_labels={r["treatment"] for r in records}
    if require_labels != set(conditions): raise ValueError(f"Unexpected latency conditions: {require_labels}")
    for ax,metric,title in [(axes[0],"median_forward_ms","Forward packet latency"),(axes[1],"median_rtt_ms","Round-trip time")]:
        for x,condition in enumerate(conditions):
            selected=[r for r in records if r["treatment"]==condition]
            ax.scatter([x+(r["worker"]-3.5)*.018 for r in selected],[r[metric] for r in selected],s=24,color=COLORS[x])
        ax.set_xticks(range(3),["Baseline","ACK +60 ms","Forward +60 ms"])
        ax.set_yscale("log");ax.set_ylabel("Latency (ms, logarithmic scale)");ax.set_title(title);ax.grid(axis="y",alpha=.18)
    save(fig,"packet-direction-control","Each point is one worker's median of 1,000 uncongested UDP timestamp probes. Eight workers per condition. Forward +60 ms is a positive control; packet latency is not application completion time.")
    metadata={"campaign_id":manifest["id"],"incomplete":incomplete,"analysis_sha256":hashlib.sha256((args.results/"analysis.json").read_bytes()).hexdigest(),
              "script_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),"matplotlib_version":matplotlib.__version__,"files":exported}
    (out/"figure-manifest.json").write_text(json.dumps(metadata,indent=2)+"\n")
    print(json.dumps({"directory":str(out),"files":len(exported),"incomplete":incomplete}))


if __name__=="__main__":main()
