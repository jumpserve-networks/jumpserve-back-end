"""Operator workflow commands. Remote writes require --persist and exact targets."""
import argparse
import json
from pathlib import Path
import uuid
import bridge
import intake
import importer
import runner
import store
from workflow import FIELDS, canonical, digest, identifier, now, numerical_protocol, paper_details, protocol_record, validate_record

def save(path, value):
    destination = Path(path)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open("xb") as stream: stream.write(canonical(value)+b"\n")

def persist_args(parser):
    parser.add_argument("--persist", action="store_true", help="Write with a trusted operator service credential; never used implicitly")
    parser.add_argument("--study-id", required=True, type=identifier)
    parser.add_argument("--actor-id", type=identifier, help="Existing owning user's UUID; private audit identity, not a public reviewer label")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command",required=True)
    create=commands.add_parser("intake",help="Create a scoped private paper record; no claims inferred")
    create.add_argument("--title",required=True);create.add_argument("--url",required=True);create.add_argument("--domain",required=True);create.add_argument("--scope",required=True);create.add_argument("--output",required=True);persist_args(create)
    retrieval=commands.add_parser("retrieve",help="Retrieve a bounded legally accessible original resource and preserve attempts")
    retrieval.add_argument("--url",required=True);retrieval.add_argument("--output-directory",required=True);retrieval.add_argument("--media-type",default="application/pdf")
    packet=commands.add_parser("text-packet",help="Extract page-referenced text without declaring substantive review")
    packet.add_argument("--file",required=True);packet.add_argument("--output",required=True)
    freeze=commands.add_parser("freeze",help="Freeze an inspected protocol or a versioned amendment")
    freeze.add_argument("--document",required=True);freeze.add_argument("--request-id",required=True,type=identifier);freeze.add_argument("--supersedes-id",type=identifier);freeze.add_argument("--version",type=int,default=1);freeze.add_argument("--amendment-reason");freeze.add_argument("--output",required=True);persist_args(freeze)
    template = commands.add_parser("template",help="Prepare a numerical protocol; inspect and freeze it separately")
    template.add_argument("--input",required=True); template.add_argument("--output",required=True)
    template.add_argument("--metric",required=True); template.add_argument("--units",required=True)
    template.add_argument("--tolerance",required=True,type=float); template.add_argument("--configurations",required=True)
    run = commands.add_parser("run",help="Execute a frozen campaign locally, optionally persist original bytes and output")
    run.add_argument("--input",required=True); run.add_argument("--protocol",required=True); run.add_argument("--campaign",required=True)
    run.add_argument("--request-id",required=True,type=identifier); run.add_argument("--output",required=True); persist_args(run)
    imported = commands.add_parser("import-ipv6",help="Preserve the existing register and prepare nine evidence-gap records")
    imported.add_argument("--assessment",required=True); imported.add_argument("--output",required=True); persist_args(imported)
    external=commands.add_parser('import-campaign',help='Validate and preserve documented external campaign records and exact original files; does not verify external execution')
    external.add_argument('--bundle',required=True);external.add_argument('--request-id',required=True,type=identifier);external.add_argument('--output',required=True);persist_args(external)
    source = commands.add_parser("source",help="Inventory locally retrieved legal bytes; does not claim substantive review")
    source.add_argument("--file",required=True); source.add_argument("--citation",required=True); source.add_argument("--url",required=True)
    source.add_argument("--version",required=True); source.add_argument("--kind",default="research"); source.add_argument("--role",default="Direct reference")
    source.add_argument("--request-id",required=True,type=identifier); source.add_argument("--output",required=True); persist_args(source)
    source.add_argument('--retrieval-record',help='Exact retrieve command audit; attempts and retrieved URL must match these original bytes')
    export = commands.add_parser("export",help="Export a complete published snapshot or an owner-scoped operator snapshot")
    export.add_argument("--study-id",required=True,type=identifier); export.add_argument("--owner",action="store_true"); export.add_argument("--actor-id",type=identifier)
    export.add_argument("--output",required=True)
    args = parser.parse_args()
    if getattr(args,'output',None) and Path(args.output).exists():parser.error('Preserve the prior output; select a new versioned output file before starting work.')
    if getattr(args,"persist",False) and not args.actor_id: parser.error("--persist requires --actor-id and a legitimate trusted operator credential")
    if args.command=="retrieve":
        value=intake.retrieve(args.url,args.output_directory,args.media_type);print(json.dumps(value));
        if value['status']!='retrieved':raise SystemExit(1)
        return
    if args.command=="text-packet":print(json.dumps(intake.text_packet(args.file,args.output)));return
    if args.command=='import-campaign':
        prepared,raw,originals=importer.prepare(args.bundle,args.request_id);save(args.output,prepared)
        if args.persist:importer.persist(prepared,raw,originals,args.study_id,args.actor_id)
        print(json.dumps(dict(import_id=args.request_id,original_sha256=prepared['original_sha256'],records=len(prepared['records']),aggregate_bytes=prepared['aggregate_bytes'],persisted=args.persist,scientific_validation='Not established by import')));return
    if args.command=="intake":
        details=paper_details(dict(title=args.title,paper_url=args.url,domain=args.domain,scope=args.scope,origin_module=None));save(args.output,dict(study_id=args.study_id,paper=details,persistence_requested=args.persist,persisted=False))
        if args.persist:store.rpc('research_create_study',dict(p_actor=args.actor_id,p_id=args.study_id,p_document=details))
        print(json.dumps(dict(study_id=args.study_id,persisted=args.persist,scientific_status='unreviewed intake; no claims inferred')));return
    if args.command=="freeze":
        document=json.loads(Path(args.document).read_bytes());existing=[]
        if args.persist:
            if not store.owns(args.study_id,args.actor_id):raise ValueError('Study ownership mismatch.')
            existing=store.rows('protocols',{'id':'eq.'+args.request_id,'study_id':'eq.'+args.study_id,'limit':1})
        if existing:
            original=existing[0]
            if original['document']!=document or original['supersedes_id']!=args.supersedes_id or original['version']!=args.version or original['amendment_reason']!=args.amendment_reason:raise ValueError('Protocol ID already used; preserve the original and create an amendment.')
            row={key:original[key] for key in ('id',*FIELDS['protocols'])}
        else:row=protocol_record(document,args.request_id,args.supersedes_id,args.version,args.amendment_reason)
        save(args.output,row)
        if args.persist and not existing:store.append(args.study_id,args.actor_id,'protocols',row)
        print(json.dumps(dict(protocol_id=row['id'],sha256=row['sha256'],frozen_at=row['frozen_at'],persisted=args.persist)));return
    if args.command=="template":
        raw=Path(args.input).read_bytes(); configurations=json.loads(Path(args.configurations).read_bytes())
        document=numerical_protocol(digest(raw),args.metric,args.units,args.tolerance,configurations)
        save(args.output,document)
        print("Protocol template saved. Disclose prior exposure, inspect methods and limits, then freeze through the authenticated API.")
        return
    if args.command=="export":
        if args.owner and (not args.actor_id or not store.owns(args.study_id,args.actor_id)): raise ValueError("Owner export requires verified ownership.")
        value=store.snapshot(args.study_id,owner=args.owner,maximum=100000,byte_budget=128_000_000)
        if value is None: raise ValueError("No accessible study snapshot.")
        save(args.output,value); print(json.dumps({"coverage":value["coverage"],"sha256":digest(Path(args.output).read_bytes())})); return
    if args.command=="import-ipv6":
        value=bridge.ipv6(args.assessment,args.study_id)
        save(args.output,value)
        if args.persist:
            store.rpc("research_create_study",dict(p_actor=args.actor_id,p_id=args.study_id,p_document=value["paper"]))
            store.append_bundle(args.study_id,args.actor_id,value["records"])
        print(json.dumps({"study_id":args.study_id,"records":len(value["records"]),"gaps":sum(r["kind"]=="gaps" for r in value["records"]),"persisted":args.persist,"provenance":value["provenance"]})); return
    if args.persist and not store.owns(args.study_id,args.actor_id): raise ValueError("Study ownership mismatch.")
    if args.command=="source":
        path=Path(args.file)
        if path.stat().st_size>10_000_000: raise ValueError("Source exceeds the declared 10 MB operator source limit; retain original bytes and amend the protocol rather than truncate.")
        raw=path.read_bytes(); evidence={"original_sha256":digest(raw),"bytes":len(raw),"retrieval_attempts":[{"url":args.url,"status":"operator-provided local bytes","retrieved_at":None,"inspected_at":now(),"limitation":"Original network retrieval time and attempts were not supplied; file modification time is not a retrieval time."}]}
        retrieved_url=None;audit_raw=None
        if args.retrieval_record:
            audit_path=Path(args.retrieval_record)
            if audit_path.stat().st_size>1_000_000:raise ValueError('Retrieval record exceeds the 1 MB metadata limit.')
            audit_raw=audit_path.read_bytes();audit=json.loads(audit_raw)
            if audit.get('status')!='retrieved' or audit.get('requested_url')!=args.url or audit.get('sha256')!=digest(raw) or audit.get('bytes')!=len(raw) or not isinstance(audit.get('attempts'),list):raise ValueError('Retrieval audit does not identify these original source bytes.')
            evidence['retrieval_attempts']=audit['attempts'];evidence['retrieval_audit_sha256']=digest(audit_raw);retrieved_url=audit.get('retrieved_url')
        row=dict(id=args.request_id,citation=args.citation,source_url=args.url,retrieved_url=None,kind=args.kind,role=args.role,retrieved_version=args.version,sha256=digest(raw),byte_count=len(raw),access_status="retrieved",review_status="retrieved-unreviewed",review_definition="Complete review requires substantive examination of all sections, figures, tables, appendices and available supplements, with notes and explicit omissions.",examined="Byte identity only; no substantive review performed by this command.",unexamined="All substantive content.",retrieval_attempts=evidence["retrieval_attempts"],reviewer={"identity":"Automated byte inventory; no substantive reviewer","type":"AI","independence":"Automation only; no human or independent review asserted"},findings="No scientific finding; hash verification does not establish review completeness.",limitations="Direct-source inventory only; legal access is the operator's declared responsibility. No bibliography is recursively expanded.")
        row['retrieved_url']=retrieved_url;row['retrieval_attempts']=evidence['retrieval_attempts']
        row=validate_record("sources",row); save(args.output,{"record":row,"provenance":evidence})
        if args.persist:
            artifact=store.artifact(args.study_id,args.actor_id,raw,str(uuid.uuid5(uuid.UUID(args.request_id),"original-source")),"application/pdf" if raw[:1024].lstrip().startswith(b'%PDF-') else "application/octet-stream",{"source_id":args.request_id,"transformation":"none"})
            entries=[dict(kind="artifacts",record=artifact)]
            if audit_raw is not None:entries.append(dict(kind='artifacts',record=store.artifact(args.study_id,args.actor_id,audit_raw,str(uuid.uuid5(uuid.UUID(args.request_id),'retrieval-audit')),'application/json',{'source_id':args.request_id,'transformation':'none; original retrieval audit'})))
            store.append_bundle(args.study_id,args.actor_id,entries+[dict(kind="sources",record=row)])
        print(json.dumps({"source_id":args.request_id,"sha256":digest(raw),"persisted":args.persist,"review_status":"retrieved-unreviewed"})); return
    if args.command=="run":
        path=Path(args.input); protocol=json.loads(Path(args.protocol).read_bytes()); campaign=json.loads(Path(args.campaign).read_bytes())
        if path.stat().st_size>10_000_000: raise ValueError("Input exceeds the hard operator limit; no truncated input is analyzed.")
        raw=path.read_bytes()
        if args.persist:
            prior=store.rows("runs",{"id":"eq."+args.request_id,"study_id":"eq."+args.study_id,"limit":1})
            if prior:
                if prior[0]["input_sha256"]!=digest(raw) or prior[0]["campaign_id"]!=campaign["id"]: raise ValueError("Run ID was already used for different inputs.")
                save(args.output,dict(run=prior[0],replayed=True,limitation='Existing run returned without re-execution; export the study for its measurements.'))
                print(json.dumps(dict(run_id=args.request_id,replayed=True,persisted=True,status=prior[0]['status'])));return
            saved_campaign=store.rows('campaigns',{'id':'eq.'+campaign['id'],'study_id':'eq.'+args.study_id,'limit':1})
            saved_protocol=store.rows('protocols',{'id':'eq.'+protocol['id'],'study_id':'eq.'+args.study_id,'limit':1})
            if not saved_campaign or any(campaign.get(key)!=saved_campaign[0].get(key) for key in ('protocol_id','adapter','experiment_type','planned_units')) or not saved_protocol or saved_protocol[0]['document']!=protocol['document'] or saved_protocol[0]['sha256']!=protocol['sha256']:
                raise ValueError('Local campaign or protocol differs from the stored frozen configuration; preserve it and use an amendment.')
        value=runner.execute(raw,protocol,campaign,args.request_id);save(args.output,value)
        if args.persist:
            artifacts=[store.artifact(args.study_id,args.actor_id,data,str(uuid.uuid5(uuid.UUID(args.request_id),label)),"application/json",{"run_id":args.request_id,"transformation":label}) for label,data in (("original-input",raw),("parsed-output",canonical(value)))]
            entries=[dict(kind="artifacts",record=r) for r in artifacts]+[dict(kind="runs",record=value["run"])]
            for kind in ("published_values","measurements","summaries"): entries.extend(dict(kind=kind,record=r) for r in value[kind])
            store.append_bundle(args.study_id,args.actor_id,entries)
        print(json.dumps({"run_id":args.request_id,"status":value["run"]["status"],"reason":value["run"]["reason"],"persisted":args.persist,"usage":value["run"]["usage"]}))
        if value["run"]["status"]=="failed": raise SystemExit(1)

if __name__=="__main__": main()
