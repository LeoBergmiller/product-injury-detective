"""Score a hand-labelled 60-row sample of product_recall_xwalk against the agent filter.

Run from the repo root: uv run python build/audit_recall_sample.py
"""
import sys, json, duckdb
sys.path.insert(0, '.')
from detective.recall_match import product_nouns, specific_phrases, plausible_for_product, belongs_elsewhere
# my labels: (neiss_code, recall_id) -> True/False
L = {
(670,2194):0,(4074,6707):1,(1395,1254):1,(1439,1246):1,(5040,8711):1,(661,5736):1,(1552,269):1,(1395,10768):1,(1555,5183):1,(661,149):1,
(1552,464):1,(1200,7819):0,(4074,1222):0,(5040,2102):1,(1242,5439):1,(1395,4922):1,(1345,3656):1,(5024,8680):0,(1395,4238):1,(4074,9056):1,
(1931,5501):0,(1864,9377):0,(4078,9611):0,(1141,11011):0,(4078,10043):0,(648,1059):0,(1205,8276):0,(1819,10810):0,(1817,2385):0,(679,1509):0,
(3219,6769):0,(1141,10100):0,(3223,4112):0,(1843,5290):0,(1679,6162):0,(1205,4006):0,(1345,10618):1,(1267,9323):0,(648,6591):0,(1843,7821):0,
(1807,9381):0,(1842,887):0,(3274,3520):0,(612,7895):0,(1212,5177):0,(5040,10924):1,(1135,1902):0,(1552,10453):0,(1135,9328):0,(5034,5117):0,
(1552,9793):0,(4074,1709):1,(3246,806):1,(3277,600):1,(884,10794):1,(1878,2199):0,(1552,9958):0,(1842,10364):0,(1414,894):0,(687,107):0}
c = duckdb.connect('data/processed/injuries.duckdb', read_only=True)
ctx = json.load(open('data/processed/agent_context.json'))
titles = {p["code"]: p["title"] for p in ctx["products"]}
spec = specific_phrases(titles)
conf = dict(((r[0],r[1]),r[2]) for r in c.sql("select neiss_code, recall_id, confidence from product_recall_xwalk where recall_id is not null").fetchall())
out=[]
from collections import Counter; st=Counter()
for (code,rid),ok in L.items():
    t = c.sql("select title from recalls where recall_id=?", params=[rid]).fetchall()[0][0]
    pn = [r[0] for r in c.sql("select name from recall_products where recall_id=? and name is not null", params=[rid]).fetchall()]
    noun = plausible_for_product(t, pn, product_nouns(titles[code]))
    other = belongs_elsewhere(t, pn, code, spec)
    kept = bool(noun and not other)
    cf = conf[(code,rid)]
    st[(cf,'n')]+=1; st[(cf,'ok')]+=ok; st[(cf,'kept')]+=kept; st[(cf,'kept_ok')]+=kept and ok
    out.append(dict(confidence=cf, neiss_code=code, product_title=titles[code], recall_id=rid, recall_title=t, correct=bool(ok), kept_by_agent_filter=kept))
json.dump(out, open(sys.argv[1] if len(sys.argv) > 1 else "docs/audit/recall_xwalk_sample.json",'w'), indent=1)
for cf in ['high','medium','low']:
    print(cf, {k:st[(cf,k)] for k in ['n','ok','kept','kept_ok']})
print('missed by filter (wrong but kept):', [(o['neiss_code'],o['recall_id']) for o in out if o['kept_by_agent_filter'] and not o['correct']])
print('dropped but correct:', [(o['neiss_code'],o['recall_id']) for o in out if not o['kept_by_agent_filter'] and o['correct']])
