import pandas as pd
dm = pd.read_parquet('data/api_cache/daily_inventory_movement.parquet', columns=['store', 'channel', 'closing_soh'])
print('DM channels:', dm['channel'].value_counts().to_dict())
for s in ['110', '115', '120', 'P001', 'P105', 'P147']:
    match = dm[dm['store'] == s].drop_duplicates(subset=['store', 'channel'])
    if not match.empty:
        print(f"Store {s}: channel={match['channel'].tolist()}")
    else:
        print(f"Store {s}: NOT FOUND in DM")
