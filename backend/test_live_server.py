import requests
import json

payload = {
    "rows": ["zone", "sales_channel", "site_id_name"],
    "columns": [],
    "values": [{"field": "sales"}],
    "year": 2027
}

try:
    r = requests.post("http://127.0.0.1:8000/api/sales-analyzer/query", json=payload, timeout=60)
    print("Status:", r.status_code)
    if r.status_code == 200:
        data = r.json()
        print("Grand total:", data.get("grand_total"))
        # Check rows for SS-SHIPRA or SS-MALAD under PT
        found_under_pt = []
        found_under_ssl = []
        for z in data.get("rows", []):
            zone = z.get("value")
            for ch in z.get("children", []):
                channel = ch.get("value")
                for st in ch.get("children", []):
                    sname = st.get("value")
                    if "SHIPRA" in sname or "MALAD" in sname or "115" in sname or "110" in sname:
                        if "Pantaloons" in channel or channel == "PT":
                            found_under_pt.append(f"{zone} | {channel} | {sname}")
                        else:
                            found_under_ssl.append(f"{zone} | {channel} | {sname}")
        print("\nFound under PT (should be EMPTY):")
        for f in found_under_pt:
            print("  *", f)
        print("\nFound under SSL:")
        for f in found_under_ssl:
            print("  *", f)
    else:
        print("Error:", r.text[:300])
except Exception as e:
    print("Request exception:", e)
