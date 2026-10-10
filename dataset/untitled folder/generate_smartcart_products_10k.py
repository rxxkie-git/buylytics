import csv
import random
import datetime

def generate_products_dataset(filename="gen_smartcart_products_10k.csv", num_rows=10000):
    products = [
        {"id": f"SKU-{i:03d}", "name": f"Product {i}", "price": round(random.uniform(5.0, 150.0), 2)}
        for i in range(1, 101)
    ]
    
    start_date = datetime.date(2025, 1, 1)
    days_between_dates = (datetime.date(2026, 8, 1) - start_date).days
    
    with open(filename, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['Date', 'Product_ID', 'Product_Name', 'Quantity', 'Price', 'Revenue'])
        
        for _ in range(num_rows):
            prod = random.choice(products)
            random_date = start_date + datetime.timedelta(days=random.randrange(days_between_dates))
            
            # Introduce a positive trend over time so the forecast goes up!
            days_from_start = (random_date - start_date).days
            trend_multiplier = 1.0 + (days_from_start / days_between_dates) * 3.0
            
            qty = int(random.randint(1, 15) * trend_multiplier)
            revenue = round(qty * prod["price"], 2)
            
            writer.writerow([
                random_date.strftime('%Y-%m-%d'),
                prod["id"],
                prod["name"],
                qty,
                prod["price"],
                revenue
            ])
            
    print(f"Generated {num_rows} rows in {filename}")

if __name__ == "__main__":
    generate_products_dataset()
