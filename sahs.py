import re

data = """[SOT_BOTv7]: 🐯💬 
NSE:BANKNIFTY2451549300PE khata khata hatha vidhi!"""

match = re.search(r'(NSE:\S+)', data)

if match:
    print(match.group(1))  # Print the content of the first capturing group
else:
    print("Pattern not found.")
