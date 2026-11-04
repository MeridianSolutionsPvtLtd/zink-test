from pipeline.customer_master import load_customer_master
cm = load_customer_master(retail_only=False)

for code in ['101', '102', '103', '106', '110', '114', '115', '116', '120', '294', 'P001', 'P105', 'P118', 'P147']:
    rows = cm[(cm['_site_key'] == code) | (cm['site_code'] == code) | (cm['site_manual_code'] == code)]
    print(f"\n=== Testing Code: {code} ({len(rows)} rows) ===")
    for _, r in rows.iterrows():
        print(f"  key={r['_site_key']} | sc={r['site_code']} | manual={r['site_manual_code']} | ch={r['channel']} | name={r['site_name_master'][:40]} | cust={r['customer_name'][:30]}")
