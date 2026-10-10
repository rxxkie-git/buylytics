import requests
from pathlib import Path

file_path = Path("sample_upload.csv")
with open(file_path, "rb") as f:
    files = {"file": f}
    data = {
        "scaling_method": "standard",
        "use_binary_label_encoding": "true"
    }
    response = requests.post(
        "http://localhost:8000/upload-product",
        files=files,
        data=data,
        timeout=300
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        print("✓ Product upload successful!")
        result = response.json()
        print(f"Dataset ID: {result.get('datasetId')}")
    else:
        print(f"✗ Error: {response.text[:2000]}")
