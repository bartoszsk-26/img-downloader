import pandas as pd
import requests
import os
import re
from io import BytesIO
from PIL import Image
from urllib.parse import urlparse

# ================= CONFIG =================
CSV_FILENAME = "products.csv"
IMAGE_FOLDER = "images"
TIMEOUT = 25
RETRIES = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "image/*,*/*;q=0.8",
}

# ==========================================


def safe_filename(name):
    return re.sub(r'[<>:"/\\|?*\n\r\t]', "_", str(name).strip())


def ensure_folder():
    os.makedirs(IMAGE_FOLDER, exist_ok=True)


def detect_extension(response, image):
    """Detect extension from header or PIL"""
    content_type = response.headers.get("Content-Type", "").lower()

    if "jpeg" in content_type:
        return "jpg"
    if "png" in content_type:
        return "png"
    if "webp" in content_type:
        return "webp"

    # fallback to PIL detection
    if image.format:
        fmt = image.format.lower()
        if fmt in ["jpeg", "jpg"]:
            return "jpg"
        if fmt in ["png", "webp"]:
            return fmt

    return "jpg"


def download_image(url, filename):
    print(f"⬇️ Trying: {url}")

    for attempt in range(RETRIES):

        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT,
                allow_redirects=True,
            )

            response.raise_for_status()

            if not response.content:
                raise Exception("Empty response")

            image = Image.open(BytesIO(response.content))

            ext = detect_extension(response, image)

            if ext == "jpg" and image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGB")

            filepath = f"{filename}.{ext}"
            image.save(filepath, quality=95)

            print(f"✅ SAVED: {filepath}")
            return True

        except Exception as e:
            print(f"Retry {attempt+1}/{RETRIES} failed → {e}")

    print(f"❌ FAILED: {url}")
    return False


# ================= LOAD CSV =================

try:
    df = pd.read_csv(CSV_FILENAME)
except Exception as e:
    print(f"CSV ERROR: {e}")
    exit()

df = df.map(lambda x: str(x).strip().replace("\u200b", "") if isinstance(x, str) else x)

ensure_folder()

# ================= FIND PRODUCT COLUMN =================

product_name_column = None
for col in df.columns:
    if any(k in col.lower() for k in ["name", "product", "title", "item"]):
        product_name_column = col
        break

if product_name_column is None:
    product_name_column = df.columns[0]

print(f"Using product column: {product_name_column}")

# ================= DOWNLOAD LOOP =================

total_downloaded = 0

for index, row in df.iterrows():

    product_name = safe_filename(row[product_name_column])
    image_count = 0

    for col in df.columns:

        if col == product_name_column:
            continue

        url = row[col]

        if pd.isna(url):
            continue

        url = str(url).strip()

        # basic URL check ONLY
        if not url.startswith("http"):
            continue

        filename = os.path.join(
            IMAGE_FOLDER,
            f"{product_name}_{image_count}" if image_count else product_name,
        )

        if download_image(url, filename):
            image_count += 1
            total_downloaded += 1

print("\n==============================")
print(f"TOTAL IMAGES DOWNLOADED: {total_downloaded}")
print("==============================")
