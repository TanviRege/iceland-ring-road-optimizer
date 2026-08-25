# Remove duplicate road status code in app.py
with open('/Users/tanvirege/projects/iceland-ring-road-optimizer/app.py', 'r') as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    if 648 <= i <= 657:
        if i == 648:
            new_lines.append(line)
        continue
    new_lines.append(line)

with open('/Users/tanvirege/projects/iceland-ring-road-optimizer/app.py', 'w') as f:
    f.writelines(new_lines)
print('Fixed duplicate')