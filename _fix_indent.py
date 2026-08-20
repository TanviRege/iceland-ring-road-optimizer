#!/usr/bin/env python3
"""Fix indentation issues caused by editor tool."""

with open('app.py', 'r') as f:
    lines = f.readlines()

# Debug: show current state of problematic areas
print("=== Before fixes ===")
for i in range(78, 108):
    print(f"{i+1}: {repr(lines[i])}")
for i in range(298, 310):
    print(f"{i+1}: {repr(lines[i])}")
for i in range(324, 340):
    print(f"{i+1}: {repr(lines[i])}")

# Fix 1: Display DataFrames comment (line 261, 1-indexed)
for i, line in enumerate(lines):
    if '# ---- Create display-ready copies' in line:
        lines[i] = '    ' + line.lstrip()
        print(f"Fixed line {i+1}: display DataFrames comment")
        break

# Fix 2: _rate line (currently has 16 spaces, should have 8)
for i, line in enumerate(lines):
    if '_rate = _get_isk_rate()' in line and 'def _get_isk_rate()' not in line:
        lines[i] = '        _rate = _get_isk_rate()\n'
        print(f"Fixed line {i+1}: _rate variable")
        break

# Fix 3: KPI cards - check and fix kpi1.metric through kpi5.metric
# The issue is that the if/else blocks have wrong indentation
for i, line in enumerate(lines):
    if 'kpi1.metric("🛡️ Route Risk"' in line:
        # kpi1.metric should be at 4 spaces
        if line.startswith('        kpi1.metric'):  # 8 spaces
            lines[i] = '    kpi1.metric("🛡️ Route Risk", risk_label, risk_desc)\n'
            print(f"Fixed line {i+1}: kpi1.metric (8→4 spaces)")
        break

# Verify the kpi2/kpi3/kpi4/kpi5 lines are at correct indentation
print("\n=== KPI card context ===")
for i in range(325, 340):
    if i < len(lines):
        print(f"{i+1}: {repr(lines[i])}")

with open('app.py', 'w') as f:
    f.writelines(lines)

print("\nFixes applied!")
