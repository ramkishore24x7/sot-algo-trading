import re

string = "Example string"  # Replace with your input string

pattern = r"(?:take one entry (?:at|above|near)|buy \d{1,2}[-\d{1,2}]? lots?|buy \d{1,2}[-\d{1,2}]? lot (?:at|above|near))"
match = re.search(pattern, string)
if match:
    print("Match found!")
else:
    print("No match found.")