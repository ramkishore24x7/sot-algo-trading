import asyncio
import csv
import pandas as pd
from tabulate import tabulate
from bs4 import BeautifulSoup
from pyppeteer import launch
from datetime import datetime

async def scrape_option_chain(url):
    try:
        # Launch the browser
        browser = await launch()
        # Open a new page
        page = await browser.newPage()
        # Navigate to the webpage
        await page.goto(url, {'waitUntil': 'networkidle2'})
        # Wait for a specific element that ensures the dynamic content is loaded
        await page.waitForSelector('a', {'timeout': 10000})
        # Get the HTML content from the page
        html_content = await page.content()
        # Close the browser
        await browser.close()

        # Parse the HTML content using BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')

        # Extracting values
        values = {}

        # Helper function to safely extract text
        def extract_text(tag):
            return tag.text.strip() if tag else ''

        # PCR
        pcr_tag = soup.find('a', string='PCR:')
        values['PCR'] = extract_text(pcr_tag.find_next('p')) if pcr_tag else ''

        # CHG OI PCR
        chg_oi_pcr_tag = soup.find('span', string='CHG OI PCR:')
        values['CHG OI PCR'] = extract_text(chg_oi_pcr_tag.find_next('p')) if chg_oi_pcr_tag else ''
        
        # Ensure CHG OI PCR can be converted to float
        try:
            chg_oi_pcr_value = float(values['CHG OI PCR'])
            values['Trend'] = 'Bearish' if chg_oi_pcr_value < 0.8 else 'Sideways' if chg_oi_pcr_value <= 1.2 else 'Bullish'
        except ValueError:
            values['Trend'] = 'Unknown'
        
        # Lot Size
        lot_size_tag = soup.find('a', string='Lot Size:')
        values['Lot Size'] = extract_text(lot_size_tag.find_next('p')) if lot_size_tag else ''

        # India VIX
        india_vix_tag = soup.find('li', class_='vix_price')
        values['India VIX'] = extract_text(india_vix_tag.find_next('p')).split()[0] if india_vix_tag else ''

        # Max Pain
        max_pain_tag = soup.find('a', string='Max Pain:')
        values['Max Pain'] = extract_text(max_pain_tag.find_next('p')) if max_pain_tag else ''

        return values
    except Exception as e:
        print(f"Error scraping {url}: {e}")
        return None

def write_to_csv(data, header, filename, rows_list):
    # Get current time
    current_time = datetime.now().strftime("%H:%M:%S")

    # Create a dictionary for the new row
    row = {
        'Timestamp': current_time,
        'Index': header,
        'Trend': data.get('Trend', ''),
        'PCR CHG OI': data.get('CHG OI PCR', ''),
        'PCR': data.get('PCR', ''),
        'Lot Size': data.get('Lot Size', ''),
        'India VIX': data.get('India VIX', ''),
        'Max Pain': data.get('Max Pain', '')
    }

    # Add the row to the list
    rows_list.append(row)

    # Check if file exists
    file_exists = False
    try:
        with open(filename, 'r', newline='') as file:
            file_exists = True
    except FileNotFoundError:
        file_exists = False

    # Write to CSV
    with open(filename, 'a', newline='') as file:
        writer = csv.writer(file)
        if not file_exists:
            # Define the header for the CSV
            csv_header = ['Timestamp', 'Index', 'Trend', 'PCR CHG OI', 'PCR', 'Lot Size', 'India VIX', 'Max Pain']
            # Write header if the file is being created
            writer.writerow(csv_header)
        # Write data
        writer.writerow([row['Timestamp'], row['Index'], row['Trend'], row['PCR CHG OI'], row['PCR'], row['Lot Size'], row['India VIX'], row['Max Pain']])

async def main():
    urls = [
        'https://www.niftytrader.in/nse-option-chain/nifty',
        'https://www.niftytrader.in/nse-option-chain/banknifty',
        'https://www.niftytrader.in/nse-option-chain/finnifty',
        'https://www.niftytrader.in/nse-option-chain/midcpnifty'
    ]

    # Get current date
    current_date = datetime.now().strftime("%d %B %Y")
    filename = f"option_chain_data_{current_date}.csv"

    # List to accumulate rows
    rows_list = []

    # Scrape data from each URL concurrently
    tasks = [scrape_option_chain(url) for url in urls]
    scraped_data = await asyncio.gather(*tasks)

    for url, data in zip(urls, scraped_data):
        if data:
            # Extract header from URL
            header = url.rstrip('/').split('/')[-1]
            # Write data to CSV
            write_to_csv(data, header, filename, rows_list)

    # Create a DataFrame from the accumulated rows and print it
    df = pd.DataFrame(rows_list)
    print(df.to_markdown())

# Run the main function
asyncio.get_event_loop().run_until_complete(main())
