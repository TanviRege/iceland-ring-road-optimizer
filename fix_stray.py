# Remove stray comment line
with open('/Users/tanvirege/projects/iceland-ring-road-optimizer/app.py', 'r') as f:
    lines = f.readlines()

new_lines = [line for line in lines if line.strip() != '# Road status → numeric']

with open('/Users/tanvirege/projects/iceland-ring-road-optimizer/app.py', 'w') as f:
    f.writelines(new_lines)
print('Fixed stray comment')