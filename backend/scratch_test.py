import pandas as pd
from api.routes.sales_analyzer import _get_prepared_sales_df, _get_prepared_soh_df, execute_analyzer_query
from pydantic import BaseModel
from typing import Optional

class Value(BaseModel):
    field: str
    label: str
    aggregation: Optional[str] = 'sum'

class Req(BaseModel):
    rows: list[str]
    columns: list[str] = []
    values: list[Value]
    filters: dict = {}
    search: str = ''

req = Req(
    rows=['zone', 'sales_channel', 'site_id_name'],
    values=[Value(field='current_soh', label='SOH'), Value(field='sales', label='Sales')]
)
res = execute_analyzer_query(req)
rows = res['rows']

def print_tree(nodes, depth=0):
    for node in nodes:
        key = node['key']
        if 'MALAD' in str(key) or 'PANTALOONS' in str(key).upper() or 'CENTRO' in str(key).upper():
            pass
        if 'MALAD' in str(key):
            print('  '*depth, key, node['metrics'])
        elif depth < 2:
            pass #print('  '*depth, key, node['metrics'])
        if 'children' in node:
            print_tree(node['children'], depth+1)

print_tree(rows)
print('Grand total SOH:', res['grand_total'].get('current_soh', 0))
