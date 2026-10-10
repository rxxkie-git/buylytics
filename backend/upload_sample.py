import requests
from pathlib import Path

file_path = Path("sample_upload.csv")
with open(file_path, "rb") as f:
    files = {"file": f}
    response = requests.post("http://localhost:8000/upload", files=files, timeout=180)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        print("✓ Upload successful!")
        data = response.json()
        print(f"Dataset ID: {data.get('datasetId')}")
    else:
        print(f"✗ Error: {response.text[:1000]}")
