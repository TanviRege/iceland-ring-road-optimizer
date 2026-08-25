# Remove ETA Timeline code from app.py
with open('/Users/tanvirege/projects/iceland-ring-road-optimizer/app.py', 'r') as f:
    lines = f.readlines()

# Find the start and end of the ETA Timeline section
start_idx = None
end_idx = None

for i, line in enumerate(lines):
    if 'NEW: ETA Timeline - Visual route with arrival times at each station' in line:
        start_idx = i - 1  # Include the separator line before
        break

for i, line in enumerate(lines):
    if start_idx is not None and i > start_idx:
        if 'NEW: Weather Alerts Banner - Translated forecast weather types' in line:
            end_idx = i - 1  # Up to the line before Weather Alerts
            break

if start_idx is not None and end_idx is not None:
    # Remove lines from start_idx to end_idx (inclusive)
    new_lines = lines[:start_idx] + lines[end_idx:]
    with open('/Users/tanvirege/projects/iceland-ring-road-optimizer/app.py', 'w') as f:
        f.writelines(new_lines)
    print(f'Removed lines {start_idx+1} to {end_idx+1} (0-indexed: {start_idx} to {end_idx})')
    print(f'New file length: {len(new_lines)} lines')
else:
    print(f'Could not find boundaries. start_idx={start_idx}, end_idx={end_idx}')