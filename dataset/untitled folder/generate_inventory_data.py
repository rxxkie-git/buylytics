import csv
import random

# Define categories, items, suppliers, and price ranges
data_definition = {
    "Electronics": {
        "items": [
            "Wireless Mouse", "Mechanical Keyboard", "USB-C Cable (1m)", "Bluetooth Speaker",
            "Noise-Canceling Headphones", "USB-C Hub (6-in-1)", "1080p Webcam", "Laptop Stand",
            "Cooling Pad", "HDMI Cable (2m)", "Power Bank 20000mAh", "Smart Plug Wi-Fi",
            "LED Strip Lights (5m)", "Wireless Charging Pad", "Bluetooth Earbuds",
            "Dual Monitor Mount", "External SSD 1TB", "Laptop Sleeve 15-inch"
        ],
        "suppliers": ["Logitech", "Corsair", "Anker", "JBL", "Sony", "Belkin", "Razer", "TP-Link", "Samsung", "SanDisk"],
        "price_range": (8.99, 150.00)
    },
    "Kitchenware": {
        "items": [
            "Ceramic Coffee Mug", "Stainless Steel Water Bottle", "Chef Knife 8-inch",
            "Non-Stick Frying Pan", "Digital Kitchen Scale", "Electric Kettle",
            "French Press Coffee Maker", "Food Storage Container Set", "Silicone Cooking Utensils (10-pack)",
            "Knife Sharpener", "Salad Spinner", "Mixing Bowl Set", "Toaster 2-Slice",
            "Citrus Juicer", "Over-the-Sink Colander", "Cast Iron Skillet"
        ],
        "suppliers": ["MugCo", "HydroFlask", "Calphalon", "OXO", "Cuisinart", "Pyrex", "Bodum", "Instant Pot", "Lodge"],
        "price_range": (9.99, 89.99)
    },
    "Furniture": {
        "items": [
            "Ergonomic Office Chair", "Adjustable Standing Desk", "LED Desk Lamp",
            "3-Tier Bookshelf", "Wooden Side Table", "Swivel Bar Stool", "File Cabinet",
            "Memory Foam Footrest", "Monitor Stand Riser", "Floor Cushion",
            "Folding Utility Table", "Mesh Back Task Chair"
        ],
        "suppliers": ["Steelcase", "Fully", "Herman Miller", "IKEA", "Ashley Furniture", "Wayfair", "Serta"],
        "price_range": (15.00, 399.99)
    },
    "Stationery": {
        "items": [
            "Leather Journal", "Gel Pen Set (12-pack)", "Dry Erase Markers (8-pack)",
            "Sticky Notes (6-pack)", "Heavy Duty Stapler", "Desktop Organizer",
            "Graph Paper Notebook", "Precision Scissors", "Weekly Planner",
            "Binder Clips Set", "Desk Pad Blotter", "Fountain Pen"
        ],
        "suppliers": ["Moleskine", "Pilot", "Sharpie", "Post-it", "Swingline", "Mead", "Lamy", "Paper Mate"],
        "price_range": (3.50, 35.00)
    },
    "Apparel": {
        "items": [
            "Cotton Crewneck T-Shirt", "Athletic Running Shorts", "Fleece Hoodie",
            "Cushioned Athletic Socks (6-pack)", "Performance Joggers", "Classic Baseball Cap",
            "Lightweight Windbreaker", "Compression Shirt", "Canvas Sneaker",
            "Knit Beanie", "Puffer Vest", "Leather Belt"
        ],
        "suppliers": ["Nike", "Adidas", "Under Armour", "Gildan", "Champion", "Columbia", "Levi's", "Puma"],
        "price_range": (12.00, 75.00)
    },
    "Fitness & Outdoors": {
        "items": [
            "Eco-Friendly Yoga Mat", "Resistance Band Set (5 levels)", "Foam Roller",
            "Running Waist Pack", "Ultralight Sleeping Bag", "Camping Lantern LED",
            "Trekking Poles", "Stainless Steel Shaker Bottle", "Agility Ladder",
            "Microfiber Travel Towel", "Waterproof Backpack 30L", "Bike Helmet"
        ],
        "suppliers": ["Lululemon", "Coleman", "CamelBak", "Gatorade", "Fitbit", "Garmin", "Gaiam", "Hydro Flask"],
        "price_range": (10.00, 120.00)
    },
    "Beauty & Personal Care": {
        "items": [
            "Moisturizing Lotion", "Hydrating Face Wash", "Tea Tree Shampoo",
            "Aloe Vera Soothing Gel", "Organic Lip Balm (4-pack)", "Electric Toothbrush",
            "Makeup Sponge Set", "Beard Oil", "Hand Cream", "Sunscreen SPF 50",
            "Bath Bombs Set", "Exfoliating Scrub"
        ],
        "suppliers": ["CeraVe", "Neutrogena", "OGX", "Burt's Bees", "Philips", "Nivea", "L'Oreal", "Dove"],
        "price_range": (4.99, 69.99)
    },
    "Home Decor": {
        "items": [
            "Minimalist Wall Clock", "Scented Soy Candle", "Ceramic Flower Vase",
            "LED Digital Alarm Clock", "Throw Pillow Cover Set", "Wooden Photo Frame 8x10",
            "Faux Succulent Trio", "Table Runner", "Essential Oil Diffuser",
            "String Fairy Lights", "Desktop Fountain"
        ],
        "suppliers": ["Yankee Candle", "Target", "Threshold", "West Elm", "Pottery Barn", "Crate & Barrel"],
        "price_range": (8.00, 55.00)
    }
}

def generate_inventory(num_items=500):
    inventory_items = []
    used_skus = set()
    
    categories = list(data_definition.keys())
    
    for i in range(num_items):
        # Determine SKU
        while True:
            sku_num = random.randint(10000, 99999)
            sku = f"SKU-{sku_num}"
            if sku not in used_skus:
                used_skus.add(sku)
                break
        
        # Pick category
        category = random.choice(categories)
        cat_info = data_definition[category]
        
        # Pick base item name and supplier
        base_item = random.choice(cat_info["items"])
        supplier = random.choice(cat_info["suppliers"])
        
        # Create a specific product variant/name
        # e.g., "Anker Wireless Mouse" or "Logitech Wireless Mouse - Pro Edition"
        variant = random.choice(["", "Pro Edition", "Premium", "Ultra", "Classic", "Plus", "Basic"])
        if variant:
            item_name = f"{supplier} {base_item} - {variant}"
        else:
            item_name = f"{supplier} {base_item}"
            
        # Determine Price
        min_p, max_p = cat_info["price_range"]
        price = round(random.uniform(min_p, max_p), 2)
        
        # Determine Quantity and stock status distribution:
        # 10% Out of Stock (0)
        # 20% Low Stock (1 to 10)
        # 70% In Stock (11 to 150)
        qty_rand = random.random()
        if qty_rand < 0.10:
            quantity = 0
        elif qty_rand < 0.30:
            quantity = random.randint(1, 10)
        else:
            quantity = random.randint(11, 150)
            
        inventory_items.append({
            "id": sku,
            "itemName": item_name,
            "category": category,
            "quantity": quantity,
            "price": price,
            "supplier": supplier
        })
        
    return inventory_items

def main():
    items = generate_inventory(500)
    output_file = "dataset/inventory_master_dataset.csv"
    
    # Write to CSV file
    with open(output_file, mode="w", newline="", encoding="utf-8") as f:
        fieldnames = ["id", "itemName", "category", "quantity", "price", "supplier"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            writer.writerow(item)
            
    print(f"Successfully generated {len(items)} inventory records and saved to '{output_file}'.")

if __name__ == "__main__":
    main()
