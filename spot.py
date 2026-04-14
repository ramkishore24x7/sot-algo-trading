import re

def grep_spot(message):
    spot_regex_pattern = r'spot\s+(\d+)'
    match = re.search(spot_regex_pattern, message, flags=re.IGNORECASE)
    
    if match:
        spot = match.group(1)  # Extract the first captured group (the digits after "spot")
        spot_cleaned = re.sub(r'[-+]', '', spot)  # Remove '-' and '+'
        spot_cleaned = re.sub(r'\b(OF|AT)\b', '', spot_cleaned, flags=re.IGNORECASE)  # Remove "OF" and "AT"
        spot_words = re.split(r'\s+', spot_cleaned)  # Split by whitespace into words
        spot_words = [word.strip() for word in spot_words if word.strip()][0]  # Remove empty strings and strip spaces
        
        return spot_words
    else:
        return None

message = """
Banknifty 52200 ce above 360 
Target 380/400/450/530+++
Sl 345 
spot 52380
"""

print(grep_spot(message))