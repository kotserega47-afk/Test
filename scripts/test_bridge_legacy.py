from core.rules_v2.bridge_legacy import build_snapshot_v2_from_legacy
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
rules_path = project_root / "rules.xlsx"
snapshot = build_snapshot_v2_from_legacy(rules_path)

print("meta:", snapshot.meta)
print("jobs:", len(snapshot.jobs))
print("partners:", len(snapshot.partners))
print("groups:", len(snapshot.partner_groups))
print("group_members:", len(snapshot.partner_group_members))
print("methods:", len(snapshot.methods))
print("job_params:", len(snapshot.job_params))
print("limit_rules:", len(snapshot.limit_rules))
print("threshold_rules:", len(snapshot.threshold_rules))
print("exclusion_rules:", len(snapshot.exclusion_rules))

print("\n--- JOBS ---")
print(sorted(snapshot.jobs.keys()))

print("\n--- METHODS ---")
print(sorted(snapshot.methods.keys()))

print("\n--- FIRST 10 PARTNERS ---")
for i, (k, v) in enumerate(sorted(snapshot.partners.items())[:10], start=1):
    print(i, k, "->", v.display_name)

print("\n--- GROUPS ---")
for k, v in sorted(snapshot.partner_groups.items()):
    print(k, "->", v.display_name)

print("\n--- FIRST 15 GROUP MEMBERS ---")
for row in snapshot.partner_group_members[:15]:
    print(row)

print("\n--- FIRST 10 JOB PARAMS ---")
for row in snapshot.job_params[:10]:
    print(row)

print("\n--- FIRST 10 LIMIT RULES ---")
for row in snapshot.limit_rules[:10]:
    print(row)

print("\n--- FIRST 10 THRESHOLD RULES ---")
for row in snapshot.threshold_rules[:10]:
    print(row)

print("\n--- FIRST 10 EXCLUSION RULES ---")
for row in snapshot.exclusion_rules[:10]:
    print(row)