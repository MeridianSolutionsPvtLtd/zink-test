import pandas as pd
from pipeline.customer_master import load_customer_master

cm = load_customer_master(retail_only=False)

# Build a mapping from _site_key to canonical site_id_name
key_to_name = {}
key_to_channel = {}
key_to_zone = {}

for _, r in cm.iterrows():
    k = str(r['_site_key']).strip()
    nm = str(r.get('site_name_master', '')).strip()
    ch = str(r.get('channel', '')).strip().upper()
    zn = str(r.get('zone', '')).strip()
    if k:
        key_to_name[k] = f"{k} - {nm}" if nm else k
        key_to_channel[k] = ch
        key_to_zone[k] = zn

print(f"Total canonical stores in map: {len(key_to_name)}")
print("Sample canonical mappings:")
for k in ['120', '110', '115', 'P001', 'P038', 'P147', 'TC3M', '8005', '528']:
    print(f"  {k:6} -> {key_to_name.get(k)} | ch={key_to_channel.get(k)} | zone={key_to_zone.get(k)}")
