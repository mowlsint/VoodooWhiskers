"""Build the static aircraft directory and review queue; no positions or provider calls."""
import argparse
import json
from pathlib import Path
from special_aircraft_registry import load_registry

ROOT = Path(__file__).resolve().parents[1]
SAR = ("search_and_rescue", "sar_support", "offshore_sar", "hems_maritime_context")
def build(root=ROOT):
    registry, selected = load_registry(root/"config/special_aircraft_registry.json")
    reference=json.loads((root/"config/aircraft_authority_reference.json").read_text())
    assert reference["schema"]=="VOODOO_AIRCRAFT_AUTHORITY_REFERENCE_V1" and reference["complete"] is False
    source={s["id"]:s["url"] for s in registry["sources"]}
    def urls(ids):return sorted({source[i] for i in ids})
    groups={};rows=[]
    for r in registry["aircraft"]:
        country=r["country"] or r["organisation_scope"]
        key=(country,r["organisation"])
        o=groups.setdefault(key,dict(country=country,name=r["organisation"],kind="existing_registry_organisation",records=0,selected=0,roles=[],sources=[]))
        o["records"]+=1;o["selected"]+=int(r["enabled"])
        o["roles"]=sorted(set(o["roles"]+r["roles"]))
        o["sources"]=sorted(set(o["sources"]+urls(r["role_source_ids"])))
        classes=[label for role,label in zip(SAR,("SAR primary","SAR support","Offshore SAR","Maritime HEMS")) if role in r["roles"]]
        if "offshore_transport" in r["roles"]:classes.append("Offshore transport (not SAR)")
        if not any(x!="Offshore transport (not SAR)" for x in classes) and any(any(k in x.lower() for k in ("sar","rescue","medevac","aeromedical")) for x in r["source_roles"]):classes.append("SAR capability candidate — review")
        reason="selected with evidenced identity" if r["enabled"] else "identity_conflict" if r["identity_conflict"] else "historical_registration_replaced" if r["service_status"]=="historical_registration_replaced" else "missing_evidenced_icao24"
        rows.append(dict(record_id=r["record_id"],country=country,organisation=r["organisation"],operator=r["operator"] or "",aircraft_type=r["aircraft_type"] or "",registration=r["registration"] or r["military_serial"] or "",selected=r["enabled"],review_reason=reason,reviewed_at=r["reviewed_at"],sar_classes=classes,roles=r["roles"],role_sources=urls(r["role_source_ids"]),identity_sources=urls(r["identity_source_ids"])))
    return dict(schema="VOODOO_AIRCRAFT_DIRECTORY_V1",complete=False,registry_updated_at=registry["updated_at"],authority_reference_updated_at=reference["updated_at"],organisations=sorted(groups.values(),key=lambda x:(x["country"],x["name"].casefold())),programmes=reference["programmes"],aircraft=rows,assessment_limit="Registry roles describe documented capability or a research lead, not current tasking, readiness, rescue occurrence or actual coverage.")
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,default=ROOT/"public/downloads/aircraft_directory.json")
    args=parser.parse_args()
    result=build()
    body=json.dumps(result,ensure_ascii=False,separators=(",",":"))+"\n"
    if len(body.encode())>1024*1024:raise ValueError("Directory exceeds 1 MiB")
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(body,encoding="utf-8")
    print(json.dumps({"aircraft":len(result["aircraft"]),"organisations":len(result["organisations"]),"programmes":len(result["programmes"])}))
if __name__=="__main__":main()
