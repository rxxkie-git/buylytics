import csv
import random
import datetime

def generate_data(num_rows=10000, filename='gen_smartcart_customers_10k.csv'):
    education_levels = ['Basic', 'Graduation', 'Master', 'PhD', '2n Cycle']
    marital_statuses = ['Single', 'Married', 'Divorced', 'Widow', 'Together']
    
    start_date = datetime.date(2012, 1, 1)
    end_date = datetime.date(2015, 12, 31)
    time_between_dates = end_date - start_date
    days_between_dates = time_between_dates.days

    with open(filename, mode='w', newline='') as file:
        writer = csv.writer(file)
        
        # Write header
        writer.writerow(['ID', 'Year_Birth', 'Education', 'Marital_Status', 'Income', 'Kidhome', 'Teenhome', 
                         'Dt_Customer', 'Recency', 'MntWines', 'MntFruits', 'MntMeatProducts', 
                         'MntFishProducts', 'MntSweetProducts', 'MntGoldProds', 'NumDealsPurchases', 
                         'NumWebPurchases', 'NumCatalogPurchases', 'NumStorePurchases', 'NumWebVisitsMonth', 
                         'Complain', 'Response', 'Total_Spend', 'Total_Orders', 'Preferred_Channel'])
                         
        for i in range(1, num_rows + 1):
            year_birth = random.randint(1940, 1995)
            education = random.choice(education_levels)
            marital_status = random.choice(marital_statuses)
            income = random.randint(15000, 150000)
            kidhome = random.randint(0, 2)
            teenhome = random.randint(0, 2)
            
            random_number_of_days = random.randrange(days_between_dates)
            random_date = start_date + datetime.timedelta(days=random_number_of_days)
            dt_customer = random_date.strftime('%d-%m-%Y')
            
            recency = random.randint(0, 99)
            
            # Generate number of purchases (distinct and low to ensure high avg value per transaction)
            num_web_purchases = random.randint(1, 5)
            num_catalog_purchases = random.randint(1, 5)
            num_store_purchases = random.randint(1, 5)
            num_deals_purchases = random.randint(0, 3)
            
            total_purchases = num_web_purchases + num_catalog_purchases + num_store_purchases
            
            channels = {
                "Web": num_web_purchases,
                "Catalog": num_catalog_purchases,
                "In-Store": num_store_purchases,
            }
            preferred_channel = max(channels, key=channels.get)
            
            # Target total spend to be at least total_purchases * 230
            target_min_spend = total_purchases * 230
            
            # Generate amounts (ensure they sum up to at least target_min_spend)
            # Add high variance
            mnt_wines = random.randint(10, 1500)
            mnt_fruits = random.randint(0, 200)
            mnt_meat = random.randint(10, 1500)
            mnt_fish = random.randint(0, 300)
            mnt_sweet = random.randint(0, 200)
            mnt_gold = random.randint(0, 400)
            
            total_spend = mnt_wines + mnt_fruits + mnt_meat + mnt_fish + mnt_sweet + mnt_gold
            
            if total_spend < target_min_spend:
                deficit = target_min_spend - total_spend
                mnt_wines += deficit // 2 + random.randint(10, 100)
                mnt_meat += deficit // 2 + random.randint(10, 100)
                total_spend = mnt_wines + mnt_fruits + mnt_meat + mnt_fish + mnt_sweet + mnt_gold
            
            num_web_visits_month = random.randint(0, 20)
            complain = random.choices([0, 1], weights=[0.95, 0.05])[0]
            response = random.choices([0, 1], weights=[0.85, 0.15])[0]
            
            writer.writerow([
                i, year_birth, education, marital_status, income, kidhome, teenhome,
                dt_customer, recency, mnt_wines, mnt_fruits, mnt_meat,
                mnt_fish, mnt_sweet, mnt_gold, num_deals_purchases,
                num_web_purchases, num_catalog_purchases, num_store_purchases,
                num_web_visits_month, complain, response, total_spend, total_purchases, preferred_channel
            ])

    print(f"Generated {num_rows} rows in {filename}")

if __name__ == '__main__':
    generate_data()
