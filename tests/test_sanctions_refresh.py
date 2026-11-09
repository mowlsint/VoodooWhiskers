import csv
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import refresh_sanctions as sanctions


def test_imo_checksum_and_no_name_matching():
    assert sanctions.valid_imo("IMO9179842")=="9179842"
    assert sanctions.valid_imo("9179843")==""
    assert sanctions.valid_imo("ANGARA")==""


def test_uk_preamble_and_designation_type():
    text="Report Date: 03-Nov-2026\nUnique ID,Designation Type,Name 6,IMO number,Sanctions Imposed,Name type\nRUS2183,Ship,ANGARA,IMO9179842,Shipping sanctions,Primary Name\nRUSX,Entity,Company,IMO9179842,Asset freeze,Primary Name\n"
    rows,meta=sanctions.parse_uk(text)
    assert len(rows)==1 and rows[0]["source_tag"]=="UK_RUS2183"
    assert meta["source_report_date"]=="03-Nov-2026"


def test_ofac_only_vessel_and_labelled_imo():
    output=io.StringIO();writer=csv.writer(output)
    writer.writerow(["37059","ANGARA","vessel","RUSSIA-EO14024","","","","","","","","Vessel Registration Identification IMO 9179842;"])
    writer.writerow(["123","COMPANY","entity","","","","","","","","","IMO 9179842"])
    rows,_=sanctions.parse_ofac(output.getvalue())
    assert len(rows)==1 and rows[0]["imo"]=="9179842"


def test_eu_table_header_and_source_date():
    rows,meta=sanctions.parse_eu('<p>Last updated 24/07/2026</p><table><tr><th>Vessel name</th><th>IMO no.</th><th>Date of application</th></tr><tr><td>Angara</td><td>9179842</td><td>25.06.2024</td></tr></table>')
    assert rows[0]["imo"]=="9179842" and meta["source_report_date"]=="24/07/2026"
    rows,_=sanctions.parse_eu('<h2>List of vessels (DPRK)</h2><table><tr><th>Vessel name</th><th>IMO number</th><th>Date</th><th>Subject to</th></tr><tr><td>Test</td><td>9179842</td><td>21.06.2017</td><td>Asset freeze</td></tr></table>')
    assert rows[0]["source_tag"]=="EU_DPRK_Vessel" and rows[0]["measures"]=="Asset freeze"


def test_curated_flags_preserved_new_sanction_not_automatically_shadow_fleet():
    original=[{"imo":"9179842","name":"CURATED ANGARA","track_shadowfleet":"true","track_falseflag":"true","notes":"analyst note","source_list":"EU_Annex_XLII"},
              {"imo":"9915090","name":"KEEP ME","source_list":"OFAC_SDN_45034","track_sanctions":"true"}]
    snapshots={"uk":{"source_url":"https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv","vessels":[
        {"imo":"9179842","name":"ANGARA","source_tag":"UK_RUS2183"},
        {"imo":"9915105","name":"KORYAK FSU","source_tag":"UK_NEW"}]}}
    rows,changes=sanctions.merge_watchlist(original,snapshots,"2026-11-09")
    assert len(rows)==3 and changes["automatically_deleted"]==0
    assert rows[0]["name"]=="CURATED ANGARA" and rows[0]["notes"]=="analyst note"
    assert rows[0]["track_shadowfleet"]=="true" and rows[0]["track_falseflag"]=="true"
    assert rows[1].get("sanctions_verification_status") is None
    assert rows[2]["track_sanctions"]=="true" and rows[2]["track_shadowfleet"]=="false"


def test_absence_only_review_after_claimed_sources_were_checked():
    rows,_=sanctions.merge_watchlist([{"imo":"9179842","source_list":"EU_Annex_XLII;OFAC_SDN_37059","track_sanctions":"true"}],
        {"eu":{"source_url":"eu","vessels":[]},"ofac":{"source_url":"ofac","vessels":[]}},"2026-11-09")
    assert rows[0]["sanctions_verification_status"]=="manual_review_required"
    assert rows[0]["track_sanctions"]=="true"


def test_parse_failure_retains_source_snapshot(tmp_path,monkeypatch):
    monkeypatch.setattr(sanctions,"SOURCE_DIR",tmp_path)
    previous='{"checked_at":"2026-01-01T00:00:00+00:00","vessels":[]}'
    (tmp_path/"uk_vessels.json").write_text(previous)
    invalid=tmp_path/"invalid.csv";invalid.write_text("blocked or changed schema")
    with pytest.raises(ValueError):
        sanctions.get_source("uk",None,datetime.now(timezone.utc),local_file=invalid)
    assert (tmp_path/"uk_vessels.json").read_text()==previous
