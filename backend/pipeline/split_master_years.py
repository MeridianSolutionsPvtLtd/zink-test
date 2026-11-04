"""Split Sales and Transfer masters into calendar-year folders, then fold only 2026."""
from pipeline.year_stores import split_current_master


def main() -> None:
    for kind in ("sales", "transfer"):
        result = split_current_master(kind)
        print(kind, result.get("ok"), result.get("reason") or "")
        for row in result.get("years") or []:
            print(
                f"  {row['year']}: rows={row['rows']} {row.get('min')}..{row.get('max')}"
            )


if __name__ == "__main__":
    main()
